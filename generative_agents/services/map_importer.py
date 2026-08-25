"""Import the bundled Ville Tiled project into the map-editor v2 document.

The importer is intentionally deterministic: identifiers are content-derived,
Tiled flip flags are retained, and every used visual gid becomes an exact
32x32 material slice.  The authoring UI can therefore render the real town
instead of approximating it with palette colours.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from generative_agents.config.map_editor import (
    GridRect,
    HierarchyNode,
    MapEditorDocumentV2,
    MaterialSlice,
    MaterialSource,
    RecipeEntry,
    RenderRecipe,
    VisualLayer,
)


VILLAGE_ROOT = (
    Path(__file__).resolve().parents[1] / "frontend" / "static" / "assets" / "village"
)
TILEMAP_PATH = VILLAGE_ROOT / "tilemap" / "tilemap.json"
MAZE_PATH = VILLAGE_ROOT / "maze.json"

GID_MASK = 0x1FFFFFFF
FLIP_H = 0x80000000
FLIP_V = 0x40000000
FLIP_D = 0x20000000

VISUAL_LEVELS = {
    "Bottom Ground": "MAP",
    "Exterior Ground": "MAP",
    "Exterior Decoration L1": "SECTOR",
    "Exterior Decoration L2": "SECTOR",
    "Interior Ground": "ARENA",
    "Wall": "ARENA",
    "Interior Furniture L1": "GAME_OBJECT",
    "Interior Furniture L2": "GAME_OBJECT",
    "Foreground L1": "GAME_OBJECT",
    "Foreground L2": "GAME_OBJECT",
}


def _load_json(path: Path) -> dict[str, Any]:
    """加载`json`。

    参数:
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    return json.loads(path.read_text(encoding="utf-8"))


def _stable_id(prefix: str, *parts: object) -> str:
    """执行`stable``id`的内部处理，供当前模块或类复用。

    参数:
        prefix: 生成稳定键、日志名或路径名时使用的前缀。 类型：`str`。
        *parts: 传给底层调用的额外位置参数，顺序和含义与被调用接口保持一致。 类型：`object`。

    返回:
        返回处理后的文本或稳定标识。
    """
    body = "\x1f".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(body.encode('utf-8')).hexdigest()[:20]}"


def _source_for_gid(tilesets: list[dict[str, Any]], gid: int) -> dict[str, Any]:
    """执行`source``for``gid`的内部处理，供当前模块或类复用。

    参数:
        tilesets: 传入当前算法的`tilesets`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`list[dict[str, Any]]`。
        gid: 传入当前算法的`gid`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`int`。

    返回:
        返回以字段名或业务键组织的结构化映射。

    异常:
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """
    candidates = [item for item in tilesets if int(item["firstgid"]) <= gid]
    if not candidates:
        raise ValueError(f"gid {gid} does not belong to a tileset")
    source = max(candidates, key=lambda item: int(item["firstgid"]))
    local = gid - int(source["firstgid"])
    if local >= int(source.get("tilecount") or 0):
        raise ValueError(f"gid {gid} exceeds tileset {source['name']}")
    return source


def _transform(raw_gid: int) -> str:
    """执行`transform`的内部处理，供当前模块或类复用。

    参数:
        raw_gid: 传入当前算法的`raw``gid`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`int`。

    返回:
        返回处理后的文本或稳定标识。
    """
    suffix = ""
    if raw_gid & FLIP_H:
        suffix += "H"
    if raw_gid & FLIP_V:
        suffix += "V"
    if raw_gid & FLIP_D:
        suffix += "D"
    return f"FLIP_{suffix}" if suffix else "NONE"


def _bounds(coords: Iterable[tuple[int, int]]) -> GridRect:
    """执行`bounds`的内部处理，供当前模块或类复用。

    参数:
        coords: 传入当前算法的`coords`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`Iterable[tuple[int, int]]`。

    返回:
        返回 `GridRect` 类型的处理结果。
    """
    points = list(coords)
    if not points:
        return GridRect(x=0, y=0, width=1, height=1)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return GridRect(
        x=min(xs),
        y=min(ys),
        width=max(xs) - min(xs) + 1,
        height=max(ys) - min(ys) + 1,
    )


def _node_id(kind: str, address: tuple[str, ...]) -> str:
    """执行`node``id`的内部处理，供当前模块或类复用。

    参数:
        kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`str`。
        address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。 类型：`tuple[str, ...]`。

    返回:
        返回处理后的文本或稳定标识。
    """
    return _stable_id(kind.casefold(), *address)


def _build_hierarchy(
    maze: dict[str, Any], width: int, height: int
) -> tuple[list[HierarchyNode], dict[tuple[str, ...], list[tuple[int, int]]]]:
    """构建`hierarchy`。

    参数:
        maze: 提供格子、地址、可通行性与寻路能力的地图实例。 类型：`dict[str, Any]`。
        width: 地图、图像或矩形区域的宽度。 类型：`int`。
        height: 地图、图像或矩形区域的高度。 类型：`int`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    address_coords: dict[tuple[str, ...], list[tuple[int, int]]] = defaultdict(list)
    for tile in maze.get("tiles", []):
        address = tuple(str(item) for item in tile.get("address", []) if item)
        coord = tile.get("coord")
        if not address or not isinstance(coord, list) or len(coord) != 2:
            continue
        x, y = int(coord[0]), int(coord[1])
        for depth in range(1, min(3, len(address)) + 1):
            address_coords[address[:depth]].append((x, y))

    root_id = _node_id("WORLD", (str(maze.get("world") or "the Ville"),))
    nodes = [
        HierarchyNode(
            id=root_id,
            kind="WORLD",
            name=str(maze.get("world") or "the Ville"),
            bounds=GridRect(x=0, y=0, width=width, height=height),
            semantic="整个可运行世界",
        )
    ]
    kind_for_depth = {1: "SECTOR", 2: "ARENA", 3: "GAME_OBJECT"}
    for address in sorted(address_coords, key=lambda value: (len(value), value)):
        kind = kind_for_depth[len(address)]
        parent_id = (
            root_id
            if len(address) == 1
            else _node_id(kind_for_depth[len(address) - 1], address[:-1])
        )
        rect = _bounds(address_coords[address])
        mask = sorted(
            [[x - rect.x, y - rect.y] for x, y in set(address_coords[address])],
            key=lambda point: (point[1], point[0]),
        )
        siblings = [key for key in address_coords if key[:-1] == address[:-1]]
        nodes.append(
            HierarchyNode(
                id=_node_id(kind, address),
                kind=kind,
                parent_id=parent_id,
                name=address[-1],
                sort_order=sorted(siblings).index(address),
                bounds=rect,
                semantic=" → ".join((str(maze.get("world") or "the Ville"), *address)),
                extensions={"address": list(address), "mask": mask},
            )
        )
    return nodes, address_coords


def _component_recipes(
    *,
    visual_layers: list[VisualLayer],
    hierarchy_nodes: list[HierarchyNode],
    slice_for_gid: dict[int, str],
    width: int,
    height: int,
) -> list[RenderRecipe]:
    """从连通的对象图层像素生成可复用、可编辑的叶节点配方。

    参数:
        visual_layers: 传入当前算法的`visual``layers`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`list[VisualLayer]`。
        hierarchy_nodes: 传入当前算法的`hierarchy``nodes`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`list[HierarchyNode]`。
        slice_for_gid: 传入当前算法的`slice``for``gid`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict[int, str]`。
        width: 地图、图像或矩形区域的宽度。 类型：`int`。
        height: 地图、图像或矩形区域的高度。 类型：`int`。

    返回:
        返回按接口约定组织的结果集合。

    说明:
        同一连通分量只生成一个配方；坐标归一化相对于分量左上角，确保配方可在其他位置复用。
    """

    go_nodes = [item for item in hierarchy_nodes if item.kind == "GAME_OBJECT"]
    node_by_cell: dict[tuple[int, int], HierarchyNode] = {}
    for node in go_nodes:
        for offset in node.extensions.get("mask", []):
            node_by_cell[
                (node.bounds.x + int(offset[0]), node.bounds.y + int(offset[1]))
            ] = node

    assigned: dict[str, list[tuple[int, int, int, int]]] = defaultdict(list)
    for layer in visual_layers:
        if layer.display_level != "GAME_OBJECT":
            continue
        for index, raw in enumerate(layer.raw_gids):
            gid = int(raw) & GID_MASK
            if not gid:
                continue
            x, y = index % width, index // width
            node = node_by_cell.get((x, y))
            if node is not None:
                assigned[node.id].append((x, y, layer.z_index, int(raw)))

    recipes: list[RenderRecipe] = []
    recipe_by_fingerprint: dict[str, str] = {}
    for node in go_nodes:
        entries = assigned.get(node.id, [])
        if not entries:
            continue
        min_x = min(item[0] for item in entries)
        min_y = min(item[1] for item in entries)
        normalized = sorted((x - min_x, y - min_y, z, raw) for x, y, z, raw in entries)
        fingerprint = hashlib.sha256(
            json.dumps(normalized, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        recipe_id = recipe_by_fingerprint.get(fingerprint)
        if recipe_id is None:
            recipe_id = _stable_id("recipe", fingerprint)
            recipe_by_fingerprint[fingerprint] = recipe_id
            recipes.append(
                RenderRecipe(
                    id=recipe_id,
                    name=node.name,
                    width_tiles=max(item[0] for item in normalized) + 1,
                    height_tiles=max(item[1] for item in normalized) + 1,
                    entries=[
                        RecipeEntry(
                            slice_id=slice_for_gid[raw & GID_MASK],
                            x=x,
                            y=y,
                            z_index=z,
                            transform=_transform(raw),
                            source_raw_gid=raw,
                        )
                        for x, y, z, raw in normalized
                    ],
                    imported=True,
                    fingerprint=fingerprint,
                )
            )
        node.render_recipe_id = recipe_id
    return recipes


@lru_cache(maxsize=1)
def import_ville_editor_document() -> MapEditorDocumentV2:
    """执行 的`import``ville``editor``document`操作。

    返回:
        返回 `MapEditorDocumentV2` 类型的处理结果。
    """
    tilemap = _load_json(TILEMAP_PATH)
    maze = _load_json(MAZE_PATH)
    width, height = int(tilemap["width"]), int(tilemap["height"])
    visual_json = [
        item
        for item in tilemap["layers"]
        if item.get("type") == "tilelayer"
        and item.get("name", "").strip() in VISUAL_LEVELS
    ]
    used_gids = sorted(
        {
            int(raw) & GID_MASK
            for layer in visual_json
            for raw in layer.get("data", [])
            if int(raw) & GID_MASK
        }
    )
    tilesets = sorted(tilemap["tilesets"], key=lambda item: int(item["firstgid"]))
    used_sources = {_source_for_gid(tilesets, gid)["name"] for gid in used_gids}

    sources: list[MaterialSource] = []
    source_id_by_name: dict[str, str] = {}
    for item in tilesets:
        if item["name"] not in used_sources:
            continue
        source_id = _stable_id("source", item["name"])
        source_id_by_name[item["name"]] = source_id
        # The repository intentionally flattens Tiled's original authoring
        # directories into ``village/tilemap`` for web delivery.
        image_path = Path(str(item["image"])).name
        sources.append(
            MaterialSource(
                id=source_id,
                name=item["name"],
                kind="BUNDLED",
                bundled_path=f"tilemap/{image_path}",
                media_type="image/png",
                width_px=int(item["imagewidth"]),
                height_px=int(item["imageheight"]),
                tile_width=int(item["tilewidth"]),
                tile_height=int(item["tileheight"]),
                columns=int(item["columns"]),
                rows=(int(item["tilecount"]) + int(item["columns"]) - 1)
                // int(item["columns"]),
                tile_count=int(item["tilecount"]),
                margin=int(item.get("margin") or 0),
                spacing=int(item.get("spacing") or 0),
                first_gid=int(item["firstgid"]),
            )
        )

    slices: list[MaterialSlice] = []
    slice_for_gid: dict[int, str] = {}
    for gid in used_gids:
        tileset = _source_for_gid(tilesets, gid)
        local = gid - int(tileset["firstgid"])
        columns = int(tileset["columns"])
        column, row = local % columns, local // columns
        tile_width, tile_height = int(tileset["tilewidth"]), int(tileset["tileheight"])
        spacing, margin = (
            int(tileset.get("spacing") or 0),
            int(tileset.get("margin") or 0),
        )
        slice_id = _stable_id("slice", tileset["name"], local)
        slice_for_gid[gid] = slice_id
        slices.append(
            MaterialSlice(
                id=slice_id,
                source_id=source_id_by_name[tileset["name"]],
                name=f"{tileset['name']} · {local}",
                kind="TILE",
                grid_rect=GridRect(x=column, y=row, width=1, height=1),
                pixel_rect=GridRect(
                    x=margin + column * (tile_width + spacing),
                    y=margin + row * (tile_height + spacing),
                    width=tile_width,
                    height=tile_height,
                ),
                indexed_gid=gid,
                local_tile_id=local,
                readonly_indexed=False,
            )
        )

    visual_layers = [
        VisualLayer(
            id=_stable_id("layer", item["name"].strip()),
            name=item["name"].strip(),
            display_level=VISUAL_LEVELS[item["name"].strip()],
            z_index=index,
            width=width,
            height=height,
            raw_gids=[int(raw) for raw in item["data"]],
            visible=bool(item.get("visible", True)),
            opacity=float(item.get("opacity", 1)),
        )
        for index, item in enumerate(visual_json)
    ]
    hierarchy_nodes, _address_coords = _build_hierarchy(maze, width, height)
    recipes = _component_recipes(
        visual_layers=visual_layers,
        hierarchy_nodes=hierarchy_nodes,
        slice_for_gid=slice_for_gid,
        width=width,
        height=height,
    )
    collisions = sorted(
        [
            list(map(int, tile["coord"]))
            for tile in maze.get("tiles", [])
            if tile.get("collision")
        ],
        key=lambda point: (point[1], point[0]),
    )
    document = MapEditorDocumentV2(
        root_node_id=hierarchy_nodes[0].id,
        material_sources=sources,
        material_slices=slices,
        render_recipes=recipes,
        visual_layers=visual_layers,
        hierarchy_nodes=hierarchy_nodes,
        import_metadata={
            "importer": "ville-tiled/v2",
            "tilemap_path": "tilemap/tilemap.json",
            "maze_path": "maze.json",
            "width": width,
            "height": height,
            "tile_size": int(tilemap["tilewidth"]),
            "used_gid_count": len(used_gids),
            "collision_coords": collisions,
            "source_sha256": hashlib.sha256(
                TILEMAP_PATH.read_bytes() + b"\0" + MAZE_PATH.read_bytes()
            ).hexdigest(),
        },
    )
    return document


def fresh_ville_editor_document() -> MapEditorDocumentV2:
    """执行 的`fresh``ville``editor``document`操作。

    返回:
        返回 `MapEditorDocumentV2` 类型的处理结果。
    """

    return MapEditorDocumentV2.model_validate(
        import_ville_editor_document().model_dump(mode="json")
    )


__all__ = ["fresh_ville_editor_document", "import_ville_editor_document"]
