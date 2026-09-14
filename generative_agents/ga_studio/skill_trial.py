"""Standalone authoring trials over a physical copy of the selected Skill closure."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from generative_agents.skills.dependencies import referenced_mcp_tools
from generative_agents.skills.registry import SnapshotSkillRegistry
from generative_agents.skills.runtime import SkillRuntime, SkillRuntimeError

from .resources import StudioResourceService
from .model_services import HostModelCredentials


class SkillTrialError(ValueError):
    def __init__(self, code, message, *, status=422, trace=()):
        super().__init__(message)
        self.code, self.status, self.trace = code, status, list(trace)


def run_skill_trial(database, registry, name, input_text, context, *, model_preset_id=None):
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
    return run_copied_skill_trial(snapshots, name, input_text, context, config=config,
                                  base_url=base_url, model=model, api_key=api_key)


def run_copied_skill_trial(snapshots, name, input_text, context, *, config, base_url, model, api_key):
    """Run the same isolated trial using already-copied documents and model config."""
    with TemporaryDirectory(prefix='ga-skill-trial-') as directory:
        copied = SnapshotSkillRegistry(snapshots, root=Path(directory) / 'skills')
        required = sorted({tool for key in snapshots for tool in referenced_mcp_tools(copied.get(key).body)})
        if required:
            raise SkillTrialError('SKILL_ITERATION_CONTEXT_REQUIRED',
                f'该 Skill 调用链需要仿真上下文和公共 MCP：{", ".join(required)}。请将其加入显式选择地图和 Agent 的实验后运行；独立试运行不提供世界或 Agent 私有记忆。')
        runtime = SkillRuntime(copied, base_url=base_url, model=model, api_key=api_key,
            provider=config.get('provider', 'vllm'), timeout=min(int(config.get('timeout_seconds', 90)), 600), max_hops=8,
            retry_attempts=1, retry_backoff_seconds=0,
            temperature=config.get('temperature', 0.2), max_tokens=config.get('max_tokens', 2048),
            enable_thinking=config.get('enable_thinking', False))
        try:
            result = runtime.run(name, input_text, context=context).as_dict()
        except SkillRuntimeError as exc:
            message = str(exc)
            if api_key:
                message = message.replace(api_key, '[REDACTED]')
            if any(marker in message.lower() for marker in ('401', '403', 'not authenticated', 'unauthorized')):
                raise SkillTrialError('SKILL_MODEL_AUTH_FAILED',
                    f'模型 {model} 拒绝了身份认证。请在模型中心填写或更新有效 API Key 后重试。（也支持 GA_SKILL_LLM_API_KEY）',
                    status=502, trace=getattr(exc, 'trace', ())) from exc
            raise SkillTrialError('SKILL_MODEL_EXECUTION_FAILED',
                f'Skill 试运行失败（模型 {model}）：{message}。请检查模型服务地址、模型名称和连接状态。',
                status=502, trace=getattr(exc, 'trace', ())) from exc
    result['model'] = model
    return result
