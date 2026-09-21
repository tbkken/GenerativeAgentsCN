"""可直接编辑的公共地图，以及实验发布时的自包含快照编译。"""

from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime, timezone
from math import ceil
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session
from pydantic import ValidationError

from generative_agents.ga_protocol.packages.hashing import canonical_json_bytes
from generative_agents.ga_studio.resources.map_document import MapEditorDocumentV2
from generative_agents.ga_protocol.spatial.navigation import compile_collision
from generative_agents.ga_protocol.schemas.experiment import WorldConfig
from generative_agents.ga_protocol.schemas.spatial_assets import SpatialAssetContract
from generative_agents.ga_protocol.schemas.spatial_assets import SpatialSceneExtension
from generative_agents.ga_studio.storage.database import Database
from generative_agents.ga_studio.storage.models import SpatialAssetDefinition
from generative_agents.ga_studio.storage.models import WorldMap
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_protocol.skills.documents import SkillRegistryError
from generative_agents.ga_studio.resources.errors import ServiceError
from generative_agents.ga_studio.resources.errors import not_found
from generative_agents.ga_studio.resources.timestamps import iso_utc


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
    payload["definition"] = copy.deepcopy(payload.get("definition") or {})
    payload["definition"]["size_unit"] = "TILE"
    if payload["definition"].get("editor_v2"):
        try:
            document = MapEditorDocumentV2.model_validate(payload["definition"]["editor_v2"])
            payload["definition"]["editor_v2"] = document.model_dump(mode="json")
            compile_collision(payload["definition"])
        except ValueError as exc:
            raise ServiceError("MAP_NAVIGATION_INVALID", str(exc), status_code=422) from exc
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
            "住宅建筑本体；可被感知，但不绑定对象 Skill。",
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
    assets_by_key = {asset.asset_key: asset for asset in assets}
    if set(assets_by_key) != required_asset_keys:
        raise ServiceError(
            "MAP_BLUEPRINT_ASSET_UNAVAILABLE",
            "两日通勤蓝图依赖的地图资产不完整",
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
    module_map_id = module_map.id if module_map is not None else None
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
                "spatial_asset_id": assets_by_key[asset_key].id,
                "x_tiles": x,
                "y_tiles": y,
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
                "source_map_id": module_map_id,
                "center": [cx, 28],
                "rotation_degrees": 0,
            }
        )
        intersections.append(
            {
                "intersection_key": key.casefold(),
                "center": [cx, 28],
                "lanes_per_direction": 3,
                "lane_width_tiles": 1.0,
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
        "lane_width_tiles": 1.0,
        "intersection_instances": intersections,
        "crosswalk_count": len(intersections) * 4,
    }
    definition["spatial_scene"] = {
        "schema_version": "ga-spatial-scene/v2",
        "palette_refs": {
            "ground": assets_by_key["tile-ground"].id,
            "road": assets_by_key["tile-road-asphalt"].id,
            "sidewalk": assets_by_key["tile-sidewalk"].id,
            "crosswalk": assets_by_key["marking-crosswalk"].id,
        },
        "placements": placements,
    }
    definition["editor"] = {
        "schema_version": 1,
        "palette": palette,
        "cells": cells,
        "spatial_assets": {
            asset.id: copy.deepcopy(asset.contract_json)
            for asset in assets_by_key.values()
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
        width: 地图宽度，单位为 Tile 格数。 类型：`int`。
        height: 地图高度，单位为 Tile 格数。 类型：`int`。
        tile_size: 每个 Tile 的像素边长。 类型：`int`。

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
                "size_unit": "TILE",
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
    if definition.get("size_unit") != "TILE":
        errors.append(
            {
                "code": "WORLD_SIZE_UNIT_INVALID",
                "path": "definition.size_unit",
                "message": "地图尺寸单位必须是 TILE；1 表示一个 Tile",
            }
        )
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
    max_x = width
    max_y = height
    for palette_key, asset_id in scene.palette_refs.items():
        asset = session.get(SpatialAssetDefinition, asset_id)
        kind = (asset.contract_json or {}).get("kind") if asset else None
        if asset is None:
            errors.append(
                {
                    "code": "SPATIAL_ASSET_UNAVAILABLE",
                    "path": f"definition.spatial_scene.palette_refs.{palette_key}",
                    "message": "画块引用的空间资产不存在",
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
        asset = session.get(SpatialAssetDefinition, placement.spatial_asset_id)
        if asset is None:
            errors.append(
                {
                    "code": "SPATIAL_ASSET_UNAVAILABLE",
                    "path": f"definition.spatial_scene.placements.{index}.spatial_asset_id",
                    "message": "地图物件引用的空间资产不存在",
                }
            )
            continue
        kind = (asset.contract_json or {}).get("kind")
        if kind == "TILE":
            errors.append(
                {
                    "code": "SPATIAL_PLACEMENT_KIND_INVALID",
                    "path": f"definition.spatial_scene.placements.{index}",
                    "message": "TILE 资产应通过画块调色板使用，不能作为物件放置",
                }
            )
        try:
            contract = SpatialAssetContract.model_validate(asset.contract_json)
        except ValidationError:
            contract = None
        if contract is not None:
            errors.extend(
                _validate_object_skill_bindings(
                    contract.skill_bindings,
                    path=f"definition.spatial_scene.placements.{index}",
                    registry=skill_registry,
                )
            )
        width_tiles = contract.physics.width_tiles if contract is not None else 1.0
        height_tiles = contract.physics.height_tiles if contract is not None else 1.0
        left = placement.x_tiles + 0.5 - width_tiles / 2.0
        right = placement.x_tiles + 0.5 + width_tiles / 2.0
        top = placement.y_tiles + 0.5 - height_tiles / 2.0
        bottom = placement.y_tiles + 0.5 + height_tiles / 2.0
        if not (left >= 0 and top >= 0 and right <= max_x and bottom <= max_y):
            errors.append(
                {
                    "code": "SPATIAL_PLACEMENT_OUT_OF_BOUNDS",
                    "path": f"definition.spatial_scene.placements.{index}",
                    "message": "地图物件坐标超出 Tile 网格边界",
                }
            )
    return errors


def _referenced_spatial_asset_ids(world: WorldConfig) -> set[str]:
    raw_scene = world.definition.get("spatial_scene")
    if not isinstance(raw_scene, dict):
        return set()
    ids = {str(value) for value in (raw_scene.get("palette_refs") or {}).values()}
    ids.update(
        str(item.get("spatial_asset_id"))
        for item in raw_scene.get("placements") or []
        if isinstance(item, dict) and item.get("spatial_asset_id")
    )
    return ids


def _hydrate_spatial_assets(session: Session, world: WorldConfig) -> WorldConfig:
    """Resolve mutable authoring asset ids into a self-contained world document."""
    payload = world.model_dump(mode="json", exclude_none=False)
    definition = payload["definition"]
    editor = definition.setdefault("editor", {})
    asset_ids = _referenced_spatial_asset_ids(world)
    rows = session.scalars(
        select(SpatialAssetDefinition).where(SpatialAssetDefinition.id.in_(asset_ids))
    ) if asset_ids else ()
    editor["spatial_assets"] = {
        asset.id: copy.deepcopy(asset.contract_json) for asset in rows
    }
    return WorldConfig.model_validate(payload)


def _validate_object_skill_bindings(
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
                    "code": "GAME_OBJECT_SKILL_INVALID_KIND",
                    "path": f"{binding_path}.skill_name",
                    "message": (
                        f"Game Object 不能绑定 Brain Skill {binding.skill_name}；"
                        "请绑定以自然语言描述对象行为的 atomic 或 pack Skill"
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
            _validate_object_skill_bindings(
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
                    f"地图包含 {len(game_objects)} 个 Game Object，但没有任何对象绑定对象 Skill；"
                    "Agent 只能感知这些对象，不能与其交互。"
                ),
            }
        )
    return errors, warnings


class WorldMapService:
    """管理可直接编辑的地图，并在实验发布时编译完整快照。"""

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
        source_map_id: str | None = None,
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
            source_map_id: 需要复制的源地图标识。 类型：`str | None`。 默认值：`None`。
            blueprint_key: 用于稳定定位`blueprint`的键。 类型：`str | None`。 默认值：`None`。
            map_key: 用于稳定定位地图的键。 类型：`str | None`。 默认值：`None`。
            width: 地图宽度，单位为 Tile 格数。 类型：`int`。 默认值：`48`。
            height: 地图高度，单位为 Tile 格数。 类型：`int`。 默认值：`32`。
            tile_size: 每个 Tile 的像素边长。 类型：`int`。 默认值：`32`。

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
        if not (1 <= width <= 240 and 1 <= height <= 240 and 8 <= tile_size <= 128):
            raise ServiceError(
                "INVALID_MAP_DIMENSIONS",
                "地图宽高必须是 1–240 的整数格数；1 表示一个 Tile。Tile 尺寸需在 8–128 像素之间",
                status_code=422,
            )
        if source_map_id and blueprint_key:
            raise ServiceError(
                "MAP_CREATE_SOURCE_CONFLICT",
                "复制地图与使用构建蓝图不能同时选择",
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
            source_map = session.get(WorldMap, source_map_id) if source_map_id else None
            if source_map_id:
                if source_map is None:
                    raise not_found("map", source_map_id)
                world = normalize_public_world(source_map.world_json)
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
                schema_version=1,
                world_json=world.model_dump(mode="json", exclude_none=False),
                world_hash=world_hash(world),
                validation_json=None,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(public_map)
            session.flush()
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
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            if public_map.row_version != expected_lock_version:
                raise ServiceError(
                    "MAP_CONFLICT",
                    "地图已变化，请重新载入后继续构建",
                    status_code=409,
                    details={
                        "expected_lock_version": expected_lock_version,
                        "actual_lock_version": public_map.row_version,
                    },
                )
            current_world = WorldConfig.model_validate(public_map.world_json)
            editor = current_world.definition.get("editor") or {}
            guide = editor.get("build_guide") or {}
            blueprint_key = guide.get("blueprint_key")
            if blueprint_key != "two-day-commute":
                raise ServiceError(
                    "MAP_BLUEPRINT_NOT_ATTACHED",
                    "当前地图没有两日通勤构建向导",
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
            result = session.execute(update(WorldMap).where(
                WorldMap.id == map_id,
                WorldMap.row_version == expected_lock_version,
            ).values(
                world_json=world.model_dump(mode="json", exclude_none=False),
                world_hash=digest,
                validation_json=None,
                row_version=WorldMap.row_version + 1,
                updated_at=now,
            ))
            if result.rowcount != 1:
                raise ServiceError(
                    "MAP_CONFLICT",
                    "地图已变化，请重新载入后继续构建",
                    status_code=409,
                )
            session.flush()
            return self._map_detail(session, session.get(WorldMap, map_id))

    def list_maps(
        self,
        *,
        query: str | None = None,
        page: int = 1,
        page_size: int = 5,
        archived: str = "active",
    ) -> dict[str, Any]:
        """查询`maps`。

        参数:
            query: 用于名称、正文或标识模糊匹配的搜索文本。 类型：`str | None`。 默认值：`None`。
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
        with self.database.session_factory() as session:
            statement = select(WorldMap)
            count_statement = select(func.count()).select_from(WorldMap)
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
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                predicate = or_(
                    WorldMap.name.ilike(pattern), WorldMap.map_key.ilike(pattern),
                    WorldMap.description.ilike(pattern),
                )
                statement = statement.where(predicate)
                count_statement = count_statement.where(predicate)
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
                "status_counts": {"ALL": total},
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

    def update_map(
        self,
        map_id: str,
        *,
        expected_lock_version: int,
        world: WorldConfig | dict[str, Any],
    ) -> dict[str, Any]:
        """直接更新地图当前内容。

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
            result = session.execute(update(WorldMap).where(
                WorldMap.id == map_id,
                WorldMap.row_version == expected_lock_version,
            ).values(
                world_json=normalized.model_dump(mode="json", exclude_none=False),
                world_hash=digest,
                validation_json=None,
                row_version=WorldMap.row_version + 1,
                updated_at=now,
            ))
            if result.rowcount != 1:
                if session.get(WorldMap, map_id) is None:
                    raise not_found("map", map_id)
                actual = session.scalar(select(WorldMap.row_version).where(
                    WorldMap.id == map_id
                ))
                raise ServiceError(
                    "MAP_CONFLICT",
                    "地图已被其他请求修改，请重新载入",
                    status_code=409,
                    details={
                        "expected_lock_version": expected_lock_version,
                        "actual_lock_version": actual,
                    },
                )
            session.flush()
            return self._map_detail(session, session.get(WorldMap, map_id))

    def validate_map(
        self,
        map_id: str,
        *,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        """校验当前地图；校验不会把地图变成只读资源。

        参数:
            map_id: 地图的唯一标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        if hasattr(self.skill_registry, "ensure_builtin_skills"):
            self.skill_registry.ensure_builtin_skills()
        with self.database.session_factory.begin() as session:
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            if public_map.row_version != expected_lock_version:
                raise ServiceError(
                    "MAP_CONFLICT",
                    "地图已变化，请重新载入",
                    status_code=409,
                )
            world = _hydrate_spatial_assets(
                session, normalize_public_world(public_map.world_json)
            )
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
                    "message": "四层空间层级、Game Object 与对象 Skill 引用",
                    "status": "FAILED" if editor_errors else "PASSED",
                },
                {
                    "code": "WORLD_TILE_GRID",
                    "message": "地图尺寸、Tile 坐标、碰撞与语义地址",
                    "status": "FAILED" if world_errors else "PASSED",
                },
                {
                    "code": "SPATIAL_SCENE_CONTRACTS",
                    "message": "空间资产、放置和初始状态合同",
                    "status": "FAILED" if spatial_errors else "PASSED",
                },
            ]
            public_map.validation_json = {
                "valid": not errors,
                "errors": errors,
                "warnings": editor_warnings,
                "checks": checks,
            }
            public_map.updated_at = _utc_now()
            session.flush()
            return self._map_detail(session, public_map)


    @staticmethod
    def materialize_world(session: Session, public_map: WorldMap) -> WorldConfig:
        """Resolve the current map and asset contracts for a draft or snapshot."""
        base = _hydrate_spatial_assets(
            session, normalize_public_world(public_map.world_json)
        )
        return WorldConfig(
            world_key=base.world_key,
            world_name=base.world_name,
            definition=copy.deepcopy(base.definition),
            assets=list(base.assets),
            map_id=public_map.id,
        )

    def _usage_experiment_ids(self, session: Session, map_id: str) -> set[str]:
        """Public Maps have no live experiment references in the package model."""

        return set()

    def _map_detail(self, session: Session, public_map: WorldMap) -> dict[str, Any]:
        """执行地图`detail`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            public_map: 传入当前算法的`public``map`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`WorldMap`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        world = _hydrate_spatial_assets(
            session, normalize_public_world(public_map.world_json)
        )
        definition = world.definition
        size = definition.get("size") if isinstance(definition, dict) else None
        return {
            "id": public_map.id,
            "map_key": public_map.map_key,
            "name": public_map.name,
            "description": public_map.description,
            "row_version": public_map.row_version,
            "lock_version": public_map.row_version,
            "world_hash": world_hash(world),
            "validation": copy.deepcopy(public_map.validation_json),
            "world": world.model_dump(mode="json", exclude_none=False),
            "archived_at": iso_utc(public_map.archived_at)
            if public_map.archived_at
            else None,
            "usage_count": len(self._usage_experiment_ids(session, public_map.id)),
            "dimensions": size if isinstance(size, list) else None,
            "tile_size": definition.get("tile_size")
            if isinstance(definition, dict)
            else None,
            "updated_at": iso_utc(public_map.updated_at),
            "created_at": iso_utc(public_map.created_at),
        }

    def materialize_validated_world(
        self, session: Session, map_id: str
    ) -> WorldConfig:
        """Read and validate the selected author map for one-time experiment import."""
        if not map_id:
            raise ServiceError("MAP_REQUIRED", "实验必须选择地图", status_code=422)
        public_map = session.get(WorldMap, map_id)
        if public_map is None:
            raise ServiceError(
                "MAP_UNAVAILABLE", "实验选择的地图已不存在", status_code=409
            )
        snapshot = self.materialize_world(session, public_map)
        editor_errors, editor_warnings = _validate_map_editor_v2(
            snapshot, skill_registry=self.skill_registry
        )
        if not editor_errors:
            snapshot = _compile_editor_v2_runtime_addresses(snapshot)
        errors = [
            *_validate_world_definition(snapshot),
            *_validate_spatial_scene(
                session, snapshot, skill_registry=self.skill_registry
            ),
            *editor_errors,
        ]
        if errors:
            raise ServiceError(
                "MAP_VALIDATION_FAILED",
                "地图当前内容无法用于实验",
                status_code=422,
                details={"errors": errors, "warnings": editor_warnings},
            )
        return snapshot
