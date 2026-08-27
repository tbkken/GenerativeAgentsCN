"""所有回放生产方共同使用并统一校验的 Replay Bundle V2 协议。"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping
from urllib.parse import quote
from uuid import UUID

from pydantic import Field

from generative_agents.config import ExperimentDefinition
from generative_agents.config.schema import StrictModel

from .results import StepResult
from .artifact_contract import REPLAY_GENERATOR_VERSION


GENERATOR_VERSION = REPLAY_GENERATOR_VERSION
_VILLAGE_URL = "/generative_agents/frontend/static/assets/village"
_VILLAGE_ROOT = (
    Path(__file__).resolve().parents[1] / "frontend" / "static" / "assets" / "village"
)
_NORMALIZED_TILEMAP_URL = "/static/console/replay-assets/tilemap.json"
_NORMALIZED_TILEMAP_SHA256 = (
    "53477dc3e5eed02798967fbe032774bf73abe96316a7aeb93397b932e1d3259b"
)
_LEGACY_TILEMAP_SHA256 = (
    "8c15aa6f46ebaf43aec6cf3244860e8161e9d8f7541d1765f907a496686a9bfc"
)
_NORMALIZED_INTERIORS_PT3_URL = "/static/console/replay-assets/interiors_pt3.png"
_NORMALIZED_INTERIORS_PT3_SHA256 = (
    "2d7eab019f428df91dfe8a5861575b7fe15196c1832f2921872de0cd7cc17952"
)


class ReplayAssetStatus(StrEnum):
    """回放描述中资源解析结果的稳定协议值。"""

    READY = "READY"  # 资源已解析并具备渲染所需元数据。
    MISSING = "MISSING"  # 资源无法解析，调用方应展示降级结果。


class ReplayAssetSource(StrEnum):
    """回放渲染资源的来源类型。"""

    BUILTIN_PACKAGE = "BUILTIN_PACKAGE"  # 项目内置的静态资源包。
    REVISION_ASSETS = "REVISION_ASSETS"  # 当前修订版本声明的资源集合。
    WORLD_GRID = "WORLD_GRID"  # 根据结构化网格世界动态生成。
    REVISION_DATABASE = "REVISION_DATABASE"  # 修订版本数据库中存储的资源。
    REVISION_ASSET = "REVISION_ASSET"  # 修订版本引用的单个资源。
    NONE = "NONE"  # 没有可用资源来源。


class ReplayAgentDefinition(StrictModel):
    """回放清单中稳定的智能体身份、名称和视觉资源。"""

    agent_key: str
    display_name: str
    initial_coord: tuple[int, int]
    sprite_asset: dict[str, Any]
    role: str | None = None
    actor_key: str | None = None
    active_tool_instance_key: str | None = None


class ReplayAgentStep(StrictModel):
    """某一步中一个智能体的位置、路径和动作显示信息。"""

    agent_key: str
    from_coord: tuple[int, int]
    coord: tuple[int, int]
    path: list[tuple[int, int]]
    path_source: str
    action: dict[str, Any]
    address: list[str]
    currently: str | None = None
    schedule_item_id: str | None = None
    decision_context: dict[str, Any] = Field(default_factory=dict)


class ReplayStep(StrictModel):
    """回放时间轴上的一个完整已提交步骤。"""

    step_no: int = Field(ge=1)
    virtual_time: str
    attempt_id: str
    attempt_boundary: bool
    checkpoint: bool
    agents: list[ReplayAgentStep]
    conversations: list[dict[str, Any]]
    memory_deltas: list[dict[str, Any]]
    schedule_revisions: list[dict[str, Any]]
    domain_events: list[dict[str, Any]]
    effects: list[dict[str, Any]] = Field(default_factory=list)


class ReplayBundleV2(StrictModel):
    """包含地图、智能体和步骤窗口的正式回放 V2 数据包。"""

    schema_version: Literal[2] = 2
    generator_version: str
    source_kind: Literal["RUN_FRAMES", "RUN_PROJECTION"]
    run_id: str
    revision_id: str
    definition_hash: str
    world: dict[str, Any]
    source_step: int = Field(ge=0)
    available_step: int = Field(ge=0)
    stride_minutes: int = Field(ge=1)
    execution_mode: Literal["SKILL_BRAIN"] = "SKILL_BRAIN"
    brain_skill: str = "stanford-town-brain"
    step_interval_ms: int | None = Field(default=None, ge=1)
    start_time: str
    agents: list[ReplayAgentDefinition]
    partial: bool
    steps: list[ReplayStep]


def _builtin_sprite_map() -> dict[str, str]:
    """执行`builtin``sprite`地图的内部处理，供当前模块或类复用。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    root = _VILLAGE_ROOT / "agents"
    if not root.is_dir():
        return {}
    directories = sorted(
        (path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name
    )
    return {
        f"resident-{index:03d}": directory.name
        for index, directory in enumerate(directories, start=1)
    }


def _world_descriptor(
    definition: ExperimentDefinition,
) -> dict[str, Any]:
    """执行世界描述信息的内部处理，供当前模块或类复用。

    参数:
        definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。 类型：`ExperimentDefinition`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    world = definition.world.model_dump(mode="json", exclude_none=False)
    if definition.world.world_key == "the-ville":
        world["render_asset"] = {
            "status": ReplayAssetStatus.READY.value,
            "source": ReplayAssetSource.BUILTIN_PACKAGE.value,
            "tilemap_url": _NORMALIZED_TILEMAP_URL,
            "tilemap_asset": {
                "url": _NORMALIZED_TILEMAP_URL,
                "sha256": _NORMALIZED_TILEMAP_SHA256,
                "source_sha256": _LEGACY_TILEMAP_SHA256,
                "normalization": "INTERIORS_PT3_IMAGEHEIGHT_10016",
            },
            "base_url": f"{_VILLAGE_URL}/tilemap",
            "tile_size": 32,
            # The legacy source has a 16 px non-tile footer.  Replay owns a
            # package-local, pixel-identical crop of the complete 313 tile
            # rows so Phaser never receives a malformed tilesheet.
            "texture_overrides": {
                "interiors_pt3": {
                    "url": _NORMALIZED_INTERIORS_PT3_URL,
                    "sha256": _NORMALIZED_INTERIORS_PT3_SHA256,
                    "width": 512,
                    "height": 10016,
                    "normalization": "CROP_BOTTOM_NON_TILE_PIXELS",
                }
            },
        }
    elif _is_complete_grid_world(world.get("definition")):
        world["render_asset"] = _grid_render_asset(world["definition"])
    else:
        world["render_asset"] = {
            "status": ReplayAssetStatus.MISSING.value,
            "source": ReplayAssetSource.REVISION_ASSETS.value,
            "error_code": "WORLD_RENDER_ASSET_UNRESOLVED",
        }
    return world


def _is_complete_grid_world(raw_definition: Any) -> bool:
    """判断是否`complete``grid`世界。

    参数:
        raw_definition: 尚未转换为强类型模型的原始世界定义。 类型：`Any`。

    返回:
        条件成立时返回 `True`，否则返回 `False`。
    """
    if not isinstance(raw_definition, Mapping):
        return False
    size = raw_definition.get("size")
    tiles = raw_definition.get("tiles")
    if (
        not isinstance(size, list)
        or len(size) != 2
        or not all(isinstance(value, int) and value > 0 for value in size)
        or not isinstance(tiles, list)
    ):
        return False
    height, width = size
    coords = {
        tuple(tile.get("coord") or ())
        for tile in tiles
        if isinstance(tile, Mapping)
        and isinstance(tile.get("coord"), list)
        and len(tile["coord"]) == 2
    }
    return len(coords) == height * width


def _grid_render_asset(raw_definition: Mapping[str, Any]) -> dict[str, Any]:
    """执行`grid``render`资源的内部处理，供当前模块或类复用。

    参数:
        raw_definition: 尚未转换为强类型模型的原始世界定义。 类型：`Mapping[str, Any]`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """

    editor = raw_definition.get("editor")
    palette_items = raw_definition.get("palette")
    if not isinstance(palette_items, list) and isinstance(editor, Mapping):
        palette_items = editor.get("palette")
    palette: dict[str, dict[str, Any]] = {}
    for item in palette_items or ():
        if not isinstance(item, Mapping):
            continue
        key = item.get("key") or item.get("id")
        if not key:
            continue
        palette[str(key)] = {
            "color": str(item.get("color") or "#d9e2df"),
            "label": str(item.get("label") or item.get("name") or key),
        }
    palette.setdefault("ground", {"color": "#d9e2df", "label": "Ground"})

    objects: list[dict[str, Any]] = []
    editor_v2 = raw_definition.get("editor_v2")
    if isinstance(editor_v2, Mapping):
        for node in editor_v2.get("hierarchy_nodes") or ():
            if not isinstance(node, Mapping) or node.get("kind") != "GAME_OBJECT":
                continue
            bounds = node.get("bounds") or {}
            extensions = node.get("extensions") or {}
            appearance = dict(extensions.get("appearance") or {})
            if not appearance and any(
                binding.get("skill_name") == "traffic-signal-state"
                for binding in node.get("skill_bindings") or ()
                if isinstance(binding, Mapping)
            ):
                appearance = {"emoji": "🚦"}
            objects.append(
                {
                    "instance_key": str(node.get("id") or "game-object"),
                    "x": float(bounds.get("x", 0)),
                    "y": float(bounds.get("y", 0)),
                    "appearance": appearance,
                    "state": dict(extensions.get("state") or {}),
                }
            )

    spatial_scene = raw_definition.get("spatial_scene")
    spatial_assets = editor.get("spatial_assets") if isinstance(editor, Mapping) else {}
    if isinstance(spatial_scene, Mapping) and isinstance(spatial_assets, Mapping):
        meters_per_tile = max(0.000001, float(spatial_scene.get("meters_per_tile", 1)))
        for placement in spatial_scene.get("placements") or ():
            if not isinstance(placement, Mapping):
                continue
            contract = spatial_assets.get(
                str(placement.get("spatial_asset_revision_id") or "")
            )
            if not isinstance(contract, Mapping):
                continue
            state = dict(contract.get("initial_state") or {})
            state.update(dict(placement.get("state_overrides") or {}))
            objects.append(
                {
                    "instance_key": str(
                        placement.get("instance_key") or "spatial-object"
                    ),
                    "x": float(placement.get("x_m", 0)) / meters_per_tile,
                    "y": float(placement.get("y_m", 0)) / meters_per_tile,
                    "appearance": dict(contract.get("appearance") or {}),
                    "state": state,
                }
            )

    tile_size = int(raw_definition.get("tile_size") or 16)
    return {
        "status": ReplayAssetStatus.READY.value,
        "source": ReplayAssetSource.WORLD_GRID.value,
        "renderer": "SPATIAL_GRID",
        "pixels_per_meter": max(8, min(tile_size, 64)),
        "palette": palette,
        "objects": objects,
    }


def _agents(
    definition: ExperimentDefinition,
) -> list[dict[str, Any]]:
    """执行智能体集合的内部处理，供当前模块或类复用。

    参数:
        definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。 类型：`ExperimentDefinition`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    builtin = _builtin_sprite_map() if definition.world.world_key == "the-ville" else {}
    output = []
    for agent in definition.agents:
        if not agent.enabled:
            continue
        directory = builtin.get(agent.agent_key)
        if agent.sprite_asset and agent.sprite_asset.startswith(
            "/api/v1/agent-images/"
        ):
            sprite = {
                "status": ReplayAssetStatus.READY.value,
                "source": ReplayAssetSource.REVISION_DATABASE.value,
                "texture_url": agent.sprite_asset,
                "atlas_url": "/static/console/replay-assets/agent-sprite-4x4.json",
            }
        elif directory:
            sprite = {
                "status": ReplayAssetStatus.READY.value,
                "source": ReplayAssetSource.BUILTIN_PACKAGE.value,
                "texture_url": f"{_VILLAGE_URL}/agents/{quote(directory)}/texture.png",
                "atlas_url": f"{_VILLAGE_URL}/agents/sprite.json",
            }
        elif agent.sprite_asset:
            sprite = {
                "status": ReplayAssetStatus.MISSING.value,
                "source": ReplayAssetSource.REVISION_ASSET.value,
                "logical_reference": agent.sprite_asset,
                "error_code": "AGENT_SPRITE_ASSET_UNRESOLVED",
            }
        else:
            sprite = {
                "status": ReplayAssetStatus.MISSING.value,
                "source": ReplayAssetSource.NONE.value,
                "error_code": "AGENT_SPRITE_MAPPING_MISSING",
            }
        tags = {str(tag).casefold() for tag in agent.tags}
        role = (
            "PEDESTRIAN"
            if any("pedestrian" in tag or "行人" in tag for tag in tags)
            else None
        )
        output.append(
            {
                "agent_key": agent.agent_key,
                "display_name": agent.name,
                "initial_coord": agent.coord,
                "sprite_asset": sprite,
                "role": role,
            }
        )
    return output


def _step_document(
    result: StepResult,
    *,
    checkpoint: bool,
    attempt_boundary: bool,
) -> dict[str, Any]:
    """执行仿真步`document`的内部处理，供当前模块或类复用。

    参数:
        result: 当前仿真步或上游组件产生的结构化结果。 类型：`StepResult`。
        checkpoint: 当前运行已验证的检查点记录或快照。 类型：`bool`。
        attempt_boundary: 当前步骤是否是一次执行尝试开始后的首个提交边界。 类型：`bool`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    wire = result.to_dict()
    return {
        "step_no": result.step_no,
        "virtual_time": result.virtual_time.isoformat(),
        "attempt_id": str(result.attempt_id),
        "attempt_boundary": attempt_boundary,
        "checkpoint": checkpoint,
        "agents": [
            {
                "agent_key": agent.agent_key,
                "from_coord": agent.from_coord,
                "coord": agent.to_coord,
                "path": list(agent.path),
                "path_source": agent.path_source,
                "action": {
                    "description": agent.action.description,
                    "emoji": agent.action.emoji,
                    "object_description": agent.action.object_description,
                },
                "address": list(agent.location),
                "currently": agent.currently,
                "schedule_item_id": agent.schedule_item_id,
                "decision_context": dict(agent.decision_context),
            }
            for agent in result.agents
        ],
        "conversations": wire["conversations"],
        "memory_deltas": wire["memory_deltas"],
        "schedule_revisions": [
            {
                "revision_id": item["revision_id"],
                "sequence": item["sequence"],
                "agent_key": item["agent_key"],
                "reason": item["reason"],
                "source_event_id": item.get("source_event_id"),
                "content_hash": item["content_hash"],
                "item_count": len(item.get("schedule", [])),
                # A replay is an inspector DTO, not a storage dump.  Keep a
                # bounded schedule preview and exclude vector/secret-shaped
                # fields while preserving the committed order.
                "schedule": [
                    {
                        str(key): value
                        for key, value in schedule_item.items()
                        if not any(
                            token in str(key).casefold()
                            for token in (
                                "embedding",
                                "vector",
                                "secret",
                                "token",
                                "api_key",
                            )
                        )
                    }
                    for schedule_item in item.get("schedule", [])[:50]
                    if isinstance(schedule_item, Mapping)
                ],
                "truncated": len(item.get("schedule", [])) > 50,
            }
            for item in wire["schedule_revisions"]
        ],
        "domain_events": wire["domain_events"],
        "effects": wire["effects"],
    }


def build_replay_v2(
    *,
    run_id: str,
    revision_id: str,
    definition_hash: str,
    definition: ExperimentDefinition,
    source_step: int,
    partial: bool,
    results: Iterable[StepResult],
    checkpoint_steps: Iterable[int] = (),
    source_kind: Literal["RUN_FRAMES", "RUN_PROJECTION"] = "RUN_FRAMES",
    generator_version: str = GENERATOR_VERSION,
    previous_attempt_id: str | UUID | None = None,
) -> dict[str, Any]:
    """构建`replay``v2`。

    参数:
        run_id: 仿真运行的唯一标识。 类型：`str`。
        revision_id: 实验修订版本的唯一标识。 类型：`str`。
        definition_hash: 已发布仿真定义规范化后的 SHA-256，用于验证运行输入未漂移。 类型：`str`。
        definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。 类型：`ExperimentDefinition`。
        source_step: 生成产物、回放或恢复时采用的源仿真步编号。 类型：`int`。
        partial: 结果是否只覆盖当前已提交边界而尚未达到请求的最终步骤。 类型：`bool`。
        results: 传入当前算法的`results`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`Iterable[StepResult]`。
        checkpoint_steps: 回放范围内存在有效检查点的步骤编号集合。 类型：`Iterable[int]`。
        source_kind: 回放事实来源。允许值：`RUN_FRAMES`（帧文件）或 `RUN_PROJECTION`（查询投影）。 类型：`Literal['RUN_FRAMES', 'RUN_PROJECTION']`。 默认值：`'RUN_FRAMES'`。
        generator_version: 产物生成器协议版本，用于幂等判断和兼容性校验。 类型：`str`。 默认值：`GENERATOR_VERSION`。
        previous_attempt_id: 上一次执行尝试标识，用于在回放中标记恢复后的尝试边界。 类型：`str | UUID | None`。 默认值：`None`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    checkpoints = set(checkpoint_steps)
    ordered = sorted(
        (result for result in results if result.step_no <= source_step),
        key=lambda item: item.step_no,
    )
    previous_attempt = previous_attempt_id
    steps = []
    for result in ordered:
        boundary = previous_attempt is None or str(previous_attempt) != str(
            result.attempt_id
        )
        steps.append(
            _step_document(
                result,
                checkpoint=result.step_no in checkpoints,
                attempt_boundary=boundary,
            )
        )
        previous_attempt = result.attempt_id
    document = ReplayBundleV2.model_validate(
        {
            "schema_version": 2,
            "generator_version": generator_version,
            "source_kind": source_kind,
            "run_id": run_id,
            "revision_id": revision_id,
            "definition_hash": definition_hash,
            "world": _world_descriptor(definition),
            "source_step": source_step,
            "available_step": source_step,
            "stride_minutes": definition.simulation.stride_minutes,
            "execution_mode": "SKILL_BRAIN",
            "brain_skill": definition.engine.brain_skill,
            "step_interval_ms": None,
            "start_time": definition.simulation.start_time.isoformat(),
            "agents": _agents(definition),
            "partial": partial,
            "steps": steps,
        }
    )
    return document.model_dump(mode="json", exclude_none=False)


def validate_replay_v2(document: Mapping[str, Any]) -> dict[str, Any]:
    """校验`replay``v2`。

    参数:
        document: 待校验、转换或持久化的结构化文档。 类型：`Mapping[str, Any]`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    return ReplayBundleV2.model_validate(document).model_dump(
        mode="json", exclude_none=False
    )
