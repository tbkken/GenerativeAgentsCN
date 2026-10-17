"""Decision trials keep raw inputs and route copied model purposes independently."""
import hashlib
import json

import pytest

from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_runtime.models.decision import DecisionModel
from generative_agents.ga_runtime.skills.trial import execute_skill_trial
from generative_agents.ga_studio.catalog.packages import StudioPackageCatalogService
from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder, SkillSource
from generative_agents.ga_studio.experiments.editor import ExperimentResourceEditor
from generative_agents.ga_studio.experiments.workspace import ExperimentWorkspaceService
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.resources.trials import prepare_copied_trial, prepare_skill_trial
from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import Base
from tests.runtime.test_skill_trial_scene import Answers, tool, trial
from tests.test_portable_package_protocol import _definition


DECISION = {'provider': 'unsloth', 'base_url': 'http://127.0.0.1:5003/v1',
            'model': 'laya-multilingual', 'timeout_seconds': 20, 'retry_attempts': 1,
            'retry_backoff_seconds': 0}
CHAT = {'provider': 'vllm', 'base_url': 'http://127.0.0.1:8888/v1', 'model': 'chat-test'}
BODY = '结合实际输入判断准备情况：输入是否表明准备完成？'
QUESTIONS = {'ready': {'type': 'noul', 'instructions': BODY}}
ANSWER = {'ready': {'type': 'noul', 'noul': 0.9}}


def markdown(key='decide-ready'):
    return ('---\nname: ' + key + '\ndescription: 判断准备情况\nllm_type: decision\n'
            'decision_questions: ' + json.dumps(QUESTIONS, ensure_ascii=False) +
            '\ndecision_prompt_sha256: ' + hashlib.sha256(BODY.encode('utf-8')).hexdigest() +
            '\n---\n' + BODY)


def snapshots(*, mixed=False):
    result = {'decide-ready': {'kind': 'skill', 'markdown': markdown(), 'scripts': {}}}
    if mixed:
        result['test-brain'] = {'kind': 'brain', 'scripts': {}, 'markdown':
            '---\nname: test-brain\ndescription: 判断后解释\n---\n调用 $decide-ready 后解释判断结果。'}
    return result


@pytest.fixture
def studio(tmp_path):
    database = create_database(f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / 'cache')
    skill = registry.create(name='decide-ready', description='判断准备情况')
    registry.save(skill.name, markdown())
    yield database, registry, StudioResourceService(database)
    database.close()


@pytest.fixture
def decision_requests(monkeypatch):
    requests = []
    def respond(self, method, url, **kwargs):
        requests.append({'method': method, 'url': url, **kwargs})
        return {'answers': ANSWER, 'usage': {'input_tokens': 12, 'output_tokens': 8}}
    monkeypatch.setattr(DecisionModel, '_request_json', respond)
    return requests


def no_chat(*args, **kwargs):
    pytest.fail('a decision root must not instantiate a generative model')


def test_decision_trial_uses_raw_input_without_chat_or_authorization(tmp_path, monkeypatch, decision_requests):
    root = prepare_copied_trial(snapshots(), 'decide-ready', '已完成准备。',
        destination=tmp_path / 'trial', config=DECISION, base_url=DECISION['base_url'], model=DECISION['model'])
    monkeypatch.setattr('generative_agents.ga_runtime.skills.trial.create_llm_model', no_chat)
    result = execute_skill_trial(root)
    assert json.loads(result['output_text']) == ANSWER
    assert result['model'] == 'laya-multilingual'
    assert result['input_text'] == '已完成准备。'
    assert result['model_usage'] == {'logical_calls': 1, 'physical_attempts': 1}
    assert 'context_resolution' not in result
    assert not list(root.rglob('context-resolution.json'))
    assert len(decision_requests) == 1
    request = decision_requests[0]
    assert request['url'] == 'http://127.0.0.1:5003/v1/systemone'
    assert request['payload'] == {'model': 'laya-multilingual', 'state': '已完成准备。', 'questions': QUESTIONS}
    assert 'Authorization' not in request['headers']


@pytest.mark.parametrize('change', ['missing_hash', 'changed_body'])
def test_trial_rejects_missing_or_stale_compilation_without_model_calls(tmp_path, monkeypatch, decision_requests, change):
    root = prepare_copied_trial(snapshots(), 'decide-ready', '已完成准备。',
        destination=tmp_path / 'trial', config=DECISION, base_url=DECISION['base_url'], model=DECISION['model'])
    source = SkillRegistry(root / 'skills').get('decide-ready').path
    changed = source.read_text(encoding='utf-8')
    if change == 'missing_hash':
        changed = '\n'.join(line for line in changed.splitlines()
                            if not line.startswith('decision_prompt_sha256:'))
    else:
        changed += '\n只有明确确认完成才算准备完成。'
    source.write_text(changed, encoding='utf-8')
    monkeypatch.setattr('generative_agents.ga_runtime.skills.trial.create_llm_model', no_chat)
    with pytest.raises(SkillTrialError, match='Prompt') as caught:
        execute_skill_trial(root)
    assert caught.value.code == 'SKILL_MODEL_EXECUTION_FAILED'
    assert decision_requests == []


def test_decision_trial_borrows_real_iteration_without_parsed_task_context(tmp_path, monkeypatch, decision_requests):
    root = trial(tmp_path, body='返回描述。')
    prepare_copied_trial(snapshots(), 'decide-ready', '核对当前场景。', destination=root,
        config=DECISION, base_url=DECISION['base_url'], model=DECISION['model'],
        experiment_directory='experiment', agent_key='alice')
    monkeypatch.setattr('generative_agents.ga_runtime.skills.trial.create_llm_model', no_chat)
    result = execute_skill_trial(root)
    state = decision_requests[0]['payload']['state']
    assert state['input_text'] == '核对当前场景。'
    assert state['IterationContext']['agent']['agent_key'] == 'alice'
    assert 'TaskContext' not in state
    assert result['planned_action'] is None and result['world_commit'] == 'NOT_COMMITTED'


def test_public_decision_trial_selects_matching_model_and_rejects_chat_preset(studio, tmp_path):
    database, registry, resources = studio
    chat = resources.create_model_preset(name='生成式', preset_key='chat', config={'chat': CHAT})
    resources.create_model_preset(name='决策', preset_key='decision', config={'decision': DECISION})
    destination = tmp_path / 'trial'
    credentials = prepare_skill_trial(database, registry, 'decide-ready', '准备完成。', destination=destination)
    request = read_json(destination / 'trial.json')
    assert request['model'] == 'laya-multilingual'
    assert request['config']['provider'] == 'unsloth'
    assert credentials == {'GA_SKILL_TRIAL_KEY': ''}
    assert SkillRegistry(destination / 'skills').get('decide-ready').decision_questions == QUESTIONS
    with pytest.raises(SkillTrialError) as caught:
        prepare_skill_trial(database, registry, 'decide-ready', '准备完成。',
            destination=tmp_path / 'wrong-model', model_preset_id=chat['id'])
    assert caught.value.code == 'SKILL_MODEL_UNAVAILABLE'
    assert '决策模型' in str(caught.value)


def test_public_mixed_closure_copies_both_models_and_keeps_keys_outside_files(studio, tmp_path, monkeypatch):
    database, registry, resources = studio
    monkeypatch.setenv('TEST_DECISION_KEY', 'decision-private-key')
    monkeypatch.setenv('TEST_CHAT_KEY', 'chat-private-key')
    resources.create_model_preset(name='生成式', preset_key='chat',
        config={'chat': {**CHAT, 'credential_env': 'TEST_CHAT_KEY'}})
    resources.create_model_preset(name='决策', preset_key='decision',
        config={'decision': {**DECISION, 'credential_env': 'TEST_DECISION_KEY'}})
    parent = registry.create(name='test-brain', description='判断后解释', kind='brain')
    registry.save(parent.name, parent.markdown + '\n调用 $decide-ready。')
    destination = tmp_path / 'trial'
    credentials = prepare_skill_trial(database, registry, 'test-brain', '准备完成。', destination=destination)
    request = read_json(destination / 'trial.json')
    assert request['model'] == 'chat-test'
    assert request['decision_config']['model'] == 'laya-multilingual'
    assert request['decision_credential_env'] == 'GA_SKILL_TRIAL_DECISION_KEY'
    assert credentials == {'GA_SKILL_TRIAL_KEY': 'chat-private-key',
                           'GA_SKILL_TRIAL_DECISION_KEY': 'decision-private-key'}
    for path in destination.rglob('*'):
        if path.is_file():
            assert b'private-key' not in path.read_bytes()


def test_mixed_trial_calls_decision_child_then_returns_to_generative_parent(tmp_path, monkeypatch, decision_requests):
    root = prepare_copied_trial(snapshots(mixed=True), 'test-brain', '准备完成。',
        destination=tmp_path / 'trial', config=CHAT, base_url=CHAT['base_url'], model=CHAT['model'],
        decision_config=DECISION)
    client = Answers(tool('call_skill', {'name': 'decide-ready', 'input_text': '父技能提供的实际状态'}),
                     {'content': '准备完成，可以开始。'})
    monkeypatch.setattr('generative_agents.ga_runtime.skills.trial.create_llm_model', lambda *a, **k: client)
    result = execute_skill_trial(root)
    assert result['output_text'] == '准备完成，可以开始。'
    assert len(client.calls) == 2 and len(decision_requests) == 1
    assert decision_requests[0]['payload']['state'] == '父技能提供的实际状态'
    assert json.loads(client.calls[-1][0][-1]['content']) == ANSWER


@pytest.mark.parametrize('has_decision_model', [True, False])
def test_public_decision_with_experiment_uses_package_model_without_actor(studio, tmp_path, has_decision_model):
    database, registry, resources = studio
    resources.create_model_preset(name='公共决策', preset_key='decision', config={'decision': DECISION})
    definition = _definition()
    if has_decision_model:
        definition['models']['decision'] = {**DECISION, 'model': 'package-model'}
    source = tmp_path / 'author' / 'test-brain' / 'SKILL.md'
    source.parent.mkdir(parents=True)
    source.write_text('---\nname: test-brain\ndescription: 描述\n---\n返回描述。', encoding='utf-8')
    original = ExperimentPackageBuilder().build_directory(tmp_path / 'experiment', definition=definition,
        skills=[SkillSource('test-brain', 'brain', source)], brain_skill='test-brain')
    record = StudioPackageCatalogService(database).upsert(original)
    destination = tmp_path / 'trial'
    if not has_decision_model:
        with pytest.raises(SkillTrialError) as caught:
            prepare_skill_trial(database, registry, 'decide-ready', '准备完成。',
                destination=destination, experiment_id=record.package_id)
        assert caught.value.code == 'SKILL_MODEL_CONFIG_MISSING'
        return
    prepare_skill_trial(database, registry, 'decide-ready', '准备完成。',
        destination=destination, experiment_id=record.package_id)
    request = read_json(destination / 'trial.json')
    assert request['model'] == 'package-model'
    assert 'experiment_directory' not in request
    assert not (destination / 'experiment').exists()


@pytest.mark.parametrize('skill_key', ['decide-ready', 'test-brain'])
def test_experiment_trials_use_embedded_purpose_models(studio, tmp_path, skill_key):
    database, _, resources = studio
    resources.create_model_preset(name='不应使用的公共决策模型', preset_key='public-decision',
        config={'decision': {**DECISION, 'model': 'public-model'}})
    definition = _definition()
    definition['models']['decision'] = {**DECISION, 'model': 'copied-decision'}
    sources = []
    for name, snapshot in snapshots(mixed=True).items():
        source = tmp_path / 'author' / name / 'SKILL.md'
        source.parent.mkdir(parents=True)
        source.write_text(snapshot['markdown'], encoding='utf-8')
        sources.append(SkillSource(name, snapshot['kind'], source))
    original = ExperimentPackageBuilder().build_directory(tmp_path / 'experiment',
        definition=definition, skills=sources, brain_skill='test-brain')
    record = StudioPackageCatalogService(database).upsert(original)
    workspace = ExperimentWorkspaceService(database, package_root=tmp_path / 'packages', var_dir=tmp_path)
    destination = tmp_path / 'trial'
    body = {'input_text': '准备完成。'}
    if skill_key == 'decide-ready':
        body.update(experiment_id=record.package_id, agent_key=None)
    ExperimentResourceEditor(workspace).prepare_trial(record.package_id, skill_key,
        body, destination=destination)
    request = read_json(destination / 'trial.json')
    if skill_key == 'decide-ready':
        assert request['model'] == 'copied-decision'
        assert 'experiment_directory' not in request
    else:
        assert request['model'] == 'local-test'
        assert request['decision_config']['model'] == 'copied-decision'
