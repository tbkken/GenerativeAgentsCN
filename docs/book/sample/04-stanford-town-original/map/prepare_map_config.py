"""Prepare portable map materials only; never connect to Studio or Runtime."""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "src"))

from generative_agents.ga_studio.resources.map_importer import fresh_ville_editor_document
from generative_agents.ga_protocol.packages.resources import (
    ResourceRecord, ResourceRef, ResourceSet, read_resource_set,
    validate_resource_set, write_config_package,
)
from generative_agents.ga_protocol.packages.io import seal_directory, verify_integrity
from generative_agents.ga_protocol.packages.semantic_index import build_semantic_index
from generative_agents.ga_protocol.packages.validation import _validate_world_and_agents
from generative_agents.ga_protocol.spatial.navigation import collision_preview

ORIGINAL = Path(r"C:\Users\D304\Downloads\GenerativeAgentsCN-main\GenerativeAgentsCN-main\generative_agents\frontend\static\assets\village")
BUNDLED = REPO / "src/generative_agents/ga_studio/bundled/maps/village"
HERE = Path(__file__).resolve().parent
KEY = "stanford-town-original"
NAME = "斯坦福小镇·完整原版地图"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    maze = json.loads((BUNDLED / "maze.json").read_text(encoding="utf-8"))
    expanded_maze = json.loads((ORIGINAL / "maze.json").read_text(encoding="utf-8"))
    tiled = json.loads((ORIGINAL / "tilemap/tilemap.json").read_text(encoding="utf-8"))
    assert tiled == json.loads((BUNDLED / "tilemap/tilemap.json").read_text(encoding="utf-8"))
    assert maze["size"] == [100, 140]
    assert (tiled["width"], tiled["height"], tiled["tilewidth"], tiled["tileheight"]) == (140, 100, 32, 32)
    original_tiles = {tuple(t["coord"]): t for t in maze["tiles"]}
    expanded_tiles = {tuple(t["coord"]): t for t in expanded_maze["tiles"]}
    differences = [coord for coord in sorted(original_tiles.keys() | expanded_tiles.keys())
                   if original_tiles.get(coord) != expanded_tiles.get(coord)]
    assert differences == [(74, 20)]
    assert original_tiles[(74, 20)]["address"] == ["霍布斯咖啡馆", "咖啡馆"]

    editor = fresh_ville_editor_document().model_dump(mode="json")
    editor.pop("ui_state", None)
    files: dict[str, bytes] = {}
    sources = []
    for png in sorted((ORIGINAL / "tilemap").glob("*.png")):
        data = png.read_bytes()
        assert data == (BUNDLED / "tilemap" / png.name).read_bytes()
        relative = f"assets/{KEY}/tilemap/{png.name}"
        files[relative] = data
        sources.append({"source": str(png), "package_path": relative, "bytes": len(data), "sha256": sha(data)})
    assert len(files) == 16
    raw_inputs = [(ORIGINAL / "tilemap/tilemap.json", f"assets/{KEY}/tilemap/tilemap.json"),
                  (BUNDLED / "maze.json", f"assets/{KEY}/maze.json")]
    for source, relative in raw_inputs:
        data = source.read_bytes()
        files[relative] = data
        sources.append({"source": str(source), "package_path": relative, "bytes": len(data), "sha256": sha(data)})
    for material in editor["material_sources"]:
        relative = f"assets/{KEY}/{material['bundled_path']}"
        material.pop("asset_id", None)
        material.update(kind="BUNDLED", bundled_path=relative, asset_hash=sha(files[relative]))
    editor["import_metadata"].update(
        tilemap_path=f"assets/{KEY}/tilemap/tilemap.json",
        maze_path=f"assets/{KEY}/maze.json",
        source_basis="经典中文 example 25 人扩展前；排除书稿第 27 章新增社区公告板",
    )

    # The original Maze materializes omitted cells as walkable World-only tiles.
    # Expand that exact default explicitly for the current file protocol.
    definition = copy.deepcopy(maze)
    definition["size_unit"] = "TILE"
    definition["editor_v2"] = editor
    definition["tiles"] = []
    for y in range(100):
        for x in range(140):
            raw = original_tiles.get((x, y), {})
            definition["tiles"].append({"coord": [x, y], "collision": bool(raw.get("collision", False)),
                                        "address": [maze["world"], *raw.get("address", [])]})
    assets = [{"logical_path": path, "asset_hash": "sha256:" + sha(data),
               "media_type": "image/png" if path.endswith(".png") else "application/json", "size": len(data)}
              for path, data in sorted(files.items())]
    record = ResourceRecord(kind="map", key=KEY, name=NAME,
        description="完整 140×100 格中文经典小镇地图，保留原始图集、10 个视觉层、2064 个碰撞格与四层空间语义。",
        definition={"definition": definition, "assets": assets}, attachments=sorted(files))
    resources = ResourceSet(resources=[record], files=files, roots=[ResourceRef(kind="map", key=KEY)])
    validate_resource_set(resources)

    # Exercise the same file-only world/index contract used when sealing an experiment.
    prepared, index = build_semantic_index(dict(record.definition, world_key=KEY, world_name=NAME))
    _validate_world_and_agents(prepared, {"agents": []}, index)
    collision = collision_preview(copy.deepcopy(definition))
    original_blocked = {y * 140 + x for (x, y), tile in original_tiles.items() if tile.get("collision")}
    assert set(collision["blocked"]) == original_blocked
    assert len(original_blocked) == 2064
    for tile in prepared["definition"]["tiles"]:
        raw = original_tiles.get(tuple(tile["coord"]), {})
        assert tile["address"] == [maze["world"], *raw.get("address", [])]
        assert tile["collision"] == bool(raw.get("collision", False))
    by_id = {node["id"]: node for node in editor["hierarchy_nodes"]}
    mask_checks = 0
    for node in by_id.values():
        bounds = node["bounds"]
        assert 0 <= bounds["x"] < 140 and 0 <= bounds["y"] < 100
        assert bounds["x"] + bounds["width"] <= 140 and bounds["y"] + bounds["height"] <= 100
        if node["parent_id"]:
            parent = by_id[node["parent_id"]]["bounds"]
            assert parent["x"] <= bounds["x"] and parent["y"] <= bounds["y"]
            assert bounds["x"] + bounds["width"] <= parent["x"] + parent["width"]
            assert bounds["y"] + bounds["height"] <= parent["y"] + parent["height"]
        for ox, oy in node["extensions"].get("mask", []):
            assert 0 <= ox < bounds["width"] and 0 <= oy < bounds["height"]
            source = original_tiles[(bounds["x"] + ox, bounds["y"] + oy)]
            path = node["extensions"]["address"]
            assert source["address"][:len(path)] == path
            mask_checks += 1
    assert Counter(node["kind"] for node in by_id.values()) == {"WORLD": 1, "SECTOR": 19, "ARENA": 63, "GAME_OBJECT": 222}
    assert len(editor["material_slices"]) == 1272 and len(editor["visual_layers"]) == 10
    for actual, expected in zip(editor["visual_layers"], tiled["layers"][:10]):
        assert actual["raw_gids"] == expected["data"]

    directory = write_config_package(HERE / "config", resources, name=NAME)
    archive = seal_directory(directory, HERE / "stanford-town-original-map-config.zip")
    reread = read_resource_set(archive)
    assert reread.files == files
    assert reread.resources[0].definition == record.definition
    integrity = verify_integrity(directory)
    report = {"status": "PASSED_OFFLINE_ONLY", "archive": str(archive), "bytes": archive.stat().st_size,
              "sha256": sha(archive.read_bytes()), "root_sha256": integrity["root_sha256"],
              "width_tiles": 140, "height_tiles": 100, "tile_size": 32, "tile_count": 14000,
              "source_sparse_tiles": 4201, "collision_tiles": 2064, "hierarchy": dict(Counter(node["kind"] for node in by_id.values())),
              "visual_layers": 10, "used_material_sources": len(editor["material_sources"]), "material_slices": 1272,
              "png_attachments": 16, "total_attachments": len(files), "semantic_mask_checks": mask_checks,
              "board_basis_difference": {"coord": [74, 20], "classic": original_tiles[(74, 20)], "expanded": expanded_tiles[(74, 20)]},
              "sources": sources,
              "scope": "离线素材与协议校验；未导入 Studio，未配置或运行实验；原版全部对象保持静态，未额外绑定对象 Skill。"}
    (HERE / "offline-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"sources", "board_basis_difference"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
