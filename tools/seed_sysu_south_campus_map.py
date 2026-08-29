"""Create the approximate SYSU South Campus public map through the Web API."""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MAP_KEY = "sysu-south-campus"
MAP_NAME = "中山大学南校区（概念版）"
WIDTH = 140
HEIGHT = 100


def _inside_polygon(x: int, y: int, points: list[tuple[int, int]]) -> bool:
    """用射线法判断网格坐标是否落在校园轮廓多边形内。"""

    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def _sector(x: int, y: int) -> str:
    """按坐标把校园 Tile 粗分到北部、中部、南部或西部区域。"""

    if y < 34:
        return "北部教学区"
    if y > 76:
        return "南部生活区"
    if x < 50:
        return "西部生活区"
    if x > 104:
        return "东部运动区"
    return "中部教学区"


def build_world() -> dict[str, Any]:
    """生成中大南校区的道路、水体、建筑和运行时地址网格。"""

    campus_outline = [
        (84, 0), (103, 3), (108, 17), (130, 18), (132, 27), (139, 30),
        (139, 51), (134, 52), (134, 71), (126, 79), (108, 85),
        (94, 90), (85, 99), (68, 99), (58, 94), (34, 92), (10, 86),
        (3, 72), (0, 61), (14, 56), (38, 54), (45, 43), (66, 38),
        (72, 27), (70, 18), (84, 16),
    ]
    cells: dict[str, dict[str, str]] = {}
    runtime: dict[tuple[int, int], dict[str, Any]] = {}

    def set_cell(
        x: int,
        y: int,
        kind: str,
        *,
        collision: bool | None = None,
        address: list[str] | None = None,
    ) -> None:
        """写入一个可见单元，并按需同步碰撞和语义地址到运行 Tile。"""

        if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
            return
        cells[f"{x},{y}"] = {"kind": kind}
        tile = runtime.setdefault((x, y), {"coord": [x, y]})
        if collision is not None:
            tile["collision"] = collision
        if address is not None:
            tile["address"] = address

    for y in range(HEIGHT):
        for x in range(WIDTH):
            if _inside_polygon(x, y, campus_outline):
                set_cell(x, y, "grass", collision=False, address=[_sector(x, y)])

    def disk(cx: int, cy: int, radius: int, kind: str) -> None:
        """用圆形画笔在校园轮廓内绘制一种地表。"""

        for y in range(cy - radius, cy + radius + 1):
            for x in range(cx - radius, cx + radius + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2 and f"{x},{y}" in cells:
                    set_cell(x, y, kind)

    def line(points: list[tuple[int, int]], width: int, kind: str) -> None:
        """沿折线采样并用圆形画笔绘制指定宽度的道路或小径。"""

        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            steps = max(abs(x2 - x1), abs(y2 - y1), 1)
            for step in range(steps + 1):
                ratio = step / steps
                disk(round(x1 + (x2 - x1) * ratio), round(y1 + (y2 - y1) * ratio), width, kind)

    # The screenshot's main north/south and east/west circulation skeleton.
    line([(82, 3), (81, 17), (78, 32), (76, 47), (75, 62), (73, 79), (72, 97)], 2, "road")
    line([(23, 59), (49, 57), (77, 55), (104, 55), (131, 53)], 2, "road")
    line([(67, 23), (86, 24), (106, 26), (132, 28)], 2, "road")
    line([(13, 84), (42, 83), (72, 82), (101, 80), (126, 75)], 2, "road")
    line([(8, 69), (30, 63), (49, 54), (63, 40), (73, 27)], 1, "road")
    line([(111, 20), (109, 39), (112, 56), (114, 72), (126, 78)], 1, "road")
    for points in (
        [(61, 32), (129, 32)], [(47, 42), (130, 42)],
        [(18, 67), (119, 67)], [(29, 73), (108, 73)],
        [(52, 38), (52, 88)], [(62, 34), (62, 90)],
        [(91, 15), (91, 84)], [(101, 24), (101, 80)],
    ):
        line(points, 0, "path")

    def ellipse(cx: int, cy: int, rx: int, ry: int, kind: str, collision: bool) -> None:
        """绘制椭圆水体或场地，并同步其碰撞属性。"""

        for y in range(cy - ry, cy + ry + 1):
            for x in range(cx - rx, cx + rx + 1):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1 and f"{x},{y}" in cells:
                    set_cell(x, y, kind, collision=collision)

    ellipse(91, 30, 6, 4, "water", True)
    ellipse(119, 63, 7, 4, "water", True)
    ellipse(36, 82, 5, 5, "water", True)

    def rectangle(
        x: int,
        y: int,
        width: int,
        height: int,
        kind: str,
        address: list[str],
    ) -> None:
        """绘制矩形建筑，并为其 Tile 写入完整空间地址。"""

        for yy in range(y, y + height):
            for xx in range(x, x + width):
                if f"{xx},{yy}" in cells:
                    set_cell(xx, yy, kind, collision=False, address=address)

    buildings = [
        (82, 4, 10, 5, "北部教学区", "北门广场", "校门服务台"),
        (94, 7, 11, 6, "北部教学区", "岭南MBA中心", "教学大厅"),
        (82, 19, 9, 7, "北部教学区", "岭南学院", "学院大厅"),
        (111, 20, 13, 8, "北部教学区", "体育馆", "主场馆"),
        (79, 31, 8, 6, "中部教学区", "哲生堂", "报告厅"),
        (66, 41, 8, 6, "中部教学区", "爪哇堂", "公共教室"),
        (57, 49, 10, 7, "中部教学区", "校史博物馆", "常设展厅"),
        (91, 46, 14, 10, "中部教学区", "南校区图书馆", "阅览室"),
        (62, 59, 8, 6, "中部教学区", "永芳堂", "讲堂"),
        (76, 65, 8, 6, "中部教学区", "怀士堂", "礼堂"),
        (88, 71, 9, 7, "中部教学区", "中文堂", "中文系教室"),
        (60, 82, 11, 7, "南部生活区", "生物博物馆", "标本展厅"),
        (84, 86, 11, 6, "南部生活区", "紫荆园宾馆", "宾馆前台"),
        (24, 78, 13, 9, "西部生活区", "中山大学附属中学", "教学楼"),
    ]
    for x, y, width, height, sector, arena, obj in buildings:
        rectangle(x, y, width, height, "building", [sector, arena])
        set_cell(x + width // 2, y + height // 2, "landmark", collision=False, address=[sector, arena, obj])

    # Sports fields and courts visible in the source image.
    rectangle(51, 65, 13, 18, "sports", ["西部生活区", "西区运动场"])
    rectangle(54, 68, 7, 12, "grass", ["西部生活区", "西区运动场", "足球场"])
    rectangle(97, 65, 18, 14, "sports", ["东部运动区", "东区田径场"])
    ellipse(106, 72, 6, 4, "grass", False)
    set_cell(106, 72, "landmark", collision=False, address=["东部运动区", "东区田径场", "足球场"])
    rectangle(113, 32, 14, 8, "sports", ["东部运动区", "露天球场"])

    # Four approximate entrances correspond to the numbered red markers.
    for x, y, arena, obj in (
        (82, 16, "北门", "北门入口"),
        (127, 74, "东门", "东门入口"),
        (72, 97, "南门", "南门入口"),
        (8, 69, "西门", "西门入口"),
    ):
        set_cell(x, y, "gate", collision=False, address=["校门与公共区域", arena, obj])
    set_cell(77, 54, "landmark", collision=False, address=["中部教学区", "中央草坪", "孙中山铜像"])

    palette = [
        {"id": "grass", "name": "校园绿地", "color": "#91cfad", "collision": False},
        {"id": "road", "name": "校园主路", "color": "#c5cfcd", "collision": False},
        {"id": "path", "name": "林荫步道", "color": "#ded8c6", "collision": False},
        {"id": "building", "name": "教学建筑", "color": "#9eb5c3", "collision": False},
        {"id": "water", "name": "湖泊水体", "color": "#71b7d4", "collision": True},
        {"id": "sports", "name": "运动场", "color": "#d98f79", "collision": False},
        {"id": "gate", "name": "校园入口", "color": "#efbf48", "collision": False},
        {"id": "landmark", "name": "地标", "color": "#8e78b7", "collision": False},
    ]
    tiles = sorted(runtime.values(), key=lambda item: (item["coord"][1], item["coord"][0]))
    return {
        "world_key": MAP_KEY,
        "world_name": "中山大学南校区",
        "definition": {
            "world": "中山大学南校区",
            "tile_size": 32,
            "size": [HEIGHT, WIDTH],
            "map": {"asset": "concept-grid", "layers": []},
            "camera": {"zoom_factor": 1, "zoom_range": [0.25, 4, 0.05]},
            "tile_address_keys": ["world", "sector", "arena", "game_object"],
            "tiles": tiles,
            "editor": {"schema_version": 1, "palette": palette, "cells": cells},
        },
        "assets": [],
        "map_id": None,
        "map_revision_id": None,
        "map_revision_hash": None,
    }


def _request(base_url: str, path: str, *, method: str = "GET", payload: Any = None) -> Any:
    """调用地图 API，并把 HTTP 错误响应转换为可读异常。"""

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


def seed(base_url: str) -> dict[str, Any]:
    """幂等创建、更新并发布中大南校区公共地图。"""

    existing = _request(base_url, f"/maps?{urlencode({'page': 1, 'page_size': 100})}")
    match = next((item for item in existing["items"] if item["map_key"] == MAP_KEY), None)
    if match is not None:
        return {"created": False, "map": match}
    public_map = _request(
        base_url,
        "/maps",
        method="POST",
        payload={
            "map_key": MAP_KEY,
            "name": MAP_NAME,
            "description": "依据校园示意图构建的近似地图，包含主要校门、道路、教学建筑、运动场、水体与空间语义。",
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
    return {"created": True, "map": public_map, "published": published}


def main() -> int:
    """解析目标服务地址并播种校园地图。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    result = seed(args.base_url)
    public_map = result["map"]
    published = result.get("published") or public_map.get("current_published") or {}
    print(
        json.dumps(
            {
                "created": result["created"],
                "map_id": public_map["id"],
                "map_key": public_map["map_key"],
                "name": public_map["name"],
                "revision_id": published.get("id"),
                "revision_no": published.get("revision_no"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
