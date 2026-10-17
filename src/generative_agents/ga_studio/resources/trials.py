"""Prepare isolated Skill inputs and borrowed initial scenes as physical files."""
from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from zipfile import BadZipFile
from sqlalchemy import select

from generative_agents.ga_protocol.packages.definition import _experiment_definition, read_experiment_documents
from generative_agents.ga_protocol.packages.io import PackageError, atomic_write_json, open_package, read_json
from generative_agents.ga_protocol.packages.locking import package_lock
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_protocol.skills.documents import SnapshotSkillRegistry
from generative_agents.ga_protocol.skills.dependencies import referenced_interfaces, skill_dependencies, validate_interface_references
from generative_agents.ga_protocol.skills.metadata import skill_frontmatter
from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_studio.catalog.packages import StudioPackageCatalogService
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.resources.exchange import resource_extra_files
from generative_agents.ga_studio.storage.credentials import HostModelCredentials
from generative_agents.ga_studio.storage.models import StudioSkill


class SkillTrialEnvironmentService:
    """Read choices and copy a scene without changing its package or catalog."""

    def __init__(self, database, var_dir):
        self.catalog = StudioPackageCatalogService(database)
        self.var_dir = Path(var_dir)

    def _archived(self):
        path = self.var_dir / 'studio-presentation.json'
        document = read_json(path) if path.is_file() else {}
        return {key for key, value in (document.get('experiments') or {}).items()
                if isinstance(value, dict) and value.get('archived_at')}

    @contextmanager
    def _location(self, experiment_id):
        with package_lock(self.var_dir / 'packages' / 'experiments' / f'{experiment_id}.identity'):
            row = self.catalog.get('experiment', experiment_id)
            if row is None or experiment_id in self._archived():
                raise SkillTrialError('SKILL_EXPERIMENT_UNAVAILABLE', '所选实验不存在或已归档，请重新选择。', status=404)
            location = Path(row.location)
            with package_lock(location):
                yield location

    @staticmethod
    def _summary(location, experiment_id):
        manifest, index, assembly = read_experiment_documents(location)
        if manifest.experiment.experiment_id != experiment_id:
            raise PackageError('experiment identity disagrees with its catalog')
        records = {item.identity: item for item in index.resources}
        if assembly.map.identity not in records:
            raise PackageError('experiment map is missing')
        agents = []
        for placement in assembly.placements:
            record = records.get(placement.agent.identity)
            if record and record.definition.get('enabled', True):
                agents.append({'key': record.key, 'agent_key': record.key, 'name': record.name})
        return {'id': experiment_id, 'experiment_id': experiment_id,
                'name': manifest.experiment.name, 'status': 'DRAFT' if location.is_dir() else 'SEALED',
                'agent_count': len(agents)}, agents

    def experiments(self):
        items = []
        for row in self.catalog.list(package_kind='experiment'):
            try:
                with self._location(row.experiment_id) as location:
                    summary, agents = self._summary(location, row.experiment_id)
                if agents:
                    items.append(summary)
            except (ValueError, OSError, BadZipFile):
                continue  # Unavailable packages are not offered as runnable scenes.
        return {'items': items}

    def agents(self, experiment_id):
        try:
            with self._location(experiment_id) as location:
                summary, agents = self._summary(location, experiment_id)
            return {'experiment_id': experiment_id, 'name': summary['name'], 'items': agents}
        except SkillTrialError:
            raise
        except (ValueError, OSError, BadZipFile) as exc:
            raise SkillTrialError('SKILL_EXPERIMENT_UNAVAILABLE', f'所选实验无法读取：{exc}') from exc

    def models(self, experiment_id):
        """Borrow package model settings without requiring a simulation actor."""
        try:
            with self._location(experiment_id) as location, open_package(location) as source:
                manifest = validate_experiment_directory(source)
                if manifest.experiment.experiment_id != experiment_id:
                    raise PackageError('experiment identity disagrees with its catalog')
                return _experiment_definition(source)[1]['models']
        except SkillTrialError:
            raise
        except (ValueError, OSError, BadZipFile) as exc:
            raise SkillTrialError('SKILL_EXPERIMENT_UNAVAILABLE', f'所选实验无法读取：{exc}') from exc

    def copy(self, experiment_id, agent_key, destination):
        if not agent_key:
            raise SkillTrialError('SKILL_AGENT_REQUIRED', '请选择实验中的 Agent 后试运行。')
        try:
            with self._location(experiment_id) as location, open_package(location) as source:
                manifest = validate_experiment_directory(source)
                if manifest.experiment.experiment_id != experiment_id:
                    raise PackageError('experiment identity disagrees with its catalog')
                definition = _experiment_definition(source)[1]
                agent = next((item for item in definition['agents'] if item['agent_key'] == agent_key), None)
                if agent is None or not agent.get('enabled', True):
                    raise SkillTrialError('SKILL_AGENT_UNAVAILABLE', '所选 Agent 不属于该实验或已停用，请重新选择。')
                target = Path(destination) / 'experiment'
                shutil.copytree(source, target)
            return definition
        except SkillTrialError:
            raise
        except (ValueError, OSError, BadZipFile) as exc:
            raise SkillTrialError('SKILL_EXPERIMENT_UNAVAILABLE', f'无法准备实验初始场景：{exc}') from exc


def trial_model(database, var_dir, *, model_preset_id=None, config=None, purpose='chat'):
    """Use the selected or first available model for this purpose, in UI list order."""
    if purpose not in {'chat', 'decision'}:
        raise ValueError(f'Unsupported Skill trial model purpose: {purpose}')
    label = '决策模型' if purpose == 'decision' else '生成式模型'
    if model_preset_id or config is None:
        presets = StudioResourceService(database).list_model_presets()
        if model_preset_id:
            preset = next((item for item in presets if item['id'] == model_preset_id), None)
            if not preset or not preset['config'].get(purpose):
                raise SkillTrialError('SKILL_MODEL_UNAVAILABLE', f'所选{label}已删除、类型不符或不可用，请重新选择。')
        else:
            preset = next((item for item in presets if _usable_model(item['config'].get(purpose))), None)
        config = (preset or {}).get('config', {}).get(purpose, {})
    config = dict(config or {})
    model = config.get('resolved_model') or config.get('model')
    if not _usable_model(config):
        raise SkillTrialError('SKILL_MODEL_CONFIG_MISSING',
            f'Skill 试运行缺少明确的{label}连接。请在模型配置中添加{label}，填写服务地址和模型 ID。')
    credential = config.get('credential_env')
    try:
        api_key = HostModelCredentials(database, var_dir).resolve(credential) if credential else ''
    except ValueError as exc:
        raise SkillTrialError('SKILL_MODEL_CREDENTIAL_MISSING', str(exc)) from exc
    if credential and not api_key:
        raise SkillTrialError('SKILL_MODEL_CREDENTIAL_MISSING', f'模型凭据未配置：请设置环境变量 {credential}。')
    return config, model, {'GA_SKILL_TRIAL_KEY': api_key}


def _usable_model(config):
    return bool(config and config.get('base_url') and
                (config.get('resolved_model') or config.get('model')) not in {None, '', 'auto'})


def prepare_skill_trial(database, registry, name, input_text, *, destination,
                        model_preset_id=None, experiment_id=None, agent_key=None):
    registry.get(name)
    _validate_trial_dependencies(registry.dependencies(name))
    snapshots = registry.snapshot([name])
    model_types = {key: skill_frontmatter(snapshot['markdown'])[0].get('llm_type', 'generative')
                   for key, snapshot in snapshots.items()}
    purpose = 'decision' if model_types[name] == 'decision' else 'chat'
    with database.session_factory() as session:
        for row in session.scalars(select(StudioSkill).where(StudioSkill.skill_key.in_(snapshots))):
            snapshot = snapshots[row.skill_key]
            if row.content_hash != snapshot['content_hash']:
                raise SkillTrialError('SKILL_CONTENT_CHANGED', '技能内容刚刚发生变化，请重新试运行。')
            snapshot['files'] = resource_extra_files(session, 'skill', row.id)
    var_dir = registry.cache_root.parent
    definition, has_scene = None, False
    if experiment_id:
        environment = SkillTrialEnvironmentService(database, var_dir)
        if purpose == 'decision' and not agent_key:
            definition = {'models': environment.models(experiment_id)}
        else:
            definition = environment.copy(experiment_id, agent_key, destination)
            has_scene = True
    elif agent_key:
        raise SkillTrialError('SKILL_EXPERIMENT_REQUIRED', '请选择 Agent 所属的实验。')
    config, model, credentials = trial_model(database, var_dir, model_preset_id=model_preset_id,
        config=(definition['models'].get(purpose) or {}) if definition else None, purpose=purpose)
    decision_config = None
    if purpose == 'chat' and 'decision' in model_types.values():
        decision_config, decision_model, decision_credentials = trial_model(database, var_dir,
            config=(definition['models'].get('decision') or {}) if definition else None, purpose='decision')
        decision_config = {**decision_config, 'model': decision_model}
        credentials['GA_SKILL_TRIAL_DECISION_KEY'] = decision_credentials['GA_SKILL_TRIAL_KEY']
    prepare_copied_trial(snapshots, name, input_text, destination=destination,
        config=config, base_url=config['base_url'], model=model,
        decision_config=decision_config,
        experiment_directory='experiment' if has_scene else None, agent_key=agent_key)
    return credentials


def prepare_copied_trial(snapshots, name, input_text, *, destination, config, base_url, model,
                         experiment_directory=None, agent_key=None, decision_config=None):
    root = Path(destination)
    copied = SnapshotSkillRegistry(snapshots, root=root / 'skills')
    documents = [copied.get(key) for key in snapshots]
    by_name = {document.name: document.detail() for document in documents}
    _validate_trial_dependencies(skill_dependencies(name, by_name.get))
    try:
        for document in documents:
            validate_interface_references(document.body)
    except ValueError as exc:
        raise SkillTrialError('SKILL_INTERFACE_UNKNOWN', str(exc)) from exc
    required = sorted({interface for document in documents for interface in referenced_interfaces(document.body)})
    if required and not (experiment_directory and agent_key):
        raise SkillTrialError('SKILL_ITERATION_CONTEXT_REQUIRED',
            f'该 Skill 调用链需要实验环境接口：{", ".join(required)}。请选择实验及其中的 Agent 后试运行，系统会自动获取地图、身份和初始情境。')
    settings = {key: value for key, value in config.items() if key not in {'api_key', 'secret_ref'}}
    document = {'skill_key': name, 'input': input_text, 'config': settings, 'base_url': base_url,
                'model': model, 'credential_env': 'GA_SKILL_TRIAL_KEY'}
    if decision_config is not None:
        document['decision_config'] = {key: value for key, value in decision_config.items()
                                       if key not in {'api_key', 'secret_ref'}}
        document['decision_credential_env'] = 'GA_SKILL_TRIAL_DECISION_KEY'
    if experiment_directory:
        document.update(experiment_directory=experiment_directory, agent_key=agent_key)
    atomic_write_json(root / 'trial.json', document)
    return root


def _validate_trial_dependencies(dependencies):
    diagnostics = dependencies['diagnostics']
    if diagnostics:
        first = diagnostics[0]
        raise SkillTrialError(first['code'], first['message'])
