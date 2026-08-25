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
        """执行 `ValidationReport` 的`valid`操作。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        return not self.errors


def _is_loopback(url: str) -> bool:
    """判断是否`loopback`。

    参数:
        url: 需要校验、规范化或访问的资源地址。 类型：`str`。

    返回:
        条件成立时返回 `True`，否则返回 `False`。
    """
    host = (urlsplit(url).hostname or "").casefold()
    return host in {"127.0.0.1", "localhost", "::1"}


def _fix_target(path: str) -> tuple[str, str | None]:
    """执行`fix``target`的内部处理，供当前模块或类复用。

    参数:
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`str`。

    返回:
        返回按接口约定组织的结果集合。 没有可用结果时返回 `None`。
    """
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
    """执行`issue`的内部处理，供当前模块或类复用。

    参数:
        code: 稳定错误码、状态码或调用方可识别的协议代码。 类型：`str`。
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`str`。
        message: 待发送、校验、脱敏或写入会话的消息文本或对象。 类型：`str`。
        severity: 配置校验问题的严重级别。 类型：`Literal['ERROR', 'WARNING']`。

    返回:
        返回函数计算得到的结果。
    """
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
    """校验`for``publish`。

    参数:
        definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。 类型：`ExperimentDefinition`。
        existing_secret_refs: 修改前已经存在的密钥引用集合，用于识别新增或失效引用。 类型：`set[str] | None`。 默认值：`None`。
        validate_legacy_agent_locations: 是否对旧版智能体地址执行兼容性校验。 类型：`bool`。 默认值：`True`。

    返回:
        返回 `ValidationReport` 类型的处理结果。
    """

    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    get_algorithm_profile(definition.engine.algorithm_version)

    if not any(agent.enabled for agent in definition.agents):
        errors.append(
            _issue("NO_ENABLED_AGENT", "agents", "至少需要一个启用的 Agent", "ERROR")
        )
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
        errors.append(
            _issue("WORLD_EMPTY", "world.definition", "世界定义不能为空", "ERROR")
        )
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
                    f".address.{spatial_issue.purpose}" if spatial_issue.purpose else ""
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
        if (
            secret_ref
            and existing_secret_refs is not None
            and secret_ref not in existing_secret_refs
        ):
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
