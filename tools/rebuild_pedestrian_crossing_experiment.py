"""Rebuild the pedestrian-crossing demo as a Ville-scale experiment.

The script intentionally keeps the public map id stable so existing bookmarks
continue to work.  It crops the real Ville visual layers, adds an editable
two-crosswalk avenue, publishes the new map revision, creates a fresh
experiment with a scenario-specific Brain, and can launch the run.
"""

from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generative_agents.config.map_editor import (  # noqa: E402
    GridRect,
    HierarchyNode,
    MapEditorDocumentV2,
    MaterialSlice,
)
from generative_agents.services.map_importer import (  # noqa: E402
    GID_MASK,
    fresh_ville_editor_document,
)


API = "http://127.0.0.1:8000/api/v1"
MAP_ID = "8f3f17dc-05b3-498a-8ff2-36fbf143992c"
SOURCE_EXPERIMENT_REVISION_ID = "c1e6f832-eb9b-41a0-9eca-90ce3a9df13e"
MAP_NAME = "Ville 晨间双人行横道"
WORLD_NAME = "Ville 晨间通勤街区"
EXPERIMENT_NAME = "Ville 长时过街实验：信号感知与安全决策"

WIDTH = 64
HEIGHT = 40
CROP_X = 38
CROP_Y = 15
ROAD_Y1 = 16
ROAD_Y2 = 21
WEST_CROSSING_X1 = 15
WEST_CROSSING_X2 = 18
EAST_CROSSING_X1 = 45
EAST_CROSSING_X2 = 48


def request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """调用本地 Web API，并在失败时保留方法、路径和响应正文。"""

    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {body}") from exc


def _crop_layer(raw: list[int], source_width: int) -> list[int]:
    """从 Ville 原始图层裁出本演示使用的固定矩形 Tile 数据。"""

    return [
        int(raw[(CROP_Y + y) * source_width + CROP_X + x])
        for y in range(HEIGHT)
        for x in range(WIDTH)
    ]


def _inside_crossing(x: int, y: int) -> bool:
    """判断坐标是否位于东西两条斑马线之一。"""

    return ROAD_Y1 <= y <= ROAD_Y2 and (
        WEST_CROSSING_X1 <= x <= WEST_CROSSING_X2
        or EAST_CROSSING_X1 <= x <= EAST_CROSSING_X2
    )


def _runtime_address(x: int, y: int) -> list[str]:
    """把裁剪后坐标映射为运行时四层空间语义地址。"""

    if WEST_CROSSING_X1 <= x <= WEST_CROSSING_X2 and 13 <= y <= 24:
        address = [WORLD_NAME, "中央大道", "西侧人行横道"]
        if y < ROAD_Y1:
            return [*address, "北侧等候区"]
        if y > ROAD_Y2:
            return [*address, "南侧出口"]
        return address
    if EAST_CROSSING_X1 <= x <= EAST_CROSSING_X2 and 13 <= y <= 24:
        address = [WORLD_NAME, "中央大道", "东侧人行横道"]
        if y < ROAD_Y1:
            return [*address, "北侧等候区"]
        if y > ROAD_Y2:
            return [*address, "南侧出口"]
        return address
    if ROAD_Y1 <= y <= ROAD_Y2:
        return [WORLD_NAME, "中央大道", "双向车行区"]
    if y < ROAD_Y1:
        return [WORLD_NAME, "北侧商业街区", "林荫步行道"]
    return [WORLD_NAME, "南侧生活街区", "社区步行道"]


def _skill_binding() -> list[dict[str, Any]]:
    """构造信号灯对象公开给行人的主动查询 Skill 绑定。"""

    return [
        {
            "interaction_key": "query-crosswalk-signal",
            "skill_name": "crosswalk-signal-advisor",
            "description": "Agent 主动询问该人行横道实例的当前相位与安全动作",
            "interaction_radius_m": 5.5,
            "default_request": "我已经接近人行横道，现在是否可以安全进入斑马线？",
        }
    ]


def _signal_node(
    *,
    node_id: str,
    parent_id: str,
    name: str,
    x: int,
    y: int,
    material_slice_id: str,
    offset_steps: int,
) -> HierarchyNode:
    """创建带独立相位偏移和查询 Skill 的交通信号灯层级节点。"""

    return HierarchyNode(
        id=node_id,
        kind="GAME_OBJECT",
        parent_id=parent_id,
        name=name,
        bounds=GridRect(x=x, y=y, width=1, height=2),
        semantic="被动提供当前行人相位和自然语言安全建议",
        material_slice_id=material_slice_id,
        skill_bindings=_skill_binding(),
        extensions={
            "state": {
                "crossing_name": name.replace("信号灯", "人行横道"),
                "signal_cycle": {
                    "red_steps": 4,
                    "green_steps": 5,
                    "flashing_steps": 2,
                    "offset_steps": offset_steps,
                },
            }
        },
    )


def build_world(current_world: dict[str, Any]) -> dict[str, Any]:
    """把当前地图草稿重建为可运行、可编辑的双斑马线演示世界。"""

    current_editor = current_world["definition"]["editor_v2"]
    uploaded_sources = [
        copy.deepcopy(item)
        for item in current_editor["material_sources"]
        if item.get("kind") == "UPLOADED"
    ]
    uploaded_source_ids = {item["id"] for item in uploaded_sources}
    uploaded_slices = [
        copy.deepcopy(item)
        for item in current_editor["material_slices"]
        if item.get("source_id") in uploaded_source_ids
        and item.get("id")
        not in {"slice-long-crossing-road-horizontal", "slice-long-crossing-zebra-vertical"}
    ]
    signal_slice = next(
        item for item in uploaded_slices if item["pixel_rect"]["height"] == 64
    )
    road_source = next(
        item for item in uploaded_sources if item["width_px"] == 64 and item["height_px"] == 64
    )
    road_slice = next(
        item
        for item in uploaded_slices
        if item["source_id"] == road_source["id"]
        and item["pixel_rect"] == {"x": 0, "y": 32, "width": 32, "height": 32}
    )
    zebra_slice = next(
        item
        for item in uploaded_slices
        if item["source_id"] == road_source["id"]
        and item["pixel_rect"] == {"x": 32, "y": 32, "width": 32, "height": 32}
    )
    road_horizontal = MaterialSlice.model_validate(
        {
            **road_slice,
            "id": "slice-long-crossing-road-horizontal",
            "name": "中央大道横向道路",
            "rotation_degrees": 90,
        }
    )
    zebra_vertical = MaterialSlice.model_validate(
        {
            **zebra_slice,
            "id": "slice-long-crossing-zebra-vertical",
            "name": "纵向人行横道",
            "rotation_degrees": 90,
        }
    )

    ville = fresh_ville_editor_document()
    source_width = int(ville.import_metadata["width"])
    cropped_layers = []
    for layer in ville.visual_layers:
        payload = layer.model_dump(mode="json", exclude_none=False)
        payload.update(
            {
                "raw_gids": _crop_layer(layer.raw_gids, source_width),
                "width": WIDTH,
                "height": HEIGHT,
            }
        )
        cropped_layers.append(type(layer).model_validate(payload))
    ville.visual_layers = cropped_layers

    root = "world-ville-morning-crossing"
    north_sector = "sector-north-commercial"
    avenue_sector = "sector-central-avenue"
    south_sector = "sector-south-neighborhood"
    west_arena = "arena-west-crosswalk"
    east_arena = "arena-east-crosswalk"
    north_walk = "arena-north-promenade"
    road_arena = "arena-two-way-road"
    south_walk = "arena-south-promenade"
    nodes: list[HierarchyNode] = [
        HierarchyNode(
            id=root,
            kind="WORLD",
            name=WORLD_NAME,
            bounds=GridRect(x=0, y=0, width=WIDTH, height=HEIGHT),
            semantic="一段从真实 Ville 视觉层裁出的晨间商业与生活街区",
        ),
        HierarchyNode(
            id=north_sector,
            kind="SECTOR",
            parent_id=root,
            name="北侧商业街区",
            bounds=GridRect(x=0, y=0, width=WIDTH, height=ROAD_Y1),
            semantic="住宅、商店与林荫步行空间",
        ),
        HierarchyNode(
            id=avenue_sector,
            kind="SECTOR",
            parent_id=root,
            name="中央大道",
            bounds=GridRect(x=0, y=13, width=WIDTH, height=12),
            semantic="双向机动车道、两组人行横道与等候区；语义范围允许与两侧街区衔接重叠",
        ),
        HierarchyNode(
            id=south_sector,
            kind="SECTOR",
            parent_id=root,
            name="南侧生活街区",
            bounds=GridRect(x=0, y=ROAD_Y2 + 1, width=WIDTH, height=HEIGHT - ROAD_Y2 - 1),
            semantic="咖啡馆入口、社区步道与公共绿地",
        ),
        HierarchyNode(
            id=north_walk,
            kind="ARENA",
            parent_id=north_sector,
            name="林荫步行道",
            bounds=GridRect(x=0, y=0, width=WIDTH, height=ROAD_Y1),
            semantic="行人接近中央大道前的步行区域",
        ),
        HierarchyNode(
            id=road_arena,
            kind="ARENA",
            parent_id=avenue_sector,
            name="双向车行区",
            bounds=GridRect(x=0, y=ROAD_Y1, width=WIDTH, height=ROAD_Y2 - ROAD_Y1 + 1),
            semantic="需要通过人行横道穿越的连续机动车道",
        ),
        HierarchyNode(
            id=west_arena,
            kind="ARENA",
            parent_id=avenue_sector,
            name="西侧人行横道",
            bounds=GridRect(x=WEST_CROSSING_X1 - 1, y=13, width=6, height=12),
            semantic="西侧斑马线、南北等候区与独立相位信号灯",
        ),
        HierarchyNode(
            id=east_arena,
            kind="ARENA",
            parent_id=avenue_sector,
            name="东侧人行横道",
            bounds=GridRect(x=EAST_CROSSING_X1 - 1, y=13, width=6, height=12),
            semantic="东侧斑马线、南北等候区与错峰相位信号灯",
        ),
        HierarchyNode(
            id=south_walk,
            kind="ARENA",
            parent_id=south_sector,
            name="社区步行道",
            bounds=GridRect(x=0, y=ROAD_Y2 + 1, width=WIDTH, height=HEIGHT - ROAD_Y2 - 1),
            semantic="过街后的步行目的地区域",
        ),
        HierarchyNode(
            id="go-west-north-wait",
            kind="GAME_OBJECT",
            parent_id=west_arena,
            name="北侧等候区",
            bounds=GridRect(x=WEST_CROSSING_X1, y=14, width=4, height=2),
            semantic="红灯或闪烁阶段尚未进入斑马线的等待位置",
        ),
        HierarchyNode(
            id="go-west-south-exit",
            kind="GAME_OBJECT",
            parent_id=west_arena,
            name="南侧出口",
            bounds=GridRect(x=WEST_CROSSING_X1, y=22, width=4, height=2),
            semantic="完成西侧过街后的安全离开区域",
        ),
        HierarchyNode(
            id="go-east-north-wait",
            kind="GAME_OBJECT",
            parent_id=east_arena,
            name="北侧等候区",
            bounds=GridRect(x=EAST_CROSSING_X1, y=14, width=4, height=2),
            semantic="东侧人行横道的北侧等待位置",
        ),
        HierarchyNode(
            id="go-east-south-exit",
            kind="GAME_OBJECT",
            parent_id=east_arena,
            name="南侧出口",
            bounds=GridRect(x=EAST_CROSSING_X1, y=22, width=4, height=2),
            semantic="完成东侧过街后的安全离开区域",
        ),
        _signal_node(
            node_id="go-west-signal-north",
            parent_id=west_arena,
            name="西侧北向信号灯",
            x=WEST_CROSSING_X1 - 2,
            y=14,
            material_slice_id=signal_slice["id"],
            offset_steps=0,
        ),
        _signal_node(
            node_id="go-west-signal-south",
            parent_id=west_arena,
            name="西侧南向信号灯",
            x=WEST_CROSSING_X2 + 2,
            y=22,
            material_slice_id=signal_slice["id"],
            offset_steps=0,
        ),
        _signal_node(
            node_id="go-east-signal-north",
            parent_id=east_arena,
            name="东侧北向信号灯",
            x=EAST_CROSSING_X1 - 2,
            y=14,
            material_slice_id=signal_slice["id"],
            offset_steps=5,
        ),
        _signal_node(
            node_id="go-east-signal-south",
            parent_id=east_arena,
            name="东侧南向信号灯",
            x=EAST_CROSSING_X2 + 2,
            y=22,
            material_slice_id=signal_slice["id"],
            offset_steps=5,
        ),
        HierarchyNode(
            id="go-cafe-entrance",
            kind="GAME_OBJECT",
            parent_id=south_walk,
            name="晨光咖啡馆入口",
            bounds=GridRect(x=WEST_CROSSING_X1, y=29, width=5, height=3),
            semantic="本次步行通勤的目的地",
        ),
    ]

    local_collisions = {
        (int(x) - CROP_X, int(y) - CROP_Y)
        for x, y in ville.import_metadata.get("collision_coords", [])
        if CROP_X <= int(x) < CROP_X + WIDTH and CROP_Y <= int(y) < CROP_Y + HEIGHT
    }
    protected_walkway = {
        (x, y)
        for y in range(8, 33)
        for x in range(WEST_CROSSING_X1, WEST_CROSSING_X2 + 1)
    } | {
        (x, y)
        for y in range(8, 33)
        for x in range(EAST_CROSSING_X1, EAST_CROSSING_X2 + 1)
    }
    local_collisions -= protected_walkway
    local_collisions -= {
        (x, y) for y in range(ROAD_Y1, ROAD_Y2 + 1) for x in range(WIDTH)
    }

    tile_layers: dict[int, list[dict[str, Any]]] = {}
    for y in range(ROAD_Y1, ROAD_Y2 + 1):
        for x in range(WIDTH):
            slice_id = (
                zebra_vertical.id if _inside_crossing(x, y) else road_horizontal.id
            )
            tile_layers[y * WIDTH + x] = [{"slice_id": slice_id, "part": None}]

    editor = MapEditorDocumentV2(
        root_node_id=root,
        material_sources=[
            *ville.material_sources,
            *uploaded_sources,
        ],
        material_slices=[
            *ville.material_slices,
            *uploaded_slices,
            road_horizontal,
            zebra_vertical,
        ],
        material_canvases=[],
        render_recipes=[],
        visual_layers=ville.visual_layers,
        hierarchy_nodes=nodes,
        import_metadata={
            "importer": "ville-crossing-crop/v1",
            "source": "tilemap/tilemap.json",
            "crop_origin": [CROP_X, CROP_Y],
            "width": WIDTH,
            "height": HEIGHT,
            "tile_size": 32,
            "collision_coords": [list(item) for item in sorted(local_collisions, key=lambda p: (p[1], p[0]))],
            "used_gid_count": len(
                {
                    int(raw) & GID_MASK
                    for layer in ville.visual_layers
                    for raw in layer.raw_gids
                    if int(raw) & GID_MASK
                }
            ),
        },
        tile_overrides={index: layers[-1]["slice_id"] for index, layers in tile_layers.items()},
        tile_override_layers=tile_layers,
        ui_state={
            "active_tab": "WORLD",
            "selected_node_id": root,
            "visible_level": "GAME_OBJECT",
            "show_semantics": False,
        },
    )

    tiles = []
    for y in range(HEIGHT):
        for x in range(WIDTH):
            tiles.append(
                {
                    "coord": [x, y],
                    "collision": (x, y) in local_collisions,
                    "address": _runtime_address(x, y),
                    "tile": (
                        "crosswalk"
                        if _inside_crossing(x, y)
                        else "road"
                        if ROAD_Y1 <= y <= ROAD_Y2
                        else "ville"
                    ),
                }
            )

    world = copy.deepcopy(current_world)
    world["world_name"] = WORLD_NAME
    world["definition"] = {
        "world": WORLD_NAME,
        "size": [HEIGHT, WIDTH],
        "tile_size": 32,
        "tile_address_keys": ["world", "sector", "arena", "game_object"],
        "tiles": tiles,
        "palette": [
            {"key": "ville", "label": "Ville 街区视觉", "color": "#86cf72", "collision": False},
            {"key": "road", "label": "中央大道", "color": "#4f5657", "collision": False},
            {"key": "crosswalk", "label": "人行横道", "color": "#f4f1df", "collision": False},
        ],
        "traffic_layout": {
            "road": {"bounds": {"x": 0, "y": ROAD_Y1, "width": WIDTH, "height": 6}},
            "crosswalks": [
                {"key": "west", "bounds": {"x": WEST_CROSSING_X1, "y": ROAD_Y1, "width": 4, "height": 6}},
                {"key": "east", "bounds": {"x": EAST_CROSSING_X1, "y": ROAD_Y1, "width": 4, "height": 6}},
            ],
        },
        "editor_v2": editor.model_dump(mode="json", exclude_none=False),
    }
    return world


def update_map_metadata() -> None:
    """直接修正演示地图的展示名称和用途说明，不改变 Revision 内容。"""

    database_path = ROOT / "var" / "generative-agents.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE world_maps SET name = ?, description = ? WHERE id = ?",
            (
                MAP_NAME,
                "64 × 40 Ville 像素街区，包含中央大道、两组人行横道、四个独立信号灯与长时过街实验语义。",
                MAP_ID,
            ),
        )
        connection.commit()


def replace_map_draft() -> dict[str, Any]:
    """读取当前地图草稿、生成新世界并用乐观锁完整替换。"""

    draft = request_json("GET", f"/maps/{MAP_ID}/draft")
    world = build_world(draft["world"])
    saved = request_json(
        "PUT",
        f"/maps/{MAP_ID}/draft",
        {"lock_version": draft["lock_version"], "world": world},
    )
    update_map_metadata()
    return saved


def publish_map() -> dict[str, Any]:
    """发布当前地图草稿并返回不可变 Revision。"""

    draft = request_json("GET", f"/maps/{MAP_ID}/draft")
    return request_json(
        "POST",
        f"/maps/{MAP_ID}/draft/publish",
        {"draft_revision_id": draft["id"], "lock_version": draft["lock_version"]},
    )


def create_experiment(map_revision_id: str) -> dict[str, Any]:
    """创建或复用演示实验，并绑定指定的已发布地图 Revision。"""

    catalog = request_json(
        "GET", f"/experiments?q={quote(EXPERIMENT_NAME)}&archived=all&page_size=50"
    )
    created = next(
        (
            item
            for item in catalog.get("items", [])
            if item.get("name") == EXPERIMENT_NAME
        ),
        None,
    )
    if created is None:
        created = request_json(
            "POST",
            "/experiments",
            {
                "name": EXPERIMENT_NAME,
                "goal": "观察 Agent 在 45 分钟晨间通勤中，能否主动查询独立信号灯、根据红绿与闪烁相位安全等待和过街，并在离开人行横道后继续前往咖啡馆。",
                "owner": "Pedestrian Safety Lab",
                "tags": ["ville", "pedestrian-crossing", "game-object-skill", "long-run", "brain"],
                "source": {"type": "REVISION", "revision_id": SOURCE_EXPERIMENT_REVISION_ID},
                "map_revision_id": map_revision_id,
            },
        )
    experiment_id = created["id"]
    if created.get("status") != "DRAFT":
        detail = request_json("GET", f"/experiments/{experiment_id}")
        published = detail.get("current_published")
        if published is None:
            raise RuntimeError("existing experiment has no published revision to fork")
        request_json(
            "POST",
            f"/experiments/{experiment_id}/revisions/{published['id']}/fork",
            {},
        )
    draft = request_json("GET", f"/experiments/{experiment_id}/draft")
    definition = draft["definition"]
    definition["engine"]["brain_skill"] = "pedestrian-crossing-brain"
    definition["simulation"].update(
        {
            "start_time": "2026-08-23T08:05:00+08:00",
            "stride_minutes": 3,
            "max_steps": 15,
            "checkpoint_interval_steps": 3,
            "checkpoint_retention": 3,
            "record_interval_minutes": 3,
            "random_seed": 2026082302,
            "log_level": "INFO",
        }
    )
    definition["results"].update(
        {
            "agent_step_projection_interval_steps": 1,
            "replay_interpolation_frames": 30,
            "capture_model_payloads": False,
        }
    )
    definition["behavior"]["percept"].update(
        {"mode": "box", "vision_radius": 10, "attention_bandwidth": 12}
    )
    definition["behavior"]["chat"].update(
        {"max_iterations": 4, "cooldown_minutes": 45, "stop_after_hour": 23}
    )
    previous = (definition.get("agents") or [{}])[0]
    definition["agents"] = [
        {
            "agent_key": "pedestrian-lin-xiao-ville",
            "enabled": True,
            "name": "林晓",
            "portrait_asset": previous.get("portrait_asset"),
            "sprite_asset": previous.get("sprite_asset"),
            "model_override": None,
            "tags": ["pedestrian", "commuter", "signal-aware"],
            "goals": [
                "从北侧商业街区步行到南侧生活街区的晨光咖啡馆",
                "接近中央大道时主动查询附近信号灯，不猜测相位",
                "红灯等待，绿灯通过，闪烁阶段按自己是否已进入斑马线采取安全动作",
            ],
            "coord": [16, 12],
            "currently": "08:05 从北侧林荫步行道出发，准备穿过中央大道去晨光咖啡馆；到达人行横道前需要主动确认信号。",
            "scratch": {
                "age": 29,
                "innate": "谨慎、守规则、观察细致，遇到不确定交通状态会先确认。",
                "learned": "熟悉 Ville 街区，但知道两个过街点的信号相位彼此独立，不能依据另一盏灯推断。",
                "lifestyle": "工作日上午步行通勤，喜欢提前出发，在咖啡馆开始一天的工作。",
                "daily_plan": "08:05 从北侧商业街区出发；接近西侧人行横道后查询信号并安全过街；08:30 前到达南侧晨光咖啡馆。",
            },
            "spatial": {
                "address": {
                    "living_area": [WORLD_NAME, "北侧商业街区", "林荫步行道"],
                    "sleeping": [WORLD_NAME, "北侧商业街区", "林荫步行道"],
                    "start": [WORLD_NAME, "北侧商业街区", "林荫步行道"],
                    "crossing": [WORLD_NAME, "中央大道", "西侧人行横道"],
                    "destination": [WORLD_NAME, "南侧生活街区", "社区步行道"],
                },
                "tree": {
                    WORLD_NAME: {
                        "北侧商业街区": {"林荫步行道": []},
                        "中央大道": {
                            "西侧人行横道": ["北侧等候区", "南侧出口"],
                            "东侧人行横道": ["北侧等候区", "南侧出口"],
                            "双向车行区": [],
                        },
                        "南侧生活街区": {"社区步行道": []},
                    }
                },
            },
        }
    ]
    saved = request_json(
        "PUT",
        f"/experiments/{experiment_id}/draft",
        {"lock_version": draft["lock_version"], "data": definition},
    )
    validation = request_json("POST", f"/experiments/{experiment_id}/draft/validate", {})
    if not validation.get("valid"):
        raise RuntimeError(f"experiment validation failed: {json.dumps(validation, ensure_ascii=False)}")
    return {"experiment": created, "draft": saved, "validation": validation}


def launch_experiment(experiment_id: str) -> dict[str, Any]:
    """发布指定实验草稿并创建一次演示 Run。"""

    draft = request_json("GET", f"/experiments/{experiment_id}/draft")
    return request_json(
        "POST",
        f"/experiments/{experiment_id}/actions/publish-and-run",
        {"draft_revision_id": draft["id"], "lock_version": draft["lock_version"]},
    )


def main() -> None:
    """按参数重建地图、实验，并可选择立即启动运行。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--map-only", action="store_true", help="save the redesigned map draft only")
    parser.add_argument("--launch", action="store_true", help="publish the experiment and queue its run")
    args = parser.parse_args()

    map_detail = request_json("GET", f"/maps/{MAP_ID}")
    saved_map = replace_map_draft() if map_detail.get("current_draft") else None
    active_revision = (
        publish_map() if saved_map is not None and not args.map_only else map_detail.get("current_published")
    )
    result: dict[str, Any] = {
        "map_id": MAP_ID,
        "map_draft_id": saved_map["id"] if saved_map else None,
        "map_lock_version": saved_map["lock_version"] if saved_map else None,
        "dimensions": [HEIGHT, WIDTH],
    }
    if not args.map_only:
        if active_revision is None:
            raise RuntimeError("map has neither a draft nor a published revision")
        built = create_experiment(active_revision["id"])
        experiment_id = built["experiment"]["id"]
        result.update(
            {
                "map_revision_id": active_revision["id"],
                "experiment_id": experiment_id,
                "experiment_draft_id": built["draft"]["id"],
                "validation": built["validation"],
            }
        )
        if args.launch:
            result["run"] = launch_experiment(experiment_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
