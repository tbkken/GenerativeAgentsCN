"""Reusable public map lifecycle and experiment-owned map overlays."""

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

from generative_agents.config import canonical_json_bytes, make_builtin_definition
from generative_agents.config.map_editor import MapEditorDocumentV2
from generative_agents.config.schema import WorldConfig, WorldOverlayConfig
from generative_agents.config.spatial_assets import SpatialAssetContract, SpatialSceneExtension
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    ExperimentRevision,
    SpatialAssetDefinition,
    SpatialAssetRevision,
    WorldMap,
    WorldMapRevision,
)
from generative_agents.skills import SkillRegistry, SkillRegistryError

from .errors import ServiceError, not_found


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_key(name: str) -> str:
    ascii_key = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return f"{(ascii_key[:48].strip('-') or 'map')}-{uuid4().hex[:8]}"


def _merge_patch(document: Any, patch: Any) -> Any:
    """Apply RFC-7396 style merge semantics without mutating either input."""

    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(document) if isinstance(document, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = _merge_patch(result.get(key), value)
    return result


def normalize_public_world(world: WorldConfig | dict[str, Any]) -> WorldConfig:
    payload = WorldConfig.model_validate(world).model_dump(mode="json", exclude_none=False)
    payload.update(
        {
            "map_id": None,
            "map_revision_id": None,
            "map_revision_hash": None,
            "overlay": WorldOverlayConfig().model_dump(mode="json", exclude_none=False),
        }
    )
    return WorldConfig.model_validate(payload)


def world_hash(world: WorldConfig | dict[str, Any]) -> str:
    normalized = normalize_public_world(world)
    return hashlib.sha256(
        canonical_json_bytes(normalized.model_dump(mode="json", exclude_none=False))
    ).hexdigest()


def _compile_editor_v2_runtime_addresses(world: WorldConfig) -> WorldConfig:
    """Compile the authoring hierarchy into the Tile addresses used by ``Maze``.

    The map workspace edits ``definition.editor_v2.hierarchy_nodes`` while the
    simulation indexes ``definition.tiles[*].address``.  Keeping those two
    representations disconnected makes a published tree look correct in the
    editor but leaves Agents navigating the previous addresses.  Publication
    is the contract boundary where the immutable runtime grid is rebuilt.

    Sibling bounds may overlap (for example a narrow crossing Arena on top of
    a road Arena).  The smallest containing child is the most specific spatial
    definition and therefore wins; ``sort_order`` and ``id`` make ties stable.
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
        tile["address"] = path
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
            {"step": 3, "key": "intersection-a", "name": "三车道路口 A", "tool": "模块"},
            {"step": 4, "key": "intersection-b", "name": "复制三车道路口 B", "tool": "模块"},
            {"step": 5, "key": "pedestrian-network", "name": "人行道与步行网络", "tool": "人行"},
            {"step": 6, "key": "signals", "name": "8 个信号灯与等待区", "tool": "信号灯"},
            {"step": 7, "key": "facilities", "name": "车辆门禁与 P01–P03", "tool": "设施"},
            {"step": 8, "key": "semantics", "name": "空间语义与导航校验", "tool": "语义"},
        ],
    },
)


def _map_blueprint(key: str) -> dict[str, Any] | None:
    return next((copy.deepcopy(item) for item in MAP_BLUEPRINTS if item["key"] == key), None)


def _commute_blueprint_world(
    session: Session,
    *,
    name: str,
    stable_key: str,
    step: int,
) -> WorldConfig:
    """Materialize one deterministic step of the approved commute-map build guide."""

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
        revision is None or revision.state != "PUBLISHED"
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
        {"id": "office-zone", "name": "公司园区", "color": "#c9ded7", "collision": False},
        {"id": "building", "name": "建筑", "color": "#f0e4cf", "collision": True},
        {"id": "road", "name": "六车道道路", "color": "#53605d", "collision": False},
        {"id": "sidewalk", "name": "人行道", "color": "#d5ddd7", "collision": False},
        {"id": "crosswalk", "name": "斑马线", "color": "#f7faf8", "collision": False},
        {"id": "parking", "name": "停车区域", "color": "#b7d8cc", "collision": False},
    ]
    cells: dict[str, dict[str, str]] = {
        f"{x},{y}": {"kind": "ground"}
        for y in range(height)
        for x in range(width)
    }
    tiles = {
        tuple(tile["coord"]): tile
        for tile in definition["tiles"]
    }

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
        paint(cx - 3, 0, cx + 2, height - 1, "road", address=["城市道路", f"三车道路口 {key}"])
        paint(cx - 3, 21, cx + 2, 23, "crosswalk", address=["城市道路", f"三车道路口 {key}", "北侧斑马线"])
        paint(cx - 3, 32, cx + 2, 34, "crosswalk", address=["城市道路", f"三车道路口 {key}", "南侧斑马线"])
        paint(cx - 6, 25, cx - 4, 30, "crosswalk", address=["城市道路", f"三车道路口 {key}", "西侧斑马线"])
        paint(cx + 3, 25, cx + 5, 30, "crosswalk", address=["城市道路", f"三车道路口 {key}", "东侧斑马线"])
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
                "crosswalk_keys": [f"{key.casefold()}-{side}" for side in ("north", "east", "south", "west")],
            }
        )

    if step >= 1:
        paint(3, 37, 18, 52, "home-zone", address=["住宅区", "林晨住宅"])
        paint(7, 41, 15, 49, "building", address=["住宅区", "林晨住宅", "住宅建筑"], collision=True)
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
                "route": ["home.driveway", "intersection.a", "intersection.b", "office.gate", "parking.P03"],
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
                    {"state": "VEHICLE_GREEN" if index % 2 else "VEHICLE_RED", "phase": "VEHICLE_GREEN" if index % 2 else "VEHICLE_RED"},
                )
            for side, x, y in (
                ("north", cx, 20), ("east", cx + 7, 28),
                ("south", cx, 36), ("west", cx - 7, 28),
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
            "intersection_waiting_zones": ["a-wait-north", "a-wait-east", "a-wait-south", "a-wait-west", "b-wait-north", "b-wait-east", "b-wait-south", "b-wait-west"],
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
    return WorldConfig.model_validate(world)


def _blank_public_world(
    *, name: str, stable_key: str, width: int, height: int, tile_size: int
) -> WorldConfig:
    """Create a complete editable grid instead of a dimensionless placeholder."""

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
    """Validate the subset consumed directly by ``Maze`` before publication."""

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
        or any(not isinstance(value, str) or not value.strip() for value in address_keys)
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
    session: Session, world: WorldConfig
) -> list[dict[str, str]]:
    """Validate the opt-in spatial extension without changing legacy map hashes."""

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
        if revision is None or revision.state != "PUBLISHED":
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
        and key not in {
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
        revision = session.get(SpatialAssetRevision, placement.spatial_asset_revision_id)
        if revision is None or revision.state != "PUBLISHED":
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


def _validate_passive_skill_bindings(bindings, *, path: str) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    registry = SkillRegistry()
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
        if document.kind != "atomic" or "scripts/main.py" not in document.scripts:
            errors.append(
                {
                    "code": "GAME_OBJECT_SKILL_NOT_PASSIVE",
                    "path": f"{binding_path}.skill_name",
                    "message": (
                        f"Game Object Skill {binding.skill_name} 必须是包含 "
                        "scripts/main.py 的 atomic Skill"
                    ),
                }
            )
    return errors


def _validate_map_editor_v2(world: WorldConfig) -> list[dict[str, str]]:
    raw_document = world.definition.get("editor_v2")
    if raw_document is None:
        return []
    try:
        document = MapEditorDocumentV2.model_validate(raw_document)
    except ValidationError as exc:
        return [
            {
                "code": "MAP_EDITOR_V2_INVALID",
                "path": "definition.editor_v2",
                "message": item["msg"],
            }
            for item in exc.errors(include_url=False)
        ]
    errors: list[dict[str, str]] = []
    for index, node in enumerate(document.hierarchy_nodes):
        errors.extend(
            _validate_passive_skill_bindings(
                node.skill_bindings,
                path=f"definition.editor_v2.hierarchy_nodes.{index}",
            )
        )
    return errors


class WorldMapService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def list_blueprints() -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in MAP_BLUEPRINTS]

    def ensure_builtin_map(self) -> dict[str, Any]:
        """Register the bundled town once and keep its first public revision immutable."""

        with self.database.session_factory.begin() as session:
            existing = session.scalar(select(WorldMap).where(WorldMap.map_key == "the-ville"))
            if existing is not None:
                return self._map_detail(session, existing)
            now = _utc_now()
            map_id = str(uuid4())
            public_map = WorldMap(
                id=map_id,
                map_key="the-ville",
                name="the Ville 标准小镇",
                description="内置公共地图，可被多个实验引用并在实验内独立微调。",
                status="DRAFT",
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(public_map)
            session.flush()
            world = normalize_public_world(
                make_builtin_definition(
                    key="builtin-map-catalog",
                    name="公共地图目录",
                ).world
            )
            payload = world.model_dump(mode="json", exclude_none=False)
            digest = world_hash(world)
            published = WorldMapRevision(
                id=str(uuid4()),
                map_id=map_id,
                revision_no=1,
                state="PUBLISHED",
                schema_version=1,
                world_json=payload,
                world_hash=digest,
                validation_json={"valid": True, "errors": [], "warnings": []},
                lock_version=1,
                created_at=now,
                updated_at=now,
                published_at=now,
            )
            session.add(published)
            session.flush()
            draft = WorldMapRevision(
                id=str(uuid4()),
                map_id=map_id,
                revision_no=2,
                state="DRAFT",
                base_revision_id=published.id,
                schema_version=1,
                world_json=copy.deepcopy(payload),
                world_hash=digest,
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            public_map.current_published_revision_id = published.id
            public_map.current_draft_revision_id = draft.id
            return self._map_detail(session, public_map)

    def ensure_intersection_map(self) -> dict[str, Any]:
        """Seed a reusable four-way, three-lane-per-direction test intersection."""

        with self.database.session_factory.begin() as session:
            existing = session.scalar(
                select(WorldMap).where(WorldMap.map_key == "standard-3lane-intersection")
            )
            asset_keys = {
                "tile-ground",
                "tile-road-asphalt",
                "tile-sidewalk",
                "marking-crosswalk",
                "object-traffic-light",
                "zone-pedestrian-wait",
                "marking-vehicle-stop-line",
            }
            assets = list(
                session.scalars(
                    select(SpatialAssetDefinition).where(
                        SpatialAssetDefinition.asset_key.in_(asset_keys)
                    )
                )
            )
            revisions = {
                item.asset_key: session.get(
                    SpatialAssetRevision, item.current_published_revision_id
                )
                for item in assets
                if item.current_published_revision_id
            }
            if set(revisions) != asset_keys or any(
                item is None for item in revisions.values()
            ):
                raise RuntimeError("intersection spatial assets are unavailable")

            size = 48
            road_min, road_max = 15, 32
            crosswalk_bands = (
                lambda x, y: (
                    road_min <= x <= road_max and y in {12, 13, 14, 33, 34, 35}
                )
                or (
                    road_min <= y <= road_max and x in {12, 13, 14, 33, 34, 35}
                )
            )
            tiles: list[dict[str, Any]] = []
            for y in range(size):
                for x in range(size):
                    if crosswalk_bands(x, y):
                        tile_key = "crosswalk"
                    elif road_min <= x <= road_max or road_min <= y <= road_max:
                        tile_key = "road"
                    elif x in {13, 14, 33, 34} or y in {13, 14, 33, 34}:
                        tile_key = "sidewalk"
                    else:
                        tile_key = "ground"
                    tiles.append(
                        {
                            "coord": [x, y],
                            "collision": False,
                            "address": ["标准路口", "公共道路"],
                            "tile": tile_key,
                        }
                    )

            def placement(
                key: str,
                asset_key: str,
                x: float,
                y: float,
                rotation: float = 0,
                state: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                return {
                    "instance_key": key,
                    "spatial_asset_revision_id": revisions[asset_key].id,
                    "x_m": x,
                    "y_m": y,
                    "rotation_degrees": rotation,
                    "state_overrides": state or {},
                }

            placements = [
                placement(
                    "signal-north",
                    "object-traffic-light",
                    14,
                    14,
                    0,
                    {"state": "VEHICLE_RED", "phase": "VEHICLE_RED"},
                ),
                placement(
                    "signal-east",
                    "object-traffic-light",
                    34,
                    14,
                    90,
                    {"state": "VEHICLE_GREEN", "phase": "VEHICLE_GREEN"},
                ),
                placement(
                    "signal-south",
                    "object-traffic-light",
                    34,
                    34,
                    180,
                    {"state": "VEHICLE_RED", "phase": "VEHICLE_RED"},
                ),
                placement(
                    "signal-west",
                    "object-traffic-light",
                    14,
                    34,
                    270,
                    {"state": "VEHICLE_GREEN", "phase": "VEHICLE_GREEN"},
                ),
                placement("wait-north", "zone-pedestrian-wait", 24, 11),
                placement("wait-east", "zone-pedestrian-wait", 36, 24),
                placement("wait-south", "zone-pedestrian-wait", 24, 36),
                placement("wait-west", "zone-pedestrian-wait", 11, 24),
                placement("stop-north", "marking-vehicle-stop-line", 24, 12, 0),
                placement("stop-east", "marking-vehicle-stop-line", 35, 24, 90),
                placement("stop-south", "marking-vehicle-stop-line", 24, 35, 180),
                placement("stop-west", "marking-vehicle-stop-line", 12, 24, 270),
            ]
            world = WorldConfig.model_validate(
                {
                    "world_key": "standard-3lane-intersection",
                    "world_name": "标准四向三车道路口",
                    "definition": {
                        "world": "标准路口",
                        "size": [size, size],
                        "tile_size": 16,
                        "tile_address_keys": [
                            "world",
                            "sector",
                            "arena",
                            "object",
                        ],
                        "traffic_layout": {
                            "intersection_type": "FOUR_WAY",
                            "approaches": ["NORTH", "EAST", "SOUTH", "WEST"],
                            "lanes_per_direction": 3,
                            "lane_width_m": 3.0,
                            "crosswalks": [
                                {
                                    "crosswalk_key": "north",
                                    "bounds_m": {"x": 15, "y": 12, "width": 18, "height": 3},
                                },
                                {
                                    "crosswalk_key": "east",
                                    "bounds_m": {"x": 33, "y": 15, "width": 3, "height": 18},
                                },
                                {
                                    "crosswalk_key": "south",
                                    "bounds_m": {"x": 15, "y": 33, "width": 18, "height": 3},
                                },
                                {
                                    "crosswalk_key": "west",
                                    "bounds_m": {"x": 12, "y": 15, "width": 3, "height": 18},
                                },
                            ],
                        },
                        "tiles": tiles,
                        "palette": [
                            {"key": "ground", "label": "基础地面", "color": "#c9d9bd", "collision": False},
                            {"key": "road", "label": "六车道道路", "color": "#53605d", "collision": False},
                            {"key": "sidewalk", "label": "人行道", "color": "#d5ddd7", "collision": False},
                            {"key": "crosswalk", "label": "斑马线", "color": "#f7faf8", "collision": False},
                        ],
                        "spatial_scene": {
                            "meters_per_tile": 1.0,
                            "palette_refs": {
                                "ground": revisions["tile-ground"].id,
                                "road": revisions["tile-road-asphalt"].id,
                                "sidewalk": revisions["tile-sidewalk"].id,
                                "crosswalk": revisions["marking-crosswalk"].id,
                            },
                            "placements": placements,
                        },
                        "editor": {
                            "schema_version": 1,
                            "palette": [
                                {"id": "ground", "name": "基础地面", "color": "#c9d9bd", "collision": False},
                                {"id": "road", "name": "六车道道路", "color": "#53605d", "collision": False},
                                {"id": "sidewalk", "name": "人行道", "color": "#d5ddd7", "collision": False},
                                {"id": "crosswalk", "name": "斑马线", "color": "#f7faf8", "collision": False},
                            ],
                            "cells": {
                                f"{item['coord'][0]},{item['coord'][1]}": {"kind": item["tile"]}
                                for item in tiles
                            },
                            "spatial_assets": {
                                revision.id: copy.deepcopy(revision.contract_json)
                                for revision in revisions.values()
                            },
                            "module_definition": {
                                "module_key": "standard-3lane-intersection",
                                "name": "标准四向三车道路口",
                                "lanes_per_direction": 3,
                                "crosswalk_count": 4,
                                "anchor": [24, 24],
                            },
                        },
                    },
                    "assets": [],
                }
            )
            errors = [
                *_validate_world_definition(world),
                *_validate_spatial_scene(session, world),
                *_validate_map_editor_v2(world),
            ]
            if errors:
                raise RuntimeError(f"invalid built-in intersection map: {errors}")
            now = _utc_now()
            payload = normalize_public_world(world).model_dump(
                mode="json", exclude_none=False
            )
            expected_hash = world_hash(world)
            if existing is not None:
                current = session.get(
                    WorldMapRevision, existing.current_published_revision_id
                )
                if current is not None and current.world_hash == expected_hash:
                    return self._map_detail(session, existing)
                revision_no = int(
                    session.scalar(
                        select(func.max(WorldMapRevision.revision_no)).where(
                            WorldMapRevision.map_id == existing.id
                        )
                    )
                    or 0
                ) + 1
                published = WorldMapRevision(
                    id=str(uuid4()),
                    map_id=existing.id,
                    revision_no=revision_no,
                    state="PUBLISHED",
                    base_revision_id=current.id if current else None,
                    schema_version=1,
                    world_json=payload,
                    world_hash=expected_hash,
                    validation_json={"valid": True, "errors": [], "warnings": []},
                    lock_version=1,
                    created_at=now,
                    updated_at=now,
                    published_at=now,
                )
                session.add(published)
                session.flush()
                existing.current_published_revision_id = published.id
                existing.row_version += 1
                existing.updated_at = now
                return self._map_detail(session, existing)
            public_map = WorldMap(
                id=str(uuid4()),
                map_key="standard-3lane-intersection",
                name="标准四向三车道路口",
                description="48m × 48m、每个方向三车道、四条人行横道与可感知交通设施的复用地图。",
                status="PUBLISHED",
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(public_map)
            session.flush()
            published = WorldMapRevision(
                id=str(uuid4()),
                map_id=public_map.id,
                revision_no=1,
                state="PUBLISHED",
                schema_version=1,
                world_json=payload,
                world_hash=expected_hash,
                validation_json={"valid": True, "errors": [], "warnings": []},
                lock_version=1,
                created_at=now,
                updated_at=now,
                published_at=now,
            )
            session.add(published)
            session.flush()
            public_map.current_published_revision_id = published.id
            return self._map_detail(session, public_map)

    def default_revision_id(self) -> str:
        """Return the published revision used by resource-first experiment creation."""

        with self.database.session_factory() as session:
            public_map = session.scalar(
                select(WorldMap).where(WorldMap.map_key == "the-ville")
            )
            if public_map is None or not public_map.current_published_revision_id:
                raise ServiceError(
                    "DEFAULT_MAP_UNAVAILABLE",
                    "the Ville 基准地图尚未初始化",
                    status_code=503,
                )
            return public_map.current_published_revision_id

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
            if session.scalar(select(WorldMap.id).where(WorldMap.map_key == stable_key)):
                raise ServiceError(
                    "MAP_KEY_CONFLICT", "地图稳定键已被使用", status_code=409
                )
            base_revision: WorldMapRevision | None = None
            if source_revision_id:
                base_revision = session.get(WorldMapRevision, source_revision_id)
                if base_revision is None or base_revision.state != "PUBLISHED":
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
                status="DRAFT",
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
                state="DRAFT",
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
        """Apply exactly the next persisted build-guide step to a map Draft."""

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
                    WorldMapRevision.state == "DRAFT",
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
        status: str | None = None,
        page: int = 1,
        page_size: int = 5,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ServiceError("INVALID_PAGINATION", "地图分页参数无效", status_code=422)
        normalized_status = status.upper() if status else None
        if normalized_status not in {None, "DRAFT", "PUBLISHED"}:
            raise ServiceError("INVALID_MAP_STATUS", "地图状态筛选无效", status_code=422)
        with self.database.session_factory() as session:
            statement = select(WorldMap)
            count_statement = select(func.count()).select_from(WorldMap)
            status_count_statement = select(WorldMap.status, func.count()).group_by(WorldMap.status)
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                predicate = or_(WorldMap.name.ilike(pattern), WorldMap.map_key.ilike(pattern))
                statement = statement.where(predicate)
                count_statement = count_statement.where(predicate)
                status_count_statement = status_count_statement.where(predicate)
            status_counts = {"DRAFT": 0, "PUBLISHED": 0}
            for item_status, item_count in session.execute(status_count_statement):
                status_counts[item_status] = int(item_count)
            status_counts["ALL"] = sum(status_counts.values())
            if normalized_status:
                statement = statement.where(WorldMap.status == normalized_status)
                count_statement = count_statement.where(WorldMap.status == normalized_status)
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

    def get_map(self, map_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            return self._map_detail(session, public_map)

    def get_draft(self, map_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            public_map, revision = self._require_draft(session, map_id)
            return self._revision_detail(revision, public_map)

    def get_revision(self, map_id: str, revision_id: str) -> dict[str, Any]:
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
        normalized = normalize_public_world(world)
        digest = world_hash(normalized)
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            public_map, revision = self._require_draft(session, map_id)
            result = session.execute(
                update(WorldMapRevision)
                .where(
                    WorldMapRevision.id == revision.id,
                    WorldMapRevision.state == "DRAFT",
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
            return self._revision_detail(session.get(WorldMapRevision, revision.id), public_map)

    def publish_draft(
        self,
        map_id: str,
        *,
        draft_revision_id: str,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            public_map, revision = self._require_draft(session, map_id)
            if revision.id != draft_revision_id or revision.lock_version != expected_lock_version:
                raise ServiceError(
                    "MAP_REVISION_CONFLICT",
                    "地图草稿已变化，请重新载入",
                    status_code=409,
                )
            world = normalize_public_world(revision.world_json)
            editor_errors = _validate_map_editor_v2(world)
            if not editor_errors:
                world = _compile_editor_v2_runtime_addresses(world)
            errors = _validate_world_definition(world)
            errors.extend(_validate_spatial_scene(session, world))
            errors.extend(editor_errors)
            if errors:
                revision.validation_json = {"valid": False, "errors": errors, "warnings": []}
                raise ServiceError(
                    "MAP_VALIDATION_FAILED",
                    "地图未通过发布校验",
                    status_code=422,
                    details=revision.validation_json,
                )
            now = _utc_now()
            revision.world_json = world.model_dump(mode="json", exclude_none=False)
            revision.world_hash = world_hash(world)
            revision.validation_json = {"valid": True, "errors": [], "warnings": []}
            revision.state = "PUBLISHED"
            revision.published_at = now
            revision.updated_at = now
            public_map.current_draft_revision_id = None
            public_map.current_published_revision_id = revision.id
            public_map.status = "PUBLISHED"
            public_map.row_version += 1
            public_map.updated_at = now
            session.flush()
            return self._revision_detail(revision, public_map)

    def fork_revision(self, map_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            if public_map.current_draft_revision_id:
                raise ServiceError("MAP_DRAFT_EXISTS", "该地图已有编辑中的草稿", status_code=409)
            source = session.get(WorldMapRevision, revision_id)
            if source is None or source.map_id != map_id or source.state != "PUBLISHED":
                raise not_found("map_revision", revision_id)
            number = int(
                session.scalar(
                    select(func.max(WorldMapRevision.revision_no)).where(
                        WorldMapRevision.map_id == map_id
                    )
                )
                or 0
            ) + 1
            now = _utc_now()
            draft = WorldMapRevision(
                id=str(uuid4()),
                map_id=map_id,
                revision_no=number,
                state="DRAFT",
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
            public_map.status = "DRAFT"
            public_map.row_version += 1
            public_map.updated_at = now
            return self._revision_detail(draft, public_map)

    def list_revisions(self, map_id: str) -> list[dict[str, Any]]:
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
            return [self._revision_detail(item, public_map, include_world=False) for item in revisions]

    def select_for_experiment(
        self,
        experiment_id: str,
        *,
        expected_lock_version: int,
        map_revision_id: str,
    ) -> dict[str, Any]:
        with self.database.session_factory() as session:
            revision = session.get(WorldMapRevision, map_revision_id)
            if revision is None or revision.state != "PUBLISHED":
                raise not_found("map_revision", map_revision_id)
            world = self.materialize_world(revision, WorldOverlayConfig())
        from .experiments import ExperimentService

        return ExperimentService(self.database).patch_draft_section(
            experiment_id=experiment_id,
            section="world",
            expected_lock_version=expected_lock_version,
            data=world.model_dump(mode="json", exclude_none=False),
        )

    def update_experiment_overlay(
        self,
        experiment_id: str,
        *,
        expected_lock_version: int,
        overlay: WorldOverlayConfig | dict[str, Any],
    ) -> dict[str, Any]:
        from .experiments import ExperimentService

        experiment_service = ExperimentService(self.database)
        draft = experiment_service.get_draft(experiment_id)
        current = WorldConfig.model_validate(draft["definition"]["world"])
        if not current.map_revision_id:
            raise ServiceError(
                "EXPERIMENT_MAP_REQUIRED",
                "请先为实验选择一个已发布公共地图",
                status_code=409,
            )
        with self.database.session_factory() as session:
            revision = session.get(WorldMapRevision, current.map_revision_id)
            if revision is None or revision.state != "PUBLISHED":
                raise ServiceError(
                    "MAP_REVISION_UNAVAILABLE",
                    "实验引用的公共地图版本不可用",
                    status_code=409,
                )
            world = self.materialize_world(revision, WorldOverlayConfig.model_validate(overlay))
        return experiment_service.patch_draft_section(
            experiment_id=experiment_id,
            section="world",
            expected_lock_version=expected_lock_version,
            data=world.model_dump(mode="json", exclude_none=False),
        )

    def materialize_for_publish_in_session(
        self, session: Session, world: WorldConfig
    ) -> WorldConfig:
        if not world.map_revision_id:
            return world
        revision = session.get(WorldMapRevision, world.map_revision_id)
        if (
            revision is None
            or revision.state != "PUBLISHED"
            or revision.map_id != world.map_id
            or revision.world_hash != world.map_revision_hash
        ):
            raise ServiceError(
                "MAP_REVISION_CONFLICT",
                "实验引用的公共地图版本已失效",
                status_code=409,
            )
        return self.materialize_world(revision, world.overlay)

    @staticmethod
    def materialize_world(
        revision: WorldMapRevision, overlay: WorldOverlayConfig
    ) -> WorldConfig:
        base = normalize_public_world(revision.world_json)
        definition = _merge_patch(base.definition, overlay.definition_patch)
        assets = {item.logical_path: item for item in base.assets}
        for logical_path in overlay.removed_asset_paths:
            assets.pop(logical_path, None)
        for item in overlay.asset_additions:
            assets[item.logical_path] = item
        return WorldConfig(
            world_key=base.world_key,
            world_name=base.world_name,
            definition=definition,
            assets=list(assets.values()),
            map_id=revision.map_id,
            map_revision_id=revision.id,
            map_revision_hash=revision.world_hash,
            overlay=overlay,
        )

    def _require_draft(
        self, session: Session, map_id: str
    ) -> tuple[WorldMap, WorldMapRevision]:
        public_map = session.get(WorldMap, map_id)
        if public_map is None:
            raise not_found("map", map_id)
        revision = (
            session.get(WorldMapRevision, public_map.current_draft_revision_id)
            if public_map.current_draft_revision_id
            else None
        )
        if revision is None or revision.map_id != map_id or revision.state != "DRAFT":
            raise ServiceError("MAP_DRAFT_UNAVAILABLE", "地图没有可编辑草稿", status_code=409)
        return public_map, revision

    def _usage_experiment_ids(self, session: Session, map_id: str) -> set[str]:
        result: set[str] = set()
        revisions = session.execute(
            select(ExperimentRevision.experiment_id, ExperimentRevision.definition_json)
        )
        for experiment_id, payload in revisions:
            if ((payload or {}).get("world") or {}).get("map_id") == map_id:
                result.add(experiment_id)
        return result

    def _map_detail(self, session: Session, public_map: WorldMap) -> dict[str, Any]:
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
            "current_draft": self._revision_summary(draft),
            "current_published": self._revision_summary(published),
            "usage_count": len(self._usage_experiment_ids(session, public_map.id)),
            "dimensions": size if isinstance(size, list) else None,
            "tile_size": definition.get("tile_size") if isinstance(definition, dict) else None,
            "updated_at": public_map.updated_at.isoformat(),
            "created_at": public_map.created_at.isoformat(),
        }

    @staticmethod
    def _revision_summary(revision: WorldMapRevision | None) -> dict[str, Any] | None:
        if revision is None:
            return None
        return {
            "id": revision.id,
            "revision_no": revision.revision_no,
            "state": revision.state,
            "world_hash": revision.world_hash,
            "lock_version": revision.lock_version,
            "updated_at": revision.updated_at.isoformat(),
            "published_at": revision.published_at.isoformat() if revision.published_at else None,
        }

    def _revision_detail(
        self,
        revision: WorldMapRevision,
        public_map: WorldMap,
        *,
        include_world: bool = True,
    ) -> dict[str, Any]:
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
