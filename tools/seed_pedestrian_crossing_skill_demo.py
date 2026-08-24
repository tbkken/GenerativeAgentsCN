"""Seed an end-to-end pedestrian crossing Game Object Skill experiment.

The script uses only public Web APIs, so the resulting map, Agent, crowd, and
experiment are the same resources that a user can inspect in the console.
"""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MAP_KEY = "pedestrian-crossing-skill-demo"
MAP_NAME = "Game Object Skill：行人过街演示"
AGENT_KEY = "pedestrian-lin-xiao"
AGENT_NAME = "过街行人林晓"
CROWD_KEY = "pedestrian-crossing-demo-crowd"
CROWD_NAME = "行人过街演示人群"
EXPERIMENT_NAME = "Game Object Skill 端到端实验：行人过街"
WORLD_NAME = "行人过街演示街区"
WIDTH = 9
HEIGHT = 7


def _materials() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    colors = {
        "north": ("北侧人行区", "#DCEFE2"),
        "road": ("机动车道", "#626A73"),
        "crosswalk": ("斑马线", "#F5F1E8"),
        "south": ("南侧人行区", "#E8DCC8"),
        "signal": ("信号灯", "#F3C742"),
    }
    sources: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    for key, (name, color) in colors.items():
        source_id = f"source-{key}"
        sources.append(
            {
                "id": source_id,
                "name": name,
                "kind": "GENERATED_COLOR",
                "generated_color": color,
                "media_type": "image/png",
                "width_px": 32,
                "height_px": 32,
                "tile_width": 32,
                "tile_height": 32,
                "columns": 1,
                "rows": 1,
                "tile_count": 1,
            }
        )
        slices.append(
            {
                "id": f"slice-{key}",
                "source_id": source_id,
                "name": name,
                "kind": "TILE",
                "purpose": "MAP",
                "pixel_rect": {"x": 0, "y": 0, "width": 32, "height": 32},
            }
        )
    return sources, slices


def _hierarchy_nodes() -> list[dict[str, Any]]:
    def node(
        node_id: str,
        kind: str,
        parent_id: str | None,
        name: str,
        x: int,
        y: int,
        width: int,
        height: int,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "id": node_id,
            "kind": kind,
            "parent_id": parent_id,
            "name": name,
            "bounds": {"x": x, "y": y, "width": width, "height": height},
            **extra,
        }

    return [
        node("world", "WORLD", None, WORLD_NAME, 0, 0, WIDTH, HEIGHT),
        node("north", "SECTOR", "world", "北侧街区", 0, 0, WIDTH, 2),
        node("cafe", "ARENA", "north", "街角咖啡店", 3, 0, 3, 2),
        node("cafe-entrance", "GAME_OBJECT", "cafe", "入口", 4, 1, 1, 1),
        node("road", "SECTOR", "world", "城市道路", 0, 2, WIDTH, 3),
        node("crosswalk", "ARENA", "road", "斑马线", 3, 2, 2, 3),
        node(
            "pedestrian-signal",
            "GAME_OBJECT",
            "crosswalk",
            "行人信号灯",
            3,
            4,
            1,
            1,
            semantic="提供当前行人相位；不会主动联系 Agent。",
            skill_bindings=[
                {
                    "interaction_key": "query-pedestrian-signal",
                    "skill_name": "traffic-signal-state",
                    "description": "Agent 主动查询当前行人信号及安全通行建议",
                    "interaction_radius_m": 2.5,
                    "default_request": "现在可以安全通过斑马线吗？",
                }
            ],
            extensions={
                "appearance": {"emoji": "🚦"},
                "state": {
                    "pedestrian_signal": "",
                    "signal_cycle": {
                        "red_steps": 1,
                        "green_steps": 2,
                        "offset_steps": 0,
                    },
                }
            },
        ),
        node("south-wait", "GAME_OBJECT", "crosswalk", "南侧候行区", 4, 4, 1, 1),
        node("road-middle", "GAME_OBJECT", "crosswalk", "路中", 4, 3, 1, 1),
        node("north-exit", "GAME_OBJECT", "crosswalk", "北侧出口", 4, 2, 1, 1),
        node("south", "SECTOR", "world", "南侧街区", 0, 5, WIDTH, 2),
        node("home", "ARENA", "south", "林晓住所", 2, 5, 4, 2),
        node("home-door", "GAME_OBJECT", "home", "门口", 4, 5, 1, 1),
        node("home-bed", "GAME_OBJECT", "home", "床", 3, 6, 1, 1),
    ]


def build_world() -> dict[str, Any]:
    """Build a compact, fully addressable crossing map for runtime and editor."""

    sources, slices = _materials()
    palette = [
        {"key": "north", "label": "北侧人行区", "color": "#DCEFE2", "collision": False},
        {"key": "road", "label": "机动车道", "color": "#626A73", "collision": False},
        {"key": "crosswalk", "label": "斑马线", "color": "#F5F1E8", "collision": False},
        {"key": "south", "label": "南侧人行区", "color": "#E8DCC8", "collision": False},
        {"key": "signal", "label": "信号灯", "color": "#F3C742", "collision": False},
    ]
    tiles: list[dict[str, Any]] = []
    cells: dict[str, dict[str, Any]] = {}
    tile_overrides: dict[int, str] = {}
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if y <= 1:
                kind, address = "north", ["北侧街区"]
            elif y <= 4:
                kind, address = "road", ["城市道路"]
            else:
                kind, address = "south", ["南侧街区"]

            if x == 4 and 2 <= y <= 4:
                kind = "crosswalk"
                address = [
                    "城市道路",
                    "斑马线",
                    {4: "南侧候行区", 3: "路中", 2: "北侧出口"}[y],
                ]
            if (x, y) == (3, 4):
                kind, address = "signal", ["城市道路", "斑马线", "行人信号灯"]
            elif (x, y) == (4, 1):
                address = ["北侧街区", "街角咖啡店", "入口"]
            elif (x, y) == (4, 5):
                address = ["南侧街区", "林晓住所", "门口"]
            elif (x, y) == (3, 6):
                address = ["南侧街区", "林晓住所", "床"]

            index = y * WIDTH + x
            tiles.append(
                {
                    "coord": [x, y],
                    "collision": False,
                    "address": address,
                    "tile": kind,
                }
            )
            cells[f"{x},{y}"] = {"kind": kind}
            tile_overrides[index] = f"slice-{kind}"

    return {
        "world_key": MAP_KEY,
        "world_name": WORLD_NAME,
        "definition": {
            "world": WORLD_NAME,
            "size": [HEIGHT, WIDTH],
            "tile_size": 32,
            "tile_address_keys": ["world", "sector", "arena", "game_object"],
            "tiles": tiles,
            "palette": palette,
            "editor": {"schema_version": 1, "palette": palette, "cells": cells},
            "editor_v2": {
                "schema_version": "ga-map-editor/v2",
                "root_node_id": "world",
                "material_sources": sources,
                "material_slices": slices,
                "render_recipes": [],
                "visual_layers": [],
                "hierarchy_nodes": _hierarchy_nodes(),
                "import_metadata": {
                    "source": "pedestrian-crossing-skill-demo",
                    "width": WIDTH,
                    "height": HEIGHT,
                    "meters_per_tile": 1.0,
                },
                "tile_overrides": tile_overrides,
                "tile_override_parts": {},
                "tile_override_layers": {},
                "ui_state": {"workspace": "world", "selected_node_id": "pedestrian-signal"},
            },
        },
        "assets": [],
        "map_id": None,
        "map_revision_id": None,
        "map_revision_hash": None,
        "overlay": {
            "definition_patch": {},
            "asset_additions": [],
            "removed_asset_paths": [],
        },
    }


def build_agent() -> dict[str, Any]:
    return {
        "agent_key": AGENT_KEY,
        "enabled": True,
        "name": AGENT_NAME,
        "portrait_asset": None,
        "sprite_asset": None,
        "model_override": None,
        "tags": ["Game Object Skill", "行人过街"],
        "goals": ["从南侧住所步行穿过斑马线，到北侧街角咖啡店"],
        "coord": [4, 5],
        "currently": "上午八点，准备从南侧候行区过马路去北侧咖啡店",
        "scratch": {
            "age": 28,
            "innate": "谨慎、遵守交通规则、会主动观察环境设施",
            "learned": "过马路前应主动查询行人信号灯，红灯等待，绿灯确认安全后通行",
            "lifestyle": "每天上午八点从住所步行到街角咖啡店",
            "daily_plan": (
                "08:00 从住所门口出发；08:15 到达南侧候行区；08:20 主动查询行人信号灯；"
                "08:25 红灯原地等待；08:40 绿灯确认安全；08:45 穿过斑马线；"
                "08:50 到达北侧街角咖啡店入口。"
            ),
        },
        "spatial": {
            "address": {
                "living_area": [WORLD_NAME, "南侧街区", "林晓住所"],
                "sleeping": [WORLD_NAME, "南侧街区", "林晓住所", "床"],
                "destination": [WORLD_NAME, "北侧街区", "街角咖啡店", "入口"],
            },
            "tree": {
                WORLD_NAME: {
                    "南侧街区": {"林晓住所": ["床", "门口"]},
                    "城市道路": {
                        "斑马线": ["北侧出口"]
                    },
                    "北侧街区": {"街角咖啡店": ["入口"]},
                }
            },
        },
    }


def _request(base_url: str, path: str, *, method: str = "GET", payload: Any = None) -> Any:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}/api/v1{path}",
        method=method,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {error.code} {detail}") from error


def _find(items: list[dict[str, Any]], field: str, value: str) -> dict[str, Any] | None:
    return next((item for item in items if item.get(field) == value), None)


def _published(resource: dict[str, Any]) -> dict[str, Any] | None:
    current = resource.get("current_published")
    return current if isinstance(current, dict) and current.get("id") else None


def _ensure_map(base_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    listing = _request(base_url, f"/maps?{urlencode({'page': 1, 'page_size': 100})}")
    public_map = _find(listing["items"], "map_key", MAP_KEY)
    if public_map is not None and _published(public_map):
        return public_map, _published(public_map)  # type: ignore[return-value]
    if public_map is None:
        public_map = _request(
            base_url,
            "/maps",
            method="POST",
            payload={
                "map_key": MAP_KEY,
                "name": MAP_NAME,
                "description": "被动 Game Object Skill 的端到端演示地图：Agent 先问灯，再决定等待或过街。",
                "width": WIDTH,
                "height": HEIGHT,
                "tile_size": 32,
            },
        )
    draft = _request(base_url, f"/maps/{public_map['id']}/draft")
    saved = _request(
        base_url,
        f"/maps/{public_map['id']}/draft",
        method="PUT",
        payload={"lock_version": draft["lock_version"], "world": build_world()},
    )
    published = _request(
        base_url,
        f"/maps/{public_map['id']}/draft/publish",
        method="POST",
        payload={"draft_revision_id": saved["id"], "lock_version": saved["lock_version"]},
    )
    return public_map, published


def _ensure_agent(base_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    listing = _request(base_url, "/agent-templates?page=1&page_size=500")
    agent = _find(listing["items"], "agent_key", AGENT_KEY)
    if agent is not None and _published(agent):
        return agent, _published(agent)  # type: ignore[return-value]
    if agent is None:
        agent = _request(
            base_url,
            "/agent-templates",
            method="POST",
            payload={
                "definition": build_agent(),
                "description": "主动查询行人信号灯，并把 Game Object Skill 输出作为外部观察。",
                "agent_key": AGENT_KEY,
            },
        )
    draft = _request(base_url, f"/agent-templates/{agent['id']}/draft")
    published = _request(
        base_url,
        f"/agent-templates/{agent['id']}/draft/publish",
        method="POST",
        payload={"draft_revision_id": draft["id"], "lock_version": draft["lock_version"]},
    )
    return agent, published


def _ensure_crowd(
    base_url: str, agent_revision_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    listing = _request(base_url, "/crowds?page=1&page_size=100")
    crowd = _find(listing["items"], "crowd_key", CROWD_KEY)
    if crowd is not None and _published(crowd):
        return crowd, _published(crowd)  # type: ignore[return-value]
    if crowd is None:
        crowd = _request(
            base_url,
            "/crowds",
            method="POST",
            payload={
                "name": CROWD_NAME,
                "crowd_key": CROWD_KEY,
                "description": "仅包含行人林晓的单 Agent 演示人群。",
                "agent_revision_ids": [agent_revision_id],
            },
        )
    draft = _request(base_url, f"/crowds/{crowd['id']}/draft")
    published = _request(
        base_url,
        f"/crowds/{crowd['id']}/draft/publish",
        method="POST",
        payload={"draft_revision_id": draft["id"], "lock_version": draft["lock_version"]},
    )
    return crowd, published


def _ensure_experiment(
    base_url: str,
    *,
    map_revision_id: str,
    crowd_revision_id: str,
    chat_base_url: str | None = None,
    chat_model: str | None = None,
    chat_secret_ref: str | None = None,
) -> tuple[dict[str, Any], bool]:
    listing = _request(base_url, "/experiments?page=1&page_size=50&archived=all")
    experiment = _find(listing["items"], "name", EXPERIMENT_NAME)
    if experiment is not None:
        return experiment, False
    experiment = _request(
        base_url,
        "/experiments",
        method="POST",
        payload={
            "name": EXPERIMENT_NAME,
            "goal": (
                "验证 Game Object Skill 的被动请求-响应边界：靠近不自动触发；"
                "Agent 主动查询；红灯输出进入外部观察后等待；绿灯输出进入外部观察后过街。"
            ),
            "owner": "Game Object Skill Demo",
            "tags": ["game-object-skill", "pedestrian-crossing", "end-to-end"],
            "map_revision_id": map_revision_id,
            "crowd_revision_ids": [crowd_revision_id],
        },
    )
    draft = _request(base_url, f"/experiments/{experiment['id']}/draft")
    definition = draft["definition"]
    definition["simulation"].update(
        {
            "start_time": "2026-08-22T08:45:00+08:00",
            "stride_minutes": 1,
            "max_steps": 3,
            "checkpoint_interval_steps": 1,
            "checkpoint_retention": 2,
            "record_interval_minutes": 1,
            "random_seed": 20260822,
        }
    )
    if chat_base_url and chat_model:
        definition["models"]["chat"].update(
            {
                "provider": "vllm",
                "base_url": chat_base_url,
                "model": chat_model,
                "resolved_model": None,
                "secret_ref": chat_secret_ref,
                "timeout_seconds": 1800,
                "retry_attempts": 2,
                "retry_backoff_seconds": 2,
            }
        )
    _request(
        base_url,
        f"/experiments/{experiment['id']}/draft",
        method="PUT",
        payload={"lock_version": draft["lock_version"], "data": definition},
    )
    return _request(base_url, f"/experiments/{experiment['id']}"), True


def seed(
    base_url: str,
    *,
    run: bool = False,
    chat_base_url: str | None = None,
    chat_model: str | None = None,
    chat_secret_ref: str | None = None,
) -> dict[str, Any]:
    public_map, map_revision = _ensure_map(base_url)
    agent, agent_revision = _ensure_agent(base_url)
    crowd, crowd_revision = _ensure_crowd(base_url, agent_revision["id"])
    experiment, created = _ensure_experiment(
        base_url,
        map_revision_id=map_revision["id"],
        crowd_revision_id=crowd_revision["id"],
        chat_base_url=chat_base_url,
        chat_model=chat_model,
        chat_secret_ref=chat_secret_ref,
    )
    result = {
        "created": created,
        "map_id": public_map["id"],
        "map_revision_id": map_revision["id"],
        "agent_id": agent["id"],
        "agent_revision_id": agent_revision["id"],
        "crowd_id": crowd["id"],
        "crowd_revision_id": crowd_revision["id"],
        "experiment_id": experiment["id"],
        "run": None,
    }
    if run:
        if experiment.get("status") == "DRAFT":
            draft = _request(base_url, f"/experiments/{experiment['id']}/draft")
            result["run"] = _request(
                base_url,
                f"/experiments/{experiment['id']}/actions/publish-and-run",
                method="POST",
                payload={
                    "draft_revision_id": draft["id"],
                    "lock_version": draft["lock_version"],
                },
            )
        else:
            revision_id = experiment.get("published_revision_id")
            if not revision_id:
                raise RuntimeError("experiment has neither a Draft nor a published Revision")
            result["run"] = _request(
                base_url,
                f"/experiments/{experiment['id']}/revisions/{revision_id}/runs",
                method="POST",
            )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--run",
        action="store_true",
        help="publish and start the experiment (requires configured model services)",
    )
    parser.add_argument("--chat-base-url", help="optional OpenAI-compatible local chat URL")
    parser.add_argument("--chat-model", help="model ID exposed by --chat-base-url")
    parser.add_argument(
        "--chat-secret-ref",
        help="optional existing console secret ID; the plaintext token is never stored here",
    )
    args = parser.parse_args()
    if bool(args.chat_base_url) != bool(args.chat_model):
        parser.error("--chat-base-url and --chat-model must be provided together")
    print(
        json.dumps(
            seed(
                args.base_url,
                run=args.run,
                chat_base_url=args.chat_base_url,
                chat_model=args.chat_model,
                chat_secret_ref=args.chat_secret_ref,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
