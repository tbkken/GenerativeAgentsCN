"""Execute an isolated file Skill workspace, without Studio services."""
from pathlib import Path
from generative_agents.ga_protocol.packages.io import read_json, atomic_write_json
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_protocol.skills.dependencies import referenced_mcp_tools
from generative_agents.ga_runtime.skills.executor import SkillRuntime, SkillRuntimeError

def execute_skill_trial(directory, *, credentials=None):
    root = Path(directory)
    request = read_json(root / 'trial.json')
    copied = SkillRegistry(root / 'skills')
    name, input_text, context = request['skill_key'], request['input'], request['context']
    config, base_url, model = request['config'], request['base_url'], request['model']
    api_key = (credentials or {}).get(request['credential_env'], '')
    if any(referenced_mcp_tools(doc.body) for doc in copied.list()):
        raise SkillTrialError('SKILL_ITERATION_CONTEXT_REQUIRED', '独立试运行不提供世界或 Agent 私有记忆。')
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
    atomic_write_json(root / 'result.json', result)
    return result
