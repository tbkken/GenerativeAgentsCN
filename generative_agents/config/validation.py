"""Publication-level validation that may leave drafts editable."""

from __future__ import annotations

from collections import Counter
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field

from .algorithm import get_algorithm_profile
from .hashing import definition_hash
from .schema import ExperimentDefinition, StrictModel
from .spatial import build_map_address_index, validate_agent_spatial


class ValidationIssue(StrictModel):
    code: str
    path: str
    message: str
    severity: Literal["ERROR", "WARNING"]
    fix_page: str | None = None
    fix_control: str | None = None


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


def _fix_target(path: str) -> tuple[str, str | None]:
    if path.startswith("agents"):
        return "agents", "agentSearch"
    if path.startswith("world"):
        return "world", "worldDefinition"
    if path.startswith("models"):
        return "models", "chatModel" if ".chat" in path else "embeddingModel"
    if path.startswith("simulation") or path.startswith("results"):
        return "overview", "maxSteps"
    return "overview", None


def _issue(code: str, path: str, message: str, severity: Literal["ERROR", "WARNING"]):
    fix_page, fix_control = _fix_target(path)
    return ValidationIssue(
        code=code,
        path=path,
        message=message,
        severity=severity,
        fix_page=fix_page,
        fix_control=fix_control,
    )


def validate_for_publish(
    definition: ExperimentDefinition,
    *,
    existing_secret_refs: set[str] | None = None,
    validate_legacy_agent_locations: bool = True,
) -> ValidationReport:
    """Perform deterministic publication checks; network checks live in model adapters."""

    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    get_algorithm_profile(definition.engine.algorithm_version)

    if not any(agent.enabled for agent in definition.agents):
        errors.append(_issue("NO_ENABLED_AGENT", "agents", "至少需要一个启用的 Agent", "ERROR"))
    enabled_agent_names = [agent.name for agent in definition.agents if agent.enabled]
    duplicate_agent_names = sorted(
        name for name, count in Counter(enabled_agent_names).items() if count > 1
    )
    if duplicate_agent_names:
        errors.append(
            _issue(
                "DUPLICATE_ENABLED_AGENT_NAME",
                "agents",
                "启用的 Agent 名称必须唯一: " + ", ".join(duplicate_agent_names),
                "ERROR",
            )
        )


    world = definition.world.definition
    if not world:
        errors.append(_issue("WORLD_EMPTY", "world.definition", "世界定义不能为空", "ERROR"))
    else:
        size = world.get("size") if isinstance(world, dict) else None
        tiles = world.get("tiles") if isinstance(world, dict) else None
        if (
            not isinstance(size, list)
            or len(size) != 2
            or any(not isinstance(value, int) or value < 1 for value in size)
        ):
            errors.append(
                _issue(
                    "WORLD_SIZE_INVALID",
                    "world.definition.size",
                    "世界必须设置有效的高度和宽度",
                    "ERROR",
                )
            )
        if not isinstance(tiles, list) or not tiles:
            errors.append(
                _issue(
                    "WORLD_TILES_MISSING",
                    "world.definition.tiles",
                    "世界必须包含至少一个可访问 Tile",
                    "ERROR",
                )
            )
            accessible: set[tuple[int, int]] = set()
        else:
            accessible = {
                tuple(tile["coord"])
                for tile in tiles
                if isinstance(tile, dict)
                and isinstance(tile.get("coord"), list)
                and len(tile["coord"]) == 2
                and not tile.get("collision", False)
            }
            if not accessible:
                errors.append(
                    _issue(
                        "WORLD_NO_ACCESSIBLE_TILE",
                        "world.definition.tiles",
                        "世界中没有可供 Agent 行走的 Tile",
                        "ERROR",
                    )
                )
        world_roots = {
            definition.world.world_name,
            world.get("world", "") if isinstance(world, dict) else "",
        }
        world_address_index = (
            build_map_address_index(world_roots=world_roots, tiles=tiles)
            if isinstance(tiles, list) and tiles
            else None
        )
        for index, agent in enumerate(definition.agents):
            if not agent.enabled:
                continue
            if not validate_legacy_agent_locations:
                continue
            if tuple(agent.coord) not in accessible:
                errors.append(
                    _issue(
                        "AGENT_INITIAL_POSITION_INVALID",
                        f"agents.{index}.coord",
                        f"Agent“{agent.name}”的初始位置不可访问",
                        "ERROR",
                    )
                )
            spatial_issues = validate_agent_spatial(
                agent.spatial.address,
                agent.spatial.tree,
                world_roots=world_roots,
                world_address_index=world_address_index,
            )
            for spatial_issue in spatial_issues:
                suffix = (
                    f".address.{spatial_issue.purpose}"
                    if spatial_issue.purpose
                    else ""
                )
                errors.append(
                    _issue(
                        spatial_issue.code,
                        f"agents.{index}.spatial{suffix}",
                        f"Agent“{agent.name}”：{spatial_issue.message}",
                        "ERROR",
                    )
                )

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
