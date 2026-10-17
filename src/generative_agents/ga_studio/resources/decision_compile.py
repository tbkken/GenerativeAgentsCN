"""Prepare author-side model settings for the Runtime decision compiler."""
from __future__ import annotations

from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_studio.resources.trials import trial_model


def decision_compile_scripts(saved_scripts, scripts=None):
    """Use the current editor draft; omission keeps the saved attachment boundary."""
    if scripts is None:
        return tuple(saved_scripts)
    if not isinstance(scripts, dict) or any(not isinstance(key, str) or not isinstance(source, str)
                                          for key, source in scripts.items()):
        raise SkillTrialError("SKILL_DECISION_COMPILE_INVALID", "scripts 必须是脚本路径到文本内容的对象。")
    return tuple(scripts)


def prepare_skill_decision_compile(database, registry, name, markdown, *, model_preset_id=None, scripts=None):
    """Resolve a public Skill's identity and chat model without saving its draft."""
    document = registry.get(name)
    config, model, credentials = trial_model(database, registry.cache_root.parent,
                                            model_preset_id=model_preset_id, purpose="chat")
    if document.archived_at:
        raise SkillTrialError("SKILL_UNAVAILABLE", "已归档的 Skill 不能编辑。", status=409)
    return {"markdown": markdown, "skill_key": document.name, "kind": document.kind,
            "scripts": decision_compile_scripts(document.scripts, scripts), "config": {**config, "model": model},
            "api_key": credentials["GA_SKILL_TRIAL_KEY"]}
