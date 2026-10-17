"""Portable Laya question definitions carried by the Skill document."""
from __future__ import annotations

import hashlib
import json
from typing import Any, TYPE_CHECKING

from generative_agents.ga_protocol.skills.metadata import SkillRegistryError

if TYPE_CHECKING:
    from generative_agents.ga_protocol.skills.documents import SkillDocument


def decision_prompt_sha256(body: str) -> str:
    """Bind inferred parameters to the complete natural-language Skill body."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def validate_compiled_decision(document: SkillDocument) -> None:
    """Require fresh inferred parameters at save, package, and execution boundaries."""
    if document.llm_type != "decision":
        return
    if not document.body or not document.decision_questions or not document.decision_prompt_sha256:
        raise SkillRegistryError(f"决策 Skill {document.name} 尚未解析，请在配置页解析 Prompt 后保存。")
    validate_decision_questions(document.decision_questions)
    if document.decision_prompt_sha256 != decision_prompt_sha256(document.body):
        raise SkillRegistryError(f"决策 Skill {document.name} 的 Prompt 已修改，请重新解析后保存。")
    if any(question["instructions"] != document.body for question in document.decision_questions.values()):
        raise SkillRegistryError(f"决策 Skill {document.name} 的问题说明必须是完整 Skill 正文，请重新解析。")


def validate_decision_questions(value: object) -> dict[str, dict[str, Any]]:
    """Validate the documented Decision API question types before execution."""
    if not isinstance(value, dict) or not 1 <= len(value) <= 64:
        raise SkillRegistryError("决策问题必须是包含 1～64 个问题的 JSON 对象。")
    for key, question in value.items():
        if not isinstance(key, str) or not key.strip():
            raise SkillRegistryError("决策问题的键不能为空。")
        if not isinstance(question, dict):
            raise SkillRegistryError(f"决策问题 {key} 必须是对象。")
        kind = question.get("type")
        if kind not in ("noul", "choice", "score"):
            raise SkillRegistryError(f"决策问题 {key} 的类型必须是 noul、choice 或 score。")
        if set(question) - {"type", "instructions", "criteria"}:
            raise SkillRegistryError(f"决策问题 {key} 含不支持的字段。")
        instructions = question.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise SkillRegistryError(f"决策问题 {key} 必须填写问题说明 instructions。")
        criteria = question.get("criteria")
        if kind == "noul":
            if "criteria" in question:
                raise SkillRegistryError(f"是/否问题 {key} 不使用 criteria。")
        elif kind == "choice":
            if not isinstance(criteria, dict) or not 1 <= len(criteria) <= 255:
                raise SkillRegistryError(f"单选问题 {key} 必须包含 1～255 个候选项。")
            if any(not isinstance(label, str) or not label.strip()
                   or not isinstance(description, str) or not description.strip()
                   for label, description in criteria.items()):
                raise SkillRegistryError(f"单选问题 {key} 的选项键和说明必须是非空文本。")
        elif not isinstance(criteria, list) or not 1 <= len(criteria) <= 10 or any(
            not isinstance(label, str) or not label.strip() for label in criteria
        ):
            raise SkillRegistryError(f"评分问题 {key} 必须包含 1～10 个按分值排序的非空等级说明。")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SkillRegistryError(f"决策问题包含重复的键：{key}")
        result[key] = value
    return result


def skill_model_settings(fields: dict[str, str]) -> tuple[str, dict]:
    """Parse model selection and inline JSON without changing source bytes."""
    llm_type = fields.get("llm_type", "generative")
    if llm_type not in ("generative", "decision"):
        raise SkillRegistryError("LLM 类型 llm_type 必须是 generative 或 decision。")
    raw = fields.get("decision_questions", "{}")
    try:
        questions = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SkillRegistryError("decision_questions 必须是单行 JSON 对象。") from exc
    if llm_type == "decision":
        # Drafts must remain editable before the configuration-time model call.
        return llm_type, validate_decision_questions(questions) if questions != {} else {}
    if questions != {} or fields.get("decision_prompt_sha256"):
        raise SkillRegistryError("生成式 Skill 不使用决策参数，请选择决策模型或清除决策参数。")
    return llm_type, {}
