"""Prepare physical Skill trial inputs without importing an execution engine."""
import os
from pathlib import Path
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.skills.documents import SnapshotSkillRegistry
from generative_agents.ga_protocol.skills.dependencies import referenced_mcp_tools
from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.storage.credentials import HostModelCredentials

def prepare_skill_trial(database, registry, name, input_text, context, *, destination, model_preset_id=None):
    registry.get(name)  # Reject missing/archived roots before resolving the closure.
    snapshots = registry.snapshot([name])
    presets = StudioResourceService(database).list_model_presets()
    preset = next((item for item in presets if item['id'] == model_preset_id), None) if model_preset_id else next((item for item in presets if item['key'] == 'local-default'), None)
    if model_preset_id and (preset is None or not preset['config'].get('chat')):
        raise SkillTrialError('SKILL_MODEL_UNAVAILABLE', '所选聊天模型已删除或不可用，请重新选择。')
    if not model_preset_id and preset is None and len(presets) == 1:
        preset = presets[0]
    config = (preset or {}).get('config', {}).get('chat', {})
    base_url = config.get('base_url') if model_preset_id else os.getenv('GA_SKILL_LLM_BASE_URL') or config.get('base_url')
    model = config.get('resolved_model') or config.get('model') if model_preset_id else os.getenv('GA_SKILL_LLM_MODEL') or config.get('resolved_model') or config.get('model')
    if not base_url or not model or model == 'auto':
        raise SkillTrialError('SKILL_MODEL_CONFIG_MISSING',
            'Skill 试运行缺少明确的模型连接。请在模型中心添加聊天模型，填写服务地址和模型 ID，然后在试运行页选择该模型。')
    credential_env = config.get('credential_env')
    try:
        api_key = HostModelCredentials(database, registry.cache_root.parent).resolve(credential_env) if credential_env else ('' if model_preset_id else os.getenv('GA_SKILL_LLM_API_KEY') or os.getenv('GA_CHAT_API_KEY', ''))
    except ValueError as exc:
        raise SkillTrialError('SKILL_MODEL_CREDENTIAL_MISSING', str(exc)) from exc
    if credential_env and not api_key:
        raise SkillTrialError('SKILL_MODEL_CREDENTIAL_MISSING', f'模型凭据未配置：请设置环境变量 {credential_env}。')
    prepare_copied_trial(snapshots, name, input_text, context, destination=destination,
                         config=config, base_url=base_url, model=model)
    return {'GA_SKILL_TRIAL_KEY': api_key}

def prepare_copied_trial(snapshots, name, input_text, context, *, destination, config, base_url, model):
    root = Path(destination)
    copied = SnapshotSkillRegistry(snapshots, root=root / 'skills')
    required = sorted({tool for key in snapshots for tool in referenced_mcp_tools(copied.get(key).body)})
    if required:
        raise SkillTrialError('SKILL_ITERATION_CONTEXT_REQUIRED',
            f'该 Skill 调用链需要仿真上下文和公共 MCP：{", ".join(required)}。请将其加入显式选择地图和 Agent 的实验后运行；独立试运行不提供世界或 Agent 私有记忆。')
    settings = {key: value for key, value in config.items() if key not in {'api_key', 'secret_ref'}}
    atomic_write_json(root / 'trial.json', {'skill_key': name, 'input': input_text, 'context': context,
        'config': settings, 'base_url': base_url, 'model': model, 'credential_env': 'GA_SKILL_TRIAL_KEY'})
    return root
