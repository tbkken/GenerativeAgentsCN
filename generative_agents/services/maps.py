"""可复用公共地图的生命周期与已发布 Revision 选择。"""

from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime, timezone
from math import ceil
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session
from pydantic import ValidationError

from generative_agents.config import ExperimentDefinition, canonical_json_bytes
from generative_agents.config.map_editor import MapEditorDocumentV2
from generative_agents.config.schema import WorldConfig
from generative_agents.config.spatial_assets import (
    SpatialAssetContract,
    SpatialSceneExtension,
)
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    ExperimentRevision,
    SpatialAssetDefinition,
    SpatialAssetRevision,
    WorldMap,
    WorldMapRevision,
)
from generative_agents.skills import (
    DatabaseSkillRegistry,
    SkillRegistryError,
)
from generative_agents.status import RevisionState

from .errors import ServiceError, not_found
from .timestamps import iso_utc


def _utc_now() -> datetime:
    """执行`utc``now`的内部处理，供当前模块或类复用。

    返回:
        返回 `datetime` 类型的处理结果。
    """
    return datetime.now(timezone.utc)


def _make_key(name: str) -> str:
    """执行`make``key`的内部处理，供当前模块或类复用。

    参数:
        name: 目标对象的人类可读名称。 类型：`str`。

    返回:
        返回处理后的文本或稳定标识。
    """
    ascii_key = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return f"{(ascii_key[:48].strip('-') or 'map')}-{uuid4().hex[:8]}"


def normalize_public_world(world: WorldConfig | dict[str, Any]) -> WorldConfig:
    """规范化`public`世界。

    参数:
        world: 当前运行使用的世界配置或运行时世界对象。 类型：`WorldConfig | dict[str, Any]`。

    返回:
        返回 `WorldConfig` 类型的处理结果。
    """
    payload = WorldConfig.model_validate(world).model_dump(
        mode="json", exclude_none=False
    )
    payload.update(
        {
            "map_id": None,
            "map_revision_id": None,
            "map_revision_hash": None,
        }
    )
    return WorldConfig.model_validate(payload)


def world_hash(world: WorldConfig | dict[str, Any]) -> str:
    """执行 的世界哈希值操作。

    参数:
        world: 当前运行使用的世界配置或运行时世界对象。 类型：`WorldConfig | dict[str, Any]`。

    返回:
        返回处理后的文本或稳定标识。
    """
    normalized = normalize_public_world(world)
    return hashlib.sha256(
        canonical_json_bytes(normalized.model_dump(mode="json", exclude_none=False))
    ).hexdigest()


def _compile_editor_v2_runtime_addresses(world: WorldConfig) -> WorldConfig:
    """把编辑器层级结构编译成仿真地图使用的运行时地址。

    参数:
        world: 当前运行使用的世界配置或运行时世界对象。 类型：`WorldConfig`。

    返回:
        返回 `WorldConfig` 类型的处理结果。

    说明:
        编译过程同时建立地址、标题、父子关系和坐标索引；任何一项失败都应拒绝整张地图，避免把部分可用的空间树交给运行时。
    """

    raw_document = world.definition.get("editor_v2")
    if raw_document is None:
        return world
    document = MapEditorDocumentV2.model_validate(raw_document)
    node_by_id = {node.id: node for node in document.hierarchy_nodes}
    root = node_by_id[document.root_node_id]
    children_by_parent: dict[str, list[Any]] = {}
    for node in document.hierarchy_nodes:
        if node.parent_id is not None:
            children_by_parent.setdefault(node.parent_id, []).append(node)

    def contains(node: Any, x: int, y: int) -> bool:
        """执行 的`contains`操作。

        参数:
            node: 当前遍历、校验或转换的树节点。 类型：`Any`。
            x: 空间坐标的水平分量。 类型：`int`。
            y: 空间坐标的垂直分量。 类型：`int`。

        返回:
            条件成立时返回 `True`，否则返回 `False`。
        """
        bounds = node.bounds
        return (
            bounds.x <= x < bounds.x + bounds.width
            and bounds.y <= y < bounds.y + bounds.height
        )

    definition = copy.deepcopy(world.definition)
    compiled_tiles: list[dict[str, Any]] = []
    expected_kinds = ("SECTOR", "ARENA", "GAME_OBJECT")
    for raw_tile in definition.get("tiles", []):
        tile = copy.deepcopy(raw_tile)
        coord = tile.get("coord")
        if not isinstance(coord, list) or len(coord) != 2:
            compiled_tiles.append(tile)
            continue
        x, y = coord
        path = [root.name]
        semantics = [
            {
                "kind": root.kind,
                "id": root.id,
                "name": root.name,
                "semantic": root.semantic,
            }
        ]
        parent = root
        for expected_kind in expected_kinds:
            candidates = [
                child
                for child in children_by_parent.get(parent.id, [])
                if child.kind == expected_kind and contains(child, x, y)
            ]
            if not candidates:
                break
            parent = min(
                candidates,
                key=lambda node: (
                    node.bounds.width * node.bounds.height,
                    node.sort_order,
                    node.id,
                ),
            )
            path.append(parent.name)
            semantics.append(
                {
                    "kind": parent.kind,
                    "id": parent.id,
                    "name": parent.name,
                    "semantic": parent.semantic,
                }
            )
        tile["address"] = path
        tile["spatial_semantics"] = semantics
        compiled_tiles.append(tile)

    definition["world"] = root.name
    definition["tile_address_keys"] = [
        "world",
        "sector",
        "arena",
        "game_object",
    ]
    definition["tiles"] = compiled_tiles
    payload = world.model_dump(mode="json", exclude_none=False)
    payload["world_name"] = root.name
    payload["definition"] = definition
    return WorldConfig.model_validate(payload)


MAP_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    {
        "key": "two-day-commute",
        "name": "住宅—公司两日通勤",
        "summary": "从空白网格逐步配置住宅、公司、两个三车道路口、行人网络、信号灯、门禁与停车位。",
        "width": 96,
        "height": 56,
        "tile_size": 32,
        "steps": [
            {"step": 1, "key": "zones", "name": "住宅区与公司园区", "tool": "区域"},
            {"step": 2, "key": "road", "name": "双向六车道主路", "tool": "道路"},
            {
                "step": 3,
                "key": "intersection-a",
                "name": "三车道路口 A",
                "tool": "模块",
            },
            {
                "step": 4,
                "key": "intersection-b",
                "name": "复制三车道路口 B",
                "tool": "模块",
            },
            {
                "step": 5,
                "key": "pedestrian-network",
                "name": "人行道与步行网络",
                "tool": "人行",
            },
            {
                "step": 6,
                "key": "signals",
                "name": "8 个信号灯与等待区",
                "tool": "信号灯",
            },
            {
                "step": 7,
                "key": "facilities",
                "name": "车辆门禁与 P01–P03",
                "tool": "设施",
            },
            {
                "step": 8,
                "key": "semantics",
                "name": "空间语义与导航校验",
                "tool": "语义",
            },
        ],
    },
)


def _map_blueprint(key: str) -> dict[str, Any] | None:
    """执行地图`blueprint`的内部处理，供当前模块或类复用。

    参数:
        key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。

    返回:
        返回以字段名或业务键组织的结构化映射。 没有可用结果时返回 `None`。
    """
    return next(
        (copy.deepcopy(item) for item in MAP_BLUEPRINTS if item["key"] == key), None
    )


def _commute_blueprint_editor_document(
    *, name: str, stable_key: str, width: int, height: int, tile_size: int, step: int
) -> dict[str, Any]:
    """Build the canonical four-level authoring tree for the commute blueprint."""

    root_id = f"world-{stable_key}"
    nodes: list[dict[str, Any]] = []

    def add_node(
        node_id: str,
        kind: str,
        parent_id: str | None,
        node_name: str,
        bounds: tuple[int, int, int, int],
        semantic: str,
        sort_order: int,
    ) -> None:
        x, y, node_width, node_height = bounds
        nodes.append(
            {
                "id": node_id,
                "kind": kind,
                "parent_id": parent_id,
                "name": node_name,
                "sort_order": sort_order,
                "bounds": {
                    "x": x,
                    "y": y,
                    "width": node_width,
                    "height": node_height,
                },
                "semantic": semantic,
                "material_slice_id": None,
                "render_recipe_id": None,
                "render_mode": "LAYER_BACKED",
                "interaction_mode": "STATIC",
                "skill_bindings": [],
                "extensions": {},
            }
        )

    add_node(
        root_id,
        "WORLD",
        None,
        name,
        (0, 0, width, height),
        "包含住宅、通勤道路和公司园区的两日通勤实验世界。",
        0,
    )
    if step >= 1:
        add_node(
            "sector-home",
            "SECTOR",
            root_id,
            "住宅区",
            (0, 35, 25, 21),
            "居民出发、返家和步行接驳区域。",
            0,
        )
        add_node(
            "arena-home",
            "ARENA",
            "sector-home",
            "林晨住宅",
            (3, 37, 16, 16),
            "林晨居住并开始每日通勤的住宅场所。",
            0,
        )
        add_node(
            "object-home-building",
            "GAME_OBJECT",
            "arena-home",
            "住宅建筑",
            (7, 41, 9, 9),
            "住宅建筑本体；可被感知，但不绑定被动 Skill。",
            0,
        )
        add_node(
            "sector-office",
            "SECTOR",
            root_id,
            "公司园区",
            (74, 0, 22, 24),
            "办公、门禁和停车发生的公司园区。",
            1,
        )
        add_node(
            "arena-office-building",
            "ARENA",
            "sector-office",
            "办公楼区域",
            (76, 2, 18, 10),
            "员工工作的办公楼及其入口区域。",
            0,
        )
        add_node(
            "object-office-building",
            "GAME_OBJECT",
            "arena-office-building",
            "办公楼",
            (80, 4, 11, 8),
            "办公楼建筑本体。",
            0,
        )
        add_node(
            "arena-office-entry",
            "ARENA",
            "sector-office",
            "园区入口与停车场",
            (76, 12, 18, 12),
            "车辆和行人进入园区、通过门禁并停车的区域。",
            1,
        )
    if step >= 2:
        add_node(
            "sector-transport",
            "SECTOR",
            root_id,
            "城市道路",
            (0, 0, width, height),
            "连接住宅区与公司园区的公共通勤道路网络。",
            2,
        )
        add_node(
            "arena-commute-corridor",
            "ARENA",
            "sector-transport",
            "东西向通勤主路",
            (0, 0, width, height),
            "包含机动车道、人行道、路口和过街设施的通勤走廊。",
            0,
        )
    for required_step, key, center_x in ((3, "a", 34), (4, "b", 60)):
        if step < required_step:
            continue
        add_node(
            f"object-intersection-{key}",
            "GAME_OBJECT",
            "arena-commute-corridor",
            f"三车道路口 {key.upper()}",
            (center_x - 7, 20, 14, 17),
            "机动车、行人与信号控制发生冲突和协商的四向路口。",
            required_step,
        )
    if step >= 6:
        for key, center_x in (("a", 34), ("b", 60)):
            for order, (side, x, y) in enumerate(
                (
                    ("north", center_x - 5, 22),
                    ("east", center_x + 4, 22),
                    ("south", center_x + 4, 33),
                    ("west", center_x - 5, 33),
                )
            ):
                add_node(
                    f"object-signal-{key}-{side}",
                    "GAME_OBJECT",
                    "arena-commute-corridor",
                    f"路口 {key.upper()} {side} 信号灯",
                    (x, y, 1, 1),
                    "控制车辆与行人通行次序的交通信号灯。",
                    10 + order,
                )
    if step >= 7:
        add_node(
            "object-office-gate",
            "GAME_OBJECT",
            "arena-office-entry",
            "园区车辆门禁",
            (80, 20, 1, 1),
            "核验车辆进入公司园区资格的门禁。",
            0,
        )
        for order, x in enumerate((85, 88, 91), start=1):
            add_node(
                f"object-parking-p{order:02d}",
                "GAME_OBJECT",
                "arena-office-entry",
                f"停车位 P{order:02d}",
                (x, 14, 1, 1),
                "可记录占用状态的园区停车位。",
                order,
            )

    def layer(layer_id: str, layer_name: str, level: str, z_index: int) -> dict[str, Any]:
        return {
            "id": layer_id,
            "name": layer_name,
            "display_level": level,
            "z_index": z_index,
            "width": width,
            "height": height,
            "raw_gids": [],
            "cell_overrides": [],
            "recipe_placements": [],
            "visible": True,
            "opacity": 1.0,
        }

    document = {
        "schema_version": "ga-map-editor/v2",
        "root_node_id": root_id,
        "material_sources": [],
        "material_slices": [],
        "material_canvases": [],
        "render_recipes": [],
        "visual_layers": [
            layer("layer-world", "地图底图", "MAP", 0),
            layer("layer-sector", "Sector", "SECTOR", 10),
            layer("layer-arena", "Arena", "ARENA", 20),
            layer("layer-object", "Game Object", "GAME_OBJECT", 30),
        ],
        "hierarchy_nodes": nodes,
        "import_metadata": {
            "importer": "two-day-commute-blueprint/v2",
            "width": width,
            "height": height,
            "tile_size": tile_size,
            "used_gid_count": 0,
            "collision_coords": [],
            "source_sha256": "",
        },
        "tile_overrides": {},
        "tile_override_parts": {},
        "tile_override_layers": {},
        "ui_state": {},
    }
    return MapEditorDocumentV2.model_validate(document).model_dump(
        mode="json", exclude_none=False
    )


def _commute_blueprint_world(
    session: Session,
    *,
    name: str,
    stable_key: str,
    step: int,
) -> WorldConfig:
    """执行`commute``blueprint`世界的内部处理，供当前模块或类复用。

    参数:
        session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
        name: 目标对象的人类可读名称。 类型：`str`。
        stable_key: 用于稳定定位`stable`的键。 类型：`str`。
        step: 当前处理、查询或恢复的仿真步记录或编号。 类型：`int`。

    返回:
        返回 `WorldConfig` 类型的处理结果。

    异常:
        ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
    """

    blueprint = _map_blueprint("two-day-commute")
    assert blueprint is not None
    if not 0 <= step <= len(blueprint["steps"]):
        raise ServiceError(
            "MAP_BLUEPRINT_STEP_INVALID",
            "地图蓝图步骤超出范围",
            status_code=422,
        )
    required_asset_keys = {
        "tile-ground",
        "tile-road-asphalt",
        "tile-sidewalk",
        "marking-crosswalk",
        "object-traffic-light",
        "zone-pedestrian-wait",
        "marking-vehicle-stop-line",
        "object-vehicle-gate",
        "zone-parking-slot",
    }
    assets = list(
        session.scalars(
            select(SpatialAssetDefinition).where(
                SpatialAssetDefinition.asset_key.in_(required_asset_keys)
            )
        )
    )
    revisions = {
        asset.asset_key: session.get(
            SpatialAssetRevision, asset.current_published_revision_id
        )
        for asset in assets
        if asset.current_published_revision_id
    }
    if set(revisions) != required_asset_keys or any(
        revision is None or revision.state != RevisionState.PUBLISHED.value
        for revision in revisions.values()
    ):
        raise ServiceError(
            "MAP_BLUEPRINT_ASSET_UNAVAILABLE",
            "两日通勤蓝图依赖的地图资产尚未全部发布",
            status_code=503,
        )

    width, height, tile_size = (
        blueprint["width"],
        blueprint["height"],
        blueprint["tile_size"],
    )
    world = _blank_public_world(
        name=name,
        stable_key=stable_key,
        width=width,
        height=height,
        tile_size=tile_size,
    ).model_dump(mode="json", exclude_none=False)
    definition = world["definition"]
    palette = [
        {"id": "ground", "name": "基础地面", "color": "#c9d9bd", "collision": False},
        {"id": "home-zone", "name": "住宅区", "color": "#dbe8ce", "collision": False},
        {
            "id": "office-zone",
            "name": "公司园区",
            "color": "#c9ded7",
            "collision": False,
        },
        {"id": "building", "name": "建筑", "color": "#f0e4cf", "collision": True},
        {"id": "road", "name": "六车道道路", "color": "#53605d", "collision": False},
        {"id": "sidewalk", "name": "人行道", "color": "#d5ddd7", "collision": False},
        {"id": "crosswalk", "name": "斑马线", "color": "#f7faf8", "collision": False},
        {"id": "parking", "name": "停车区域", "color": "#b7d8cc", "collision": False},
    ]
    cells: dict[str, dict[str, str]] = {
        f"{x},{y}": {"kind": "ground"} for y in range(height) for x in range(width)
    }
    tiles = {tuple(tile["coord"]): tile for tile in definition["tiles"]}

    def paint(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        kind: str,
        *,
        address: list[str] | None = None,
        collision: bool | None = None,
    ) -> None:
        """执行 的`paint`操作。

        参数:
            x1: 传入当前算法的`x1`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`int`。
            y1: 传入当前算法的`y1`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`int`。
            x2: 传入当前算法的`x2`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`int`。
            y2: 传入当前算法的`y2`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`int`。
            kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`str`。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。 类型：`list[str] | None`。 默认值：`None`。
            collision: 路径或移动过程中检测到的碰撞信息。 类型：`bool | None`。 默认值：`None`。

        返回:
            无返回值。
        """
        for y in range(max(0, y1), min(height - 1, y2) + 1):
            for x in range(max(0, x1), min(width - 1, x2) + 1):
                cells[f"{x},{y}"] = {"kind": kind}
                tile = tiles[(x, y)]
                tile["tile"] = kind
                if address is not None:
                    tile["address"] = list(address)
                if collision is not None:
                    tile["collision"] = collision

    module_map = session.scalar(
        select(WorldMap).where(WorldMap.map_key == "standard-3lane-intersection")
    )
    module_revision_id = (
        module_map.current_published_revision_id if module_map is not None else None
    )
    module_instances: list[dict[str, Any]] = []
    intersections: list[dict[str, Any]] = []
    placements: list[dict[str, Any]] = []

    def placement(
        key: str,
        asset_key: str,
        x: float,
        y: float,
        rotation: float = 0,
        state: dict[str, Any] | None = None,
    ) -> None:
        """向地图蓝图添加一个空间资源实例及其初始状态覆盖。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。
            asset_key: 用于稳定定位资源的键。 类型：`str`。
            x: 空间坐标的水平分量。 类型：`float`。
            y: 空间坐标的垂直分量。 类型：`float`。
            rotation: 空间资源实例相对于默认方向的旋转角度。 类型：`float`。 默认值：`0`。
            state: 空间资源实例的初始状态覆盖映射；为空时使用资源定义中的默认状态。 类型：`dict[str, Any] | None`。 默认值：`None`。

        返回:
            无返回值。
        """
        placements.append(
            {
                "instance_key": key,
                "spatial_asset_revision_id": revisions[asset_key].id,
                "x_m": x,
                "y_m": y,
                "rotation_degrees": rotation,
                "state_overrides": state or {},
            }
        )

    def intersection(key: str, cx: int) -> None:
        """执行 的`intersection`操作。

        参数:
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。
            cx: 传入当前算法的`cx`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`int`。

        返回:
            无返回值。
        """
        paint(
            cx - 3,
            0,
            cx + 2,
            height - 1,
            "road",
            address=["城市道路", f"三车道路口 {key}"],
        )
        paint(
            cx - 3,
            21,
            cx + 2,
            23,
            "crosswalk",
            address=["城市道路", f"三车道路口 {key}", "北侧斑马线"],
        )
        paint(
            cx - 3,
            32,
            cx + 2,
            34,
            "crosswalk",
            address=["城市道路", f"三车道路口 {key}", "南侧斑马线"],
        )
        paint(
            cx - 6,
            25,
            cx - 4,
            30,
            "crosswalk",
            address=["城市道路", f"三车道路口 {key}", "西侧斑马线"],
        )
        paint(
            cx + 3,
            25,
            cx + 5,
            30,
            "crosswalk",
            address=["城市道路", f"三车道路口 {key}", "东侧斑马线"],
        )
        module_instances.append(
            {
                "instance_key": f"intersection-{key.casefold()}",
                "module_key": "standard-3lane-intersection",
                "source_map_revision_id": module_revision_id,
                "center": [cx, 28],
                "rotation_degrees": 0,
            }
        )
        intersections.append(
            {
                "intersection_key": key.casefold(),
                "center": [cx, 28],
                "lanes_per_direction": 3,
                "lane_width_m": 1.0,
                "crosswalk_keys": [
                    f"{key.casefold()}-{side}"
                    for side in ("north", "east", "south", "west")
                ],
            }
        )

    if step >= 1:
        paint(3, 37, 18, 52, "home-zone", address=["住宅区", "林晨住宅"])
        paint(
            7,
            41,
            15,
            49,
            "building",
            address=["住宅区", "林晨住宅", "住宅建筑"],
            collision=True,
        )
        paint(76, 2, 92, 17, "office-zone", address=["公司园区"])
        paint(80, 4, 90, 11, "building", address=["公司园区", "办公楼"], collision=True)
    if step >= 2:
        paint(0, 25, width - 1, 30, "road", address=["城市道路", "东西向通勤主路"])
        paint(0, 23, width - 1, 24, "sidewalk", address=["城市道路", "北侧人行道"])
        paint(0, 31, width - 1, 32, "sidewalk", address=["城市道路", "南侧人行道"])
        paint(11, 31, 14, 40, "road", address=["住宅区", "车辆出入口"])
        paint(79, 12, 82, 24, "road", address=["公司园区", "车辆入口"])
    if step >= 3:
        intersection("A", 34)
    if step >= 4:
        intersection("B", 60)
    if step >= 5:
        definition["navigation_networks"] = [
            {
                "network_key": "vehicle-commute",
                "mode": "CAR",
                "route": [
                    "home.driveway",
                    "intersection.a",
                    "intersection.b",
                    "office.gate",
                    "parking.P03",
                ],
                "distance_km": 1.8,
            },
            {
                "network_key": "pedestrian-commute",
                "mode": "PEDESTRIAN",
                "route": ["home.entry", "crosswalk.a", "crosswalk.b", "office.entry"],
                "distance_km": 1.2,
            },
        ]
        paint(15, 33, 15, 40, "sidewalk", address=["住宅区", "步行出口"])
        paint(83, 12, 83, 23, "sidewalk", address=["公司园区", "步行入口"])
    if step >= 6:
        for key, cx, offset in (("a", 34, 0), ("b", 60, 8_000)):
            signal_specs = (
                ("north", cx - 5, 22, 0, "wait-east"),
                ("east", cx + 4, 22, 90, "wait-north"),
                ("south", cx + 4, 33, 180, "wait-west"),
                ("west", cx - 5, 33, 270, "wait-south"),
            )
            for index, (side, x, y, rotation, wait_side) in enumerate(signal_specs):
                placement(
                    f"signal-{key}-{side}",
                    "object-traffic-light",
                    x,
                    y,
                    rotation,
                    {
                        "state": "VEHICLE_GREEN" if index % 2 else "VEHICLE_RED",
                        "phase": "VEHICLE_GREEN" if index % 2 else "VEHICLE_RED",
                    },
                )
            for side, x, y in (
                ("north", cx, 20),
                ("east", cx + 7, 28),
                ("south", cx, 36),
                ("west", cx - 7, 28),
            ):
                placement(f"{key}-wait-{side}", "zone-pedestrian-wait", x, y)
    if step >= 7:
        placement(
            "gate-office-entry",
            "object-vehicle-gate",
            80,
            20,
            0,
            {"state": "closed", "required_credential": "company.vehicle.enter"},
        )
        for index, x in enumerate((85, 88, 91), start=1):
            placement(
                f"parking-p{index:02d}",
                "zone-parking-slot",
                x,
                14,
                0,
                {"occupied": index < 3, "slot_key": f"P{index:02d}"},
            )
        paint(83, 12, 93, 16, "parking", address=["公司园区", "停车场"])
    if step >= 8:
        definition["commute_semantics"] = {
            "home": "sector.home",
            "office": "sector.office",
            "intersection_waiting_zones": [
                "a-wait-north",
                "a-wait-east",
                "a-wait-south",
                "a-wait-west",
                "b-wait-north",
                "b-wait-east",
                "b-wait-south",
                "b-wait-west",
            ],
            "gate_credential": "company.vehicle.enter",
            "parking_slots": ["P01", "P02", "P03"],
        }

    definition["palette"] = [
        {
            "key": item["id"],
            "label": item["name"],
            "color": item["color"],
            "collision": item["collision"],
        }
        for item in palette
    ]
    definition["traffic_layout"] = {
        "intersection_type": "FOUR_WAY",
        "approaches": ["NORTH", "EAST", "SOUTH", "WEST"],
        "lanes_per_direction": 3,
        "lane_width_m": 1.0,
        "intersection_instances": intersections,
        "crosswalk_count": len(intersections) * 4,
    }
    definition["spatial_scene"] = {
        "schema_version": "ga-spatial-scene/v1",
        "meters_per_tile": 1.0,
        "palette_refs": {
            "ground": revisions["tile-ground"].id,
            "road": revisions["tile-road-asphalt"].id,
            "sidewalk": revisions["tile-sidewalk"].id,
            "crosswalk": revisions["marking-crosswalk"].id,
        },
        "placements": placements,
    }
    definition["editor"] = {
        "schema_version": 1,
        "palette": palette,
        "cells": cells,
        "spatial_assets": {
            revision.id: copy.deepcopy(revision.contract_json)
            for revision in revisions.values()
        },
        "module_instances": module_instances,
        "build_guide": {
            "blueprint_key": blueprint["key"],
            "name": blueprint["name"],
            "current_step": step,
            "total_steps": len(blueprint["steps"]),
            "steps": blueprint["steps"],
            "complete": step == len(blueprint["steps"]),
        },
    }
    definition["editor_v2"] = _commute_blueprint_editor_document(
        name=name,
        stable_key=stable_key,
        width=width,
        height=height,
        tile_size=tile_size,
        step=step,
    )
    return WorldConfig.model_validate(world)


def _blank_public_world(
    *, name: str, stable_key: str, width: int, height: int, tile_size: int
) -> WorldConfig:
    """执行`blank``public`世界的内部处理，供当前模块或类复用。

    参数:
        name: 目标对象的人类可读名称。 类型：`str`。
        stable_key: 用于稳定定位`stable`的键。 类型：`str`。
        width: 地图、图像或矩形区域的宽度。 类型：`int`。
        height: 地图、图像或矩形区域的高度。 类型：`int`。
        tile_size: `tile`的数量或容量。 类型：`int`。

    返回:
        返回 `WorldConfig` 类型的处理结果。
    """

    tiles = [
        {
            "coord": [x, y],
            "collision": False,
            "address": [],
            "tile": "ground",
        }
        for y in range(height)
        for x in range(width)
    ]
    return WorldConfig.model_validate(
        {
            "world_key": stable_key,
            "world_name": name,
            "definition": {
                "world": name,
                "size": [height, width],
                "tile_size": tile_size,
                "tile_address_keys": ["world", "sector", "arena", "object"],
                "tiles": tiles,
                "palette": [
                    {
                        "key": "ground",
                        "label": "地面",
                        "color": "#dce9df",
                        "collision": False,
                    }
                ],
            },
            "assets": [],
        }
    )


def _validate_world_definition(world: WorldConfig) -> list[dict[str, str]]:
    """校验世界仿真定义。

    参数:
        world: 当前运行使用的世界配置或运行时世界对象。 类型：`WorldConfig`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """

    definition = world.definition
    errors: list[dict[str, str]] = []
    size = definition.get("size") if isinstance(definition, dict) else None
    if (
        not isinstance(size, list)
        or len(size) != 2
        or any(not isinstance(value, int) or value < 1 for value in size)
    ):
        errors.append(
            {
                "code": "WORLD_SIZE_INVALID",
                "path": "definition.size",
                "message": "地图尺寸必须是 [高度, 宽度]，且两项均为正整数",
            }
        )
        return errors
    height, width = size
    if not isinstance(definition.get("world"), str) or not definition["world"].strip():
        errors.append(
            {
                "code": "WORLD_NAME_REQUIRED",
                "path": "definition.world",
                "message": "运行时世界名称不能为空",
            }
        )
    tile_size = definition.get("tile_size")
    if not isinstance(tile_size, int) or tile_size < 1:
        errors.append(
            {
                "code": "WORLD_TILE_SIZE_INVALID",
                "path": "definition.tile_size",
                "message": "Tile 像素尺寸必须是正整数",
            }
        )
    address_keys = definition.get("tile_address_keys")
    if (
        not isinstance(address_keys, list)
        or not address_keys
        or address_keys[0] != "world"
        or any(
            not isinstance(value, str) or not value.strip() for value in address_keys
        )
    ):
        errors.append(
            {
                "code": "WORLD_ADDRESS_KEYS_INVALID",
                "path": "definition.tile_address_keys",
                "message": "地址层级必须从 world 开始，并至少包含一个有效层级",
            }
        )
        address_keys = ["world"]
    tiles = definition.get("tiles")
    if not isinstance(tiles, list):
        errors.append(
            {
                "code": "WORLD_TILES_INVALID",
                "path": "definition.tiles",
                "message": "地图 Tile 定义必须是数组",
            }
        )
        return errors
    seen: set[tuple[int, int]] = set()
    for index, tile in enumerate(tiles):
        coord = tile.get("coord") if isinstance(tile, dict) else None
        if (
            not isinstance(coord, list)
            or len(coord) != 2
            or any(not isinstance(value, int) for value in coord)
        ):
            errors.append(
                {
                    "code": "WORLD_TILE_COORD_INVALID",
                    "path": f"definition.tiles.{index}.coord",
                    "message": "Tile 坐标必须是 [x, y] 整数对",
                }
            )
            continue
        x, y = coord
        if not (0 <= x < width and 0 <= y < height):
            errors.append(
                {
                    "code": "WORLD_TILE_OUT_OF_BOUNDS",
                    "path": f"definition.tiles.{index}.coord",
                    "message": f"Tile 坐标 [{x}, {y}] 超出地图边界",
                }
            )
        if (x, y) in seen:
            errors.append(
                {
                    "code": "WORLD_TILE_DUPLICATED",
                    "path": f"definition.tiles.{index}.coord",
                    "message": f"Tile 坐标 [{x}, {y}] 重复定义",
                }
            )
        seen.add((x, y))
        if not isinstance(tile.get("collision"), bool):
            errors.append(
                {
                    "code": "WORLD_TILE_COLLISION_INVALID",
                    "path": f"definition.tiles.{index}.collision",
                    "message": "每个 Tile 必须显式声明是否碰撞",
                }
            )
        address = tile.get("address") if isinstance(tile, dict) else None
        if address is not None and (
            not isinstance(address, list)
            or len(address) > len(address_keys)
            or any(not isinstance(value, str) or not value.strip() for value in address)
            or (
                len(address) == len(address_keys)
                and address[0] != definition.get("world")
            )
        ):
            errors.append(
                {
                    "code": "WORLD_TILE_ADDRESS_INVALID",
                    "path": f"definition.tiles.{index}.address",
                    "message": "Tile 地址必须是非空字符串数组，且层级数不能超过地址定义",
                }
            )
    expected_tiles = height * width
    if len(seen) != expected_tiles:
        errors.append(
            {
                "code": "WORLD_TILE_GRID_INCOMPLETE",
                "path": "definition.tiles",
                "message": f"地图应包含 {expected_tiles} 个 Tile，当前为 {len(seen)} 个",
            }
        )
    editor = definition.get("editor") if isinstance(definition, dict) else None
    build_guide = editor.get("build_guide") if isinstance(editor, dict) else None
    if isinstance(build_guide, dict) and not build_guide.get("complete"):
        errors.append(
            {
                "code": "MAP_BLUEPRINT_INCOMPLETE",
                "path": "definition.editor.build_guide",
                "message": (
                    "地图构建向导尚未完成："
                    f"当前 {build_guide.get('current_step', 0)} / "
                    f"{build_guide.get('total_steps', '?')} 步"
                ),
            }
        )
    return errors


def _validate_spatial_scene(
    session: Session, world: WorldConfig, *, skill_registry=None
) -> list[dict[str, str]]:
    """校验空间数据`scene`。

    参数:
        session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
        world: 当前运行使用的世界配置或运行时世界对象。 类型：`WorldConfig`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """

    raw_scene = world.definition.get("spatial_scene")
    if raw_scene is None:
        return []
    try:
        scene = SpatialSceneExtension.model_validate(raw_scene)
    except ValidationError as exc:
        return [
            {
                "code": "SPATIAL_SCENE_INVALID",
                "path": "definition.spatial_scene",
                "message": item["msg"],
            }
            for item in exc.errors(include_url=False)
        ]
    errors: list[dict[str, str]] = []
    definition = world.definition
    size = definition.get("size")
    height, width = (
        size
        if isinstance(size, list)
        and len(size) == 2
        and all(isinstance(value, int) and value >= 0 for value in size)
        else [0, 0]
    )
    max_x = width * scene.meters_per_tile
    max_y = height * scene.meters_per_tile
    for palette_key, revision_id in scene.palette_refs.items():
        revision = session.get(SpatialAssetRevision, revision_id)
        kind = (revision.contract_json or {}).get("kind") if revision else None
        if revision is None or revision.state != RevisionState.PUBLISHED.value:
            errors.append(
                {
                    "code": "SPATIAL_ASSET_REVISION_UNAVAILABLE",
                    "path": f"definition.spatial_scene.palette_refs.{palette_key}",
                    "message": "画块必须引用已发布的空间资产版本",
                }
            )
        elif kind not in {"TILE", "MARKING"}:
            errors.append(
                {
                    "code": "SPATIAL_PALETTE_KIND_INVALID",
                    "path": f"definition.spatial_scene.palette_refs.{palette_key}",
                    "message": "画块调色板只能引用 TILE 或 MARKING 空间资产",
                }
            )
    tile_keys = {
        tile.get("tile")
        for tile in definition.get("tiles", [])
        if isinstance(tile, dict) and tile.get("tile")
    }
    unresolved = sorted(
        key
        for key in tile_keys
        if key not in scene.palette_refs
        and key
        not in {
            item.get("key")
            for item in definition.get("palette", [])
            if isinstance(item, dict)
        }
    )
    for key in unresolved:
        errors.append(
            {
                "code": "SPATIAL_PALETTE_REFERENCE_MISSING",
                "path": "definition.spatial_scene.palette_refs",
                "message": f"Tile 使用的画块 {key} 没有可解析的调色板引用",
            }
        )
    for index, placement in enumerate(scene.placements):
        revision = session.get(
            SpatialAssetRevision, placement.spatial_asset_revision_id
        )
        if revision is None or revision.state != RevisionState.PUBLISHED.value:
            errors.append(
                {
                    "code": "SPATIAL_ASSET_REVISION_UNAVAILABLE",
                    "path": f"definition.spatial_scene.placements.{index}.spatial_asset_revision_id",
                    "message": "地图物件必须引用已发布的空间资产版本",
                }
            )
            continue
        kind = (revision.contract_json or {}).get("kind")
        if kind == "TILE":
            errors.append(
                {
                    "code": "SPATIAL_PLACEMENT_KIND_INVALID",
                    "path": f"definition.spatial_scene.placements.{index}",
                    "message": "TILE 资产应通过画块调色板使用，不能作为物件放置",
                }
            )
        try:
            contract = SpatialAssetContract.model_validate(revision.contract_json)
        except ValidationError:
            contract = None
        if contract is not None:
            errors.extend(
                _validate_passive_skill_bindings(
                    contract.skill_bindings,
                    path=f"definition.spatial_scene.placements.{index}",
                    registry=skill_registry,
                )
            )
        if not (0 <= placement.x_m < max_x and 0 <= placement.y_m < max_y):
            errors.append(
                {
                    "code": "SPATIAL_PLACEMENT_OUT_OF_BOUNDS",
                    "path": f"definition.spatial_scene.placements.{index}",
                    "message": "地图物件坐标超出米制地图边界",
                }
            )
    return errors


def _validate_passive_skill_bindings(
    bindings, *, path: str, registry
) -> list[dict[str, str]]:
    """校验`passive`技能`bindings`。

    参数:
        bindings: 技能、提示词或空间对象之间的声明式绑定集合。
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`str`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    errors: list[dict[str, str]] = []
    for index, binding in enumerate(bindings):
        binding_path = f"{path}.skill_bindings.{index}"
        try:
            document = registry.get(binding.skill_name)
        except SkillRegistryError:
            errors.append(
                {
                    "code": "GAME_OBJECT_SKILL_UNAVAILABLE",
                    "path": f"{binding_path}.skill_name",
                    "message": f"Game Object 引用的 Skill {binding.skill_name} 不存在",
                }
            )
            continue
        if document.kind == "brain":
            errors.append(
                {
                    "code": "GAME_OBJECT_SKILL_NOT_PASSIVE",
                    "path": f"{binding_path}.skill_name",
                    "message": (
                        f"Game Object 不能绑定 Brain Skill {binding.skill_name}；"
                        "请绑定返回文本反馈的 atomic 或 pack Skill"
                    ),
                }
            )
    return errors


def _validate_map_editor_v2(
    world: WorldConfig, *, skill_registry=None
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """校验地图`editor``v2`。

    参数:
        world: 当前运行使用的世界配置或运行时世界对象。 类型：`WorldConfig`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    raw_document = world.definition.get("editor_v2")
    if raw_document is None:
        return [], []
    try:
        document = MapEditorDocumentV2.model_validate(raw_document)
    except ValidationError as exc:
        return (
            [
                {
                    "code": "MAP_EDITOR_V2_INVALID",
                    "path": "definition.editor_v2",
                    "message": item["msg"],
                }
                for item in exc.errors(include_url=False)
            ],
            [],
        )
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    game_objects = []
    for index, node in enumerate(document.hierarchy_nodes):
        errors.extend(
            _validate_passive_skill_bindings(
                node.skill_bindings,
                path=f"definition.editor_v2.hierarchy_nodes.{index}",
                registry=skill_registry,
            )
        )
        if node.kind == "GAME_OBJECT":
            game_objects.append((index, node))
    if game_objects and not any(
        node.interaction_mode == "SKILL_BOUND" for _index, node in game_objects
    ):
        warnings.append(
            {
                "code": "ALL_GAME_OBJECTS_STATIC",
                "path": "definition.editor_v2.hierarchy_nodes",
                "message": (
                    f"地图包含 {len(game_objects)} 个 Game Object，但没有任何对象绑定被动 Skill；"
                    "Agent 只能感知这些对象，不能与其交互。"
                ),
            }
        )
    return errors, warnings


class WorldMapService:
    """管理地图草稿、发布版本、实验引用和编辑器文档编译。"""

    def __init__(self, database: Database, *, skill_registry=None) -> None:
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。

        返回:
            无返回值。
        """
        self.database = database
        self.skill_registry = skill_registry or DatabaseSkillRegistry(
            database,
            cache_root="var/skill-runtime-cache",
        )

    @staticmethod
    def list_blueprints() -> list[dict[str, Any]]:
        """查询`blueprints`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        return [copy.deepcopy(item) for item in MAP_BLUEPRINTS]

    def create_map(
        self,
        *,
        name: str,
        description: str = "",
        source_revision_id: str | None = None,
        blueprint_key: str | None = None,
        map_key: str | None = None,
        width: int = 48,
        height: int = 32,
        tile_size: int = 32,
    ) -> dict[str, Any]:
        """创建地图。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。
            description: 目标对象的人类可读说明；会按业务规则去除无效空白。 类型：`str`。 默认值：`''`。
            source_revision_id: `source`修订版本的唯一标识。 类型：`str | None`。 默认值：`None`。
            blueprint_key: 用于稳定定位`blueprint`的键。 类型：`str | None`。 默认值：`None`。
            map_key: 用于稳定定位地图的键。 类型：`str | None`。 默认值：`None`。
            width: 地图、图像或矩形区域的宽度。 类型：`int`。 默认值：`48`。
            height: 地图、图像或矩形区域的高度。 类型：`int`。 默认值：`32`。
            tile_size: `tile`的数量或容量。 类型：`int`。 默认值：`32`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        name = name.strip()
        if not name:
            raise ServiceError("INVALID_MAP_NAME", "地图名称不能为空", status_code=422)
        stable_key = map_key.strip() if map_key else _make_key(name)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", stable_key):
            raise ServiceError(
                "INVALID_MAP_KEY",
                "地图稳定键必须由小写字母、数字和连字符组成",
                status_code=422,
            )
        if not (4 <= width <= 240 and 4 <= height <= 240 and 8 <= tile_size <= 128):
            raise ServiceError(
                "INVALID_MAP_DIMENSIONS",
                "地图宽高需在 4–240 之间，Tile 尺寸需在 8–128 像素之间",
                status_code=422,
            )
        if source_revision_id and blueprint_key:
            raise ServiceError(
                "MAP_CREATE_SOURCE_CONFLICT",
                "复制已发布地图与使用构建蓝图不能同时选择",
                status_code=422,
            )
        blueprint = _map_blueprint(blueprint_key) if blueprint_key else None
        if blueprint_key and blueprint is None:
            raise ServiceError(
                "MAP_BLUEPRINT_NOT_FOUND",
                "地图构建蓝图不存在",
                status_code=404,
            )
        with self.database.session_factory.begin() as session:
            if session.scalar(
                select(WorldMap.id).where(WorldMap.map_key == stable_key)
            ):
                raise ServiceError(
                    "MAP_KEY_CONFLICT", "地图稳定键已被使用", status_code=409
                )
            base_revision: WorldMapRevision | None = None
            if source_revision_id:
                base_revision = session.get(WorldMapRevision, source_revision_id)
                if (
                    base_revision is None
                    or base_revision.state != RevisionState.PUBLISHED.value
                ):
                    raise not_found("map_revision", source_revision_id)
                world = normalize_public_world(base_revision.world_json)
            elif blueprint is not None:
                world = normalize_public_world(
                    _commute_blueprint_world(
                        session,
                        name=name,
                        stable_key=stable_key,
                        step=0,
                    )
                )
            else:
                world = normalize_public_world(
                    _blank_public_world(
                        name=name,
                        stable_key=stable_key,
                        width=width,
                        height=height,
                        tile_size=tile_size,
                    )
                )
            now = _utc_now()
            public_map = WorldMap(
                id=str(uuid4()),
                map_key=stable_key,
                name=name,
                description=description,
                status=RevisionState.DRAFT.value,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(public_map)
            session.flush()
            revision = WorldMapRevision(
                id=str(uuid4()),
                map_id=public_map.id,
                revision_no=1,
                state=RevisionState.DRAFT.value,
                base_revision_id=base_revision.id if base_revision else None,
                schema_version=1,
                world_json=world.model_dump(mode="json", exclude_none=False),
                world_hash=world_hash(world),
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(revision)
            session.flush()
            public_map.current_draft_revision_id = revision.id
            return self._map_detail(session, public_map)

    def apply_blueprint_step(
        self,
        map_id: str,
        *,
        expected_lock_version: int,
        step: int,
    ) -> dict[str, Any]:
        """应用`blueprint`仿真步。

        参数:
            map_id: 地图的唯一标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。
            step: 当前处理、查询或恢复的仿真步记录或编号。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """

        now = _utc_now()
        with self.database.session_factory.begin() as session:
            public_map, revision = self._require_draft(session, map_id)
            if revision.lock_version != expected_lock_version:
                raise ServiceError(
                    "MAP_REVISION_CONFLICT",
                    "地图草稿已变化，请重新载入后继续构建",
                    status_code=409,
                    details={
                        "expected_lock_version": expected_lock_version,
                        "actual_lock_version": revision.lock_version,
                    },
                )
            current_world = WorldConfig.model_validate(revision.world_json)
            editor = current_world.definition.get("editor") or {}
            guide = editor.get("build_guide") or {}
            blueprint_key = guide.get("blueprint_key")
            if blueprint_key != "two-day-commute":
                raise ServiceError(
                    "MAP_BLUEPRINT_NOT_ATTACHED",
                    "当前地图草稿没有两日通勤构建向导",
                    status_code=409,
                )
            current_step = int(guide.get("current_step") or 0)
            if step != current_step + 1:
                raise ServiceError(
                    "MAP_BLUEPRINT_STEP_OUT_OF_ORDER",
                    "地图蓝图必须按顺序构建",
                    status_code=409,
                    details={"current_step": current_step, "requested_step": step},
                )
            world = normalize_public_world(
                _commute_blueprint_world(
                    session,
                    name=public_map.name,
                    stable_key=public_map.map_key,
                    step=step,
                )
            )
            digest = world_hash(world)
            result = session.execute(
                update(WorldMapRevision)
                .where(
                    WorldMapRevision.id == revision.id,
                    WorldMapRevision.state == RevisionState.DRAFT.value,
                    WorldMapRevision.lock_version == expected_lock_version,
                )
                .values(
                    world_json=world.model_dump(mode="json", exclude_none=False),
                    world_hash=digest,
                    validation_json=None,
                    lock_version=WorldMapRevision.lock_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise ServiceError(
                    "MAP_REVISION_CONFLICT",
                    "地图草稿已变化，请重新载入后继续构建",
                    status_code=409,
                )
            public_map.updated_at = now
            public_map.row_version += 1
            session.flush()
            return self._revision_detail(
                session.get(WorldMapRevision, revision.id), public_map
            )

    def list_maps(
        self,
        *,
        query: str | None = None,
        status: RevisionState | str | None = None,
        page: int = 1,
        page_size: int = 5,
        archived: str = "active",
    ) -> dict[str, Any]:
        """查询`maps`。

        参数:
            query: 用于名称、正文或标识模糊匹配的搜索文本。 类型：`str | None`。 默认值：`None`。
            status: 目录对象状态筛选值。允许值：`DRAFT`（草稿）或 `PUBLISHED`（已发布）。 类型：`RevisionState | str | None`。 默认值：`None`。
            page: 从 1 开始的分页页码。 类型：`int`。 默认值：`1`。
            page_size: 每页最多返回的记录数量。 类型：`int`。 默认值：`5`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        if page < 1 or page_size < 1 or page_size > 100:
            raise ServiceError(
                "INVALID_PAGINATION", "地图分页参数无效", status_code=422
            )
        if archived not in {"active", "archived", "all"}:
            raise ServiceError(
                "INVALID_ARCHIVE_FILTER", "地图归档筛选无效", status_code=422
            )
        try:
            normalized_status = (
                RevisionState(str(status).upper()).value if status else None
            )
        except ValueError as exc:
            raise ServiceError(
                "INVALID_MAP_STATUS", "地图状态筛选无效", status_code=422
            ) from exc
        with self.database.session_factory() as session:
            statement = select(WorldMap)
            count_statement = select(func.count()).select_from(WorldMap)
            status_count_statement = select(WorldMap.status, func.count()).group_by(
                WorldMap.status
            )
            archive_predicate = (
                WorldMap.archived_at.is_(None)
                if archived == "active"
                else WorldMap.archived_at.is_not(None)
                if archived == "archived"
                else None
            )
            if archive_predicate is not None:
                statement = statement.where(archive_predicate)
                count_statement = count_statement.where(archive_predicate)
                status_count_statement = status_count_statement.where(
                    archive_predicate
                )
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                predicate = or_(
                    WorldMap.name.ilike(pattern), WorldMap.map_key.ilike(pattern)
                )
                statement = statement.where(predicate)
                count_statement = count_statement.where(predicate)
                status_count_statement = status_count_statement.where(predicate)
            status_counts = {
                RevisionState.DRAFT.value: 0,
                RevisionState.PUBLISHED.value: 0,
            }
            for item_status, item_count in session.execute(status_count_statement):
                status_counts[item_status] = int(item_count)
            status_counts["ALL"] = sum(status_counts.values())
            if normalized_status:
                statement = statement.where(WorldMap.status == normalized_status)
                count_statement = count_statement.where(
                    WorldMap.status == normalized_status
                )
            total = int(session.scalar(count_statement) or 0)
            rows = list(
                session.scalars(
                    statement.order_by(WorldMap.updated_at.desc(), WorldMap.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return {
                "items": [self._map_detail(session, item) for item in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, ceil(total / page_size)),
                "status_counts": status_counts,
            }

    def set_archived(self, map_id: str, *, archived: bool) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            public_map.archived_at = _utc_now() if archived else None
            public_map.updated_at = _utc_now()
            public_map.row_version += 1
        return self.get_map(map_id)

    def delete_map(self, map_id: str) -> None:
        with self.database.session_factory.begin() as session:
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            if self._usage_experiment_ids(session, map_id):
                raise ServiceError(
                    "MAP_IN_USE",
                    "地图仍被实验 Revision 引用；请先删除引用它的实验",
                    status_code=409,
                )
            public_map.current_draft_revision_id = None
            public_map.current_published_revision_id = None
            session.flush()
            session.execute(
                update(WorldMapRevision)
                .where(WorldMapRevision.map_id == map_id)
                .values(base_revision_id=None)
            )
            session.execute(
                delete(WorldMapRevision).where(WorldMapRevision.map_id == map_id)
            )
            session.delete(public_map)

    def get_map(self, map_id: str) -> dict[str, Any]:
        """获取地图。

        参数:
            map_id: 地图的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            return self._map_detail(session, public_map)

    def get_draft(self, map_id: str) -> dict[str, Any]:
        """获取`draft`。

        参数:
            map_id: 地图的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            public_map, revision = self._require_draft(session, map_id)
            return self._revision_detail(revision, public_map)

    def get_revision(self, map_id: str, revision_id: str) -> dict[str, Any]:
        """获取修订版本。

        参数:
            map_id: 地图的唯一标识。 类型：`str`。
            revision_id: 实验修订版本的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            public_map = session.get(WorldMap, map_id)
            revision = session.get(WorldMapRevision, revision_id)
            if public_map is None:
                raise not_found("map", map_id)
            if revision is None or revision.map_id != map_id:
                raise not_found("map_revision", revision_id)
            return self._revision_detail(revision, public_map)

    def update_draft(
        self,
        map_id: str,
        *,
        expected_lock_version: int,
        world: WorldConfig | dict[str, Any],
    ) -> dict[str, Any]:
        """更新`draft`。

        参数:
            map_id: 地图的唯一标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。
            world: 当前运行使用的世界配置或运行时世界对象。 类型：`WorldConfig | dict[str, Any]`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        normalized = normalize_public_world(world)
        digest = world_hash(normalized)
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            public_map, revision = self._require_draft(session, map_id)
            result = session.execute(
                update(WorldMapRevision)
                .where(
                    WorldMapRevision.id == revision.id,
                    WorldMapRevision.state == RevisionState.DRAFT.value,
                    WorldMapRevision.lock_version == expected_lock_version,
                )
                .values(
                    world_json=normalized.model_dump(mode="json", exclude_none=False),
                    world_hash=digest,
                    validation_json=None,
                    lock_version=WorldMapRevision.lock_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                actual = session.scalar(
                    select(WorldMapRevision.lock_version).where(
                        WorldMapRevision.id == revision.id
                    )
                )
                raise ServiceError(
                    "MAP_REVISION_CONFLICT",
                    "地图草稿已被其他请求修改，请重新载入",
                    status_code=409,
                    details={
                        "expected_lock_version": expected_lock_version,
                        "actual_lock_version": actual,
                    },
                )
            public_map.updated_at = now
            public_map.row_version += 1
            session.flush()
            return self._revision_detail(
                session.get(WorldMapRevision, revision.id), public_map
            )

    def publish_draft(
        self,
        map_id: str,
        *,
        draft_revision_id: str,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        """发布`draft`。

        参数:
            map_id: 地图的唯一标识。 类型：`str`。
            draft_revision_id: 当前正在编辑且受乐观锁保护的草稿修订版本标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        if hasattr(self.skill_registry, "ensure_builtin_skills"):
            self.skill_registry.ensure_builtin_skills()
        validation_failure: dict[str, Any] | None = None
        with self.database.session_factory.begin() as session:
            public_map, revision = self._require_draft(session, map_id)
            if (
                revision.id != draft_revision_id
                or revision.lock_version != expected_lock_version
            ):
                raise ServiceError(
                    "MAP_REVISION_CONFLICT",
                    "地图草稿已变化，请重新载入",
                    status_code=409,
                )
            world = normalize_public_world(revision.world_json)
            editor_errors, editor_warnings = _validate_map_editor_v2(
                world, skill_registry=self.skill_registry
            )
            if not editor_errors:
                world = _compile_editor_v2_runtime_addresses(world)
            world_errors = _validate_world_definition(world)
            spatial_errors = _validate_spatial_scene(
                session, world, skill_registry=self.skill_registry
            )
            errors = [*world_errors, *spatial_errors, *editor_errors]
            checks = [
                {
                    "code": "EDITOR_V2_HIERARCHY_AND_SKILLS",
                    "message": "四层空间层级、Game Object 与被动 Skill 引用",
                    "status": "FAILED" if editor_errors else "PASSED",
                },
                {
                    "code": "WORLD_TILE_GRID",
                    "message": "地图尺寸、Tile 坐标、碰撞与语义地址",
                    "status": "FAILED" if world_errors else "PASSED",
                },
                {
                    "code": "SPATIAL_SCENE_CONTRACTS",
                    "message": "版本化空间资产、放置和初始状态合同",
                    "status": "FAILED" if spatial_errors else "PASSED",
                },
            ]
            if errors:
                revision.validation_json = {
                    "valid": False,
                    "errors": errors,
                    "warnings": editor_warnings,
                    "checks": checks,
                }
                revision.updated_at = _utc_now()
                validation_failure = dict(revision.validation_json)
            else:
                now = _utc_now()
                revision.world_json = world.model_dump(mode="json", exclude_none=False)
                revision.world_hash = world_hash(world)
                revision.validation_json = {
                    "valid": True,
                    "errors": [],
                    "warnings": editor_warnings,
                    "checks": checks,
                }
                revision.state = RevisionState.PUBLISHED.value
                revision.published_at = now
                revision.updated_at = now
                public_map.current_draft_revision_id = None
                public_map.current_published_revision_id = revision.id
                public_map.status = RevisionState.PUBLISHED.value
                public_map.row_version += 1
                public_map.updated_at = now
                session.flush()
                return self._revision_detail(revision, public_map)
        raise ServiceError(
            "MAP_VALIDATION_FAILED",
            "地图未通过发布校验",
            status_code=422,
            details=validation_failure or {},
        )

    def fork_revision(self, map_id: str, revision_id: str) -> dict[str, Any]:
        """执行 `WorldMapService` 的`fork`修订版本操作。

        参数:
            map_id: 地图的唯一标识。 类型：`str`。
            revision_id: 实验修订版本的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        with self.database.session_factory.begin() as session:
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            if public_map.current_draft_revision_id:
                raise ServiceError(
                    "MAP_DRAFT_EXISTS", "该地图已有编辑中的草稿", status_code=409
                )
            source = session.get(WorldMapRevision, revision_id)
            if (
                source is None
                or source.map_id != map_id
                or source.state != RevisionState.PUBLISHED.value
            ):
                raise not_found("map_revision", revision_id)
            number = (
                int(
                    session.scalar(
                        select(func.max(WorldMapRevision.revision_no)).where(
                            WorldMapRevision.map_id == map_id
                        )
                    )
                    or 0
                )
                + 1
            )
            now = _utc_now()
            draft = WorldMapRevision(
                id=str(uuid4()),
                map_id=map_id,
                revision_no=number,
                state=RevisionState.DRAFT.value,
                base_revision_id=source.id,
                schema_version=source.schema_version,
                world_json=copy.deepcopy(source.world_json),
                world_hash=source.world_hash,
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            public_map.current_draft_revision_id = draft.id
            public_map.status = RevisionState.DRAFT.value
            public_map.row_version += 1
            public_map.updated_at = now
            return self._revision_detail(draft, public_map)

    def list_revisions(self, map_id: str) -> list[dict[str, Any]]:
        """查询`revisions`。

        参数:
            map_id: 地图的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            revisions = list(
                session.scalars(
                    select(WorldMapRevision)
                    .where(WorldMapRevision.map_id == map_id)
                    .order_by(WorldMapRevision.revision_no.desc())
                )
            )
            return [
                self._revision_detail(item, public_map, include_world=False)
                for item in revisions
            ]

    def select_for_experiment(
        self,
        experiment_id: str,
        *,
        expected_lock_version: int,
        map_revision_id: str,
    ) -> dict[str, Any]:
        """执行 `WorldMapService` 的`select``for`实验操作。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。
            map_revision_id: 地图修订版本的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            revision = session.get(WorldMapRevision, map_revision_id)
            if revision is None or revision.state != RevisionState.PUBLISHED.value:
                raise not_found("map_revision", map_revision_id)
            world = self.materialize_world(revision)
        from .experiments import ExperimentService

        experiment_service = ExperimentService(self.database)
        draft = experiment_service.get_draft(experiment_id)
        payload = draft["definition"]
        payload["world"] = world.model_dump(mode="json", exclude_none=False)
        return experiment_service.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=expected_lock_version,
            definition=ExperimentDefinition.model_validate(payload),
            allow_resource_reselection=True,
        )

    def materialize_for_publish_in_session(
        self, session: Session, world: WorldConfig
    ) -> WorldConfig:
        """执行 `WorldMapService` 的`materialize``for``publish``in``session`操作。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            world: 当前运行使用的世界配置或运行时世界对象。 类型：`WorldConfig`。

        返回:
            返回 `WorldConfig` 类型的处理结果。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        if not world.map_revision_id:
            return world
        revision = session.get(WorldMapRevision, world.map_revision_id)
        if (
            revision is None
            or revision.state != RevisionState.PUBLISHED.value
            or revision.map_id != world.map_id
            or revision.world_hash != world.map_revision_hash
        ):
            raise ServiceError(
                "MAP_REVISION_CONFLICT",
                "实验引用的公共地图版本已失效",
                status_code=409,
            )
        return self.materialize_world(revision)

    @staticmethod
    def materialize_world(revision: WorldMapRevision) -> WorldConfig:
        """Materialize one immutable public map Revision for an experiment."""
        base = normalize_public_world(revision.world_json)
        return WorldConfig(
            world_key=base.world_key,
            world_name=base.world_name,
            definition=copy.deepcopy(base.definition),
            assets=list(base.assets),
            map_id=revision.map_id,
            map_revision_id=revision.id,
            map_revision_hash=revision.world_hash,
        )

    def _require_draft(
        self, session: Session, map_id: str
    ) -> tuple[WorldMap, WorldMapRevision]:
        """执行`require``draft`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            map_id: 地图的唯一标识。 类型：`str`。

        返回:
            返回按接口约定组织的结果集合。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        public_map = session.get(WorldMap, map_id)
        if public_map is None:
            raise not_found("map", map_id)
        revision = (
            session.get(WorldMapRevision, public_map.current_draft_revision_id)
            if public_map.current_draft_revision_id
            else None
        )
        if (
            revision is None
            or revision.map_id != map_id
            or revision.state != RevisionState.DRAFT.value
        ):
            raise ServiceError(
                "MAP_DRAFT_UNAVAILABLE", "地图没有可编辑草稿", status_code=409
            )
        return public_map, revision

    def _usage_experiment_ids(self, session: Session, map_id: str) -> set[str]:
        """执行`usage`实验`ids`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            map_id: 地图的唯一标识。 类型：`str`。

        返回:
            返回按接口约定组织的结果集合。
        """
        result: set[str] = set()
        revisions = session.execute(
            select(ExperimentRevision.experiment_id, ExperimentRevision.definition_json)
        )
        for experiment_id, payload in revisions:
            if ((payload or {}).get("world") or {}).get("map_id") == map_id:
                result.add(experiment_id)
        return result

    def _map_detail(self, session: Session, public_map: WorldMap) -> dict[str, Any]:
        """执行地图`detail`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            public_map: 传入当前算法的`public``map`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`WorldMap`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        draft = (
            session.get(WorldMapRevision, public_map.current_draft_revision_id)
            if public_map.current_draft_revision_id
            else None
        )
        published = (
            session.get(WorldMapRevision, public_map.current_published_revision_id)
            if public_map.current_published_revision_id
            else None
        )
        source = draft or published
        world = WorldConfig.model_validate(source.world_json) if source else None
        definition = world.definition if world else {}
        size = definition.get("size") if isinstance(definition, dict) else None
        return {
            "id": public_map.id,
            "map_key": public_map.map_key,
            "name": public_map.name,
            "description": public_map.description,
            "status": public_map.status,
            "row_version": public_map.row_version,
            "archived_at": iso_utc(public_map.archived_at)
            if public_map.archived_at
            else None,
            "current_draft": self._revision_summary(draft),
            "current_published": self._revision_summary(published),
            "usage_count": len(self._usage_experiment_ids(session, public_map.id)),
            "dimensions": size if isinstance(size, list) else None,
            "tile_size": definition.get("tile_size")
            if isinstance(definition, dict)
            else None,
            "updated_at": iso_utc(public_map.updated_at),
            "created_at": iso_utc(public_map.created_at),
        }

    @staticmethod
    def _revision_summary(revision: WorldMapRevision | None) -> dict[str, Any] | None:
        """执行修订版本摘要的内部处理，供当前模块或类复用。

        参数:
            revision: 当前读取、发布、克隆或校验的修订版本记录。 类型：`WorldMapRevision | None`。

        返回:
            返回以字段名或业务键组织的结构化映射。 没有可用结果时返回 `None`。
        """
        if revision is None:
            return None
        return {
            "id": revision.id,
            "revision_no": revision.revision_no,
            "state": revision.state,
            "world_hash": revision.world_hash,
            "lock_version": revision.lock_version,
            "updated_at": iso_utc(revision.updated_at),
            "published_at": iso_utc(revision.published_at)
            if revision.published_at
            else None,
        }

    def _revision_detail(
        self,
        revision: WorldMapRevision,
        public_map: WorldMap,
        *,
        include_world: bool = True,
    ) -> dict[str, Any]:
        """执行修订版本`detail`的内部处理，供当前模块或类复用。

        参数:
            revision: 当前读取、发布、克隆或校验的修订版本记录。 类型：`WorldMapRevision`。
            public_map: 传入当前算法的`public``map`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`WorldMap`。
            include_world: 是否启用世界相关处理。 类型：`bool`。 默认值：`True`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        result = self._revision_summary(revision) or {}
        result.update(
            {
                "map_id": public_map.id,
                "map_key": public_map.map_key,
                "map_name": public_map.name,
                "base_revision_id": revision.base_revision_id,
                "schema_version": revision.schema_version,
                "validation": revision.validation_json,
            }
        )
        if include_world:
            result["world"] = revision.world_json
        return result
