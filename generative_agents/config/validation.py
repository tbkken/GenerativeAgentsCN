"""Publication-level validation that may leave drafts editable."""

from __future__ import annotations

from string import Template
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field

from .algorithm import get_algorithm_profile
from .hashing import definition_hash
from .schema import ExperimentDefinition, REQUIRED_PROMPT_KEYS, StrictModel


class ValidationIssue(StrictModel):
    code: str
    path: str
    message: str
    severity: Literal["ERROR", "WARNING"]


class ValidationReport(StrictModel):
    definition_hash: str
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold()
    return host in {"127.0.0.1", "localhost", "::1"}


def _issue(code: str, path: str, message: str, severity: Literal["ERROR", "WARNING"]):
    return ValidationIssue(code=code, path=path, message=message, severity=severity)


def validate_for_publish(
    definition: ExperimentDefinition,
    *,
    existing_secret_refs: set[str] | None = None,
) -> ValidationReport:
    """Perform deterministic publication checks; network checks live in model adapters."""

    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    get_algorithm_profile(definition.engine.algorithm_version)

    if not any(agent.enabled for agent in definition.agents):
        errors.append(_issue("NO_ENABLED_AGENT", "agents", "至少需要一个启用的 Agent", "ERROR"))

    missing_prompts = sorted(REQUIRED_PROMPT_KEYS - definition.prompts.keys())
    if missing_prompts:
        errors.append(
            _issue(
                "PROMPTS_MISSING",
                "prompts",
                f"缺少必需 Prompt: {', '.join(missing_prompts)}",
                "ERROR",
            )
        )
    for key in sorted(REQUIRED_PROMPT_KEYS & definition.prompts.keys()):
        content = definition.prompts[key].content
        if not content.strip():
            errors.append(_issue("PROMPT_EMPTY", f"prompts.{key}", "Prompt 正文不能为空", "ERROR"))
        try:
            # substitute is intentionally called with an empty mapping to distinguish
            # valid placeholders from malformed '$' syntax. Missing names are valid here.
            Template(content).safe_substitute({})
        except ValueError as exc:
            errors.append(_issue("PROMPT_SYNTAX", f"prompts.{key}", str(exc), "ERROR"))

    world = definition.world.definition
    if not world:
        errors.append(_issue("WORLD_EMPTY", "world.definition", "世界定义不能为空", "ERROR"))

    for purpose, model in (
        ("chat", definition.models.chat),
        ("embedding", definition.models.embedding),
    ):
        if model.model.casefold() == "auto" and not model.resolved_model:
            errors.append(
                _issue(
                    "MODEL_NOT_RESOLVED",
                    f"models.{purpose}.resolved_model",
                    "model=auto 必须先测试连接并固化实际模型",
                    "ERROR",
                )
            )
        base_url = getattr(model, "base_url", None)
        if base_url is not None and not _is_loopback(str(base_url)):
            warnings.append(
                _issue(
                    "MODEL_ENDPOINT_NOT_LOOPBACK",
                    f"models.{purpose}.base_url",
                    "模型服务不是本机地址，请确认数据边界",
                    "WARNING",
                )
            )
        secret_ref = getattr(model, "secret_ref", None)
        if secret_ref and existing_secret_refs is not None and secret_ref not in existing_secret_refs:
            errors.append(
                _issue(
                    "SECRET_NOT_FOUND",
                    f"models.{purpose}.secret_ref",
                    "引用的 Secret 不存在",
                    "ERROR",
                )
            )

    return ValidationReport(
        definition_hash=definition_hash(definition), errors=errors, warnings=warnings
    )
