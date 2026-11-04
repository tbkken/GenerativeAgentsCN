"""Prepare author-side model settings for the Runtime decision compiler."""
from __future__ import annotations

from generative_agents.ga_protocol.schemas.errors import ServiceError
from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_studio.storage.credentials import HostModelCredentials


def decision_compiler_model(database, root, compiler_config):
    """Resolve the host credential only when an editable Skill is being compiled."""
    if not compiler_config:
        raise SkillTrialError("SKILL_DECISION_COMPILER_NOT_CONFIGURED",
            "请在 services.toml 的 [decision_compiler] 配置决策 Skill 解析模型。")
    config = dict(compiler_config)
    alias = config.pop("credential_env", None)
    api_key = ""
    if alias:
        try:
            api_key = HostModelCredentials(database, root).resolve(alias)
        except (OSError, ValueError, ServiceError) as exc:
            raise SkillTrialError("SKILL_MODEL_CREDENTIAL_MISSING",
                f"决策 Skill 解析模型的凭据不可用。请核对 services.toml 的 [decision_compiler] "
                f"和本机凭据或环境变量 {alias}。") from exc
    return {"config": config, "api_key": api_key}


def decision_compile_scripts(saved_scripts, scripts=None):
    """Use the current editor draft; omission keeps the saved attachment boundary."""
    if scripts is None:
        return tuple(saved_scripts)
    if not isinstance(scripts, dict) or any(not isinstance(key, str) or not isinstance(source, str)
                                          for key, source in scripts.items()):
        raise SkillTrialError("SKILL_DECISION_COMPILE_INVALID", "scripts 必须是脚本路径到文本内容的对象。")
    return tuple(scripts)


def prepare_skill_decision_compile(database, registry, name, markdown, *, compiler_config, scripts=None):
    """Resolve a public Skill's identity and chat model without saving its draft."""
    document = registry.get(name)
    if document.archived_at:
        raise SkillTrialError("SKILL_UNAVAILABLE", "已归档的 Skill 不能编辑。", status=409)
    return {"markdown": markdown, "skill_key": document.name, "kind": document.kind,
            "scripts": decision_compile_scripts(document.scripts, scripts),
            **decision_compiler_model(database, registry.cache_root.parent, compiler_config)}
