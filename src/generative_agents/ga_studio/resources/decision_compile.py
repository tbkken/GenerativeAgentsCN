"""Prepare author-side model settings for the Runtime decision compiler."""
from __future__ import annotations

from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_studio.resources.catalog import StudioResourceService, StudioResourceError
from generative_agents.ga_studio.resources.trials import trial_model
from generative_agents.ga_studio.storage.model_settings import StudioModelSettings


def decision_compiler_model(database, root):
    """Resolve the one explicitly configured author model, never a package fallback."""
    model_id = StudioModelSettings(root).decision_compiler_model_id()
    if not model_id:
        raise SkillTrialError("SKILL_DECISION_COMPILER_NOT_CONFIGURED",
            "尚未配置决策 Skill 解析模型，请在模型中心的生成式模型配置中勾选“用于解析决策 Skill”。")
    try:
        preset = StudioResourceService(database).get_model_preset(model_id)
    except StudioResourceError as exc:
        raise SkillTrialError("SKILL_DECISION_COMPILER_UNAVAILABLE",
            "统一配置的决策 Skill 解析模型已删除、归档或不可用，请在模型中心重新指定生成式模型。") from exc
    config = preset["config"].get("chat")
    if not config:
        raise SkillTrialError("SKILL_DECISION_COMPILER_UNAVAILABLE",
            "统一配置的决策 Skill 解析模型没有生成式模型配置，请在模型中心重新指定。")
    return trial_model(database, root, config=config, purpose="chat")


def decision_compile_scripts(saved_scripts, scripts=None):
    """Use the current editor draft; omission keeps the saved attachment boundary."""
    if scripts is None:
        return tuple(saved_scripts)
    if not isinstance(scripts, dict) or any(not isinstance(key, str) or not isinstance(source, str)
                                          for key, source in scripts.items()):
        raise SkillTrialError("SKILL_DECISION_COMPILE_INVALID", "scripts 必须是脚本路径到文本内容的对象。")
    return tuple(scripts)


def prepare_skill_decision_compile(database, registry, name, markdown, *, scripts=None):
    """Resolve a public Skill's identity and chat model without saving its draft."""
    document = registry.get(name)
    config, model, credentials = decision_compiler_model(database, registry.cache_root.parent)
    if document.archived_at:
        raise SkillTrialError("SKILL_UNAVAILABLE", "已归档的 Skill 不能编辑。", status=409)
    return {"markdown": markdown, "skill_key": document.name, "kind": document.kind,
            "scripts": decision_compile_scripts(document.scripts, scripts), "config": {**config, "model": model},
            "api_key": credentials["GA_SKILL_TRIAL_KEY"]}
