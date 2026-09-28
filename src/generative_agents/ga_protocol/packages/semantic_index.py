"""Derive the shared spatial index from portable map content."""
from __future__ import annotations
import copy
import hashlib
from collections.abc import Mapping, Sequence
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.schemas.world import WorldDocument

def _portable_editor(document: dict) -> dict:
    value = copy.deepcopy(document)
    value.pop("ui_state", None)
    value.pop("import_metadata", None)
    value["material_sources"] = [{key: item for key, item in source.items() if key != "asset_id"}
                                 for source in value.get("material_sources", [])]
    return WorldDocument.model_validate(value).model_dump(mode="json")

def _semantic_node_id(kind: str, address: Sequence[str]) -> str:
    digest = hashlib.sha256(
        (kind + "\0" + "\0".join(address)).encode("utf-8")
    ).hexdigest()[:24]
    return f"semantic-{kind.casefold()}-{digest}"


def build_semantic_index(world: dict) -> tuple[dict, dict]:
    """Build one package-owned hierarchy and coordinate lookup.

    Author-provided node IDs are retained.  Maps without an editor hierarchy
    receive deterministic package-local IDs derived from their full address.
    """

    prepared = copy.deepcopy(world)
    definition = prepared.get("definition")
    if not isinstance(definition, dict):
        raise PackageError("definition.world.definition must be an object")
    from generative_agents.ga_protocol.spatial.navigation import compile_collision

    if definition.get("editor_v2"):
        definition["editor_v2"] = _portable_editor(definition["editor_v2"])
        compile_collision(definition)
    tiles = definition.get("tiles")
    if not isinstance(tiles, list):
        raise PackageError("definition.world.definition.tiles must be an array")
    editor = definition.get("editor_v2")
    raw_nodes = editor.get("hierarchy_nodes") if isinstance(editor, dict) else None
    nodes: dict[str, dict] = {}
    path_to_id: dict[tuple[str, ...], str] = {}

    if isinstance(raw_nodes, list) and raw_nodes:
        source_by_id = {
            str(item.get("id")): item
            for item in raw_nodes
            if isinstance(item, Mapping) and str(item.get("id") or "").strip()
        }
        if len(source_by_id) != len(raw_nodes):
            raise PackageError("semantic hierarchy node IDs must be present and unique")

        def address_for(node: Mapping) -> list[str]:
            parts: list[str] = []
            current: Mapping | None = node
            visited: set[str] = set()
            while current is not None:
                node_id = str(current.get("id") or "")
                if not node_id or node_id in visited:
                    raise PackageError(f"semantic hierarchy contains a cycle at {node_id}")
                visited.add(node_id)
                parts.append(str(current.get("name") or node_id))
                parent_id = str(current.get("parent_id") or "")
                current = source_by_id.get(parent_id) if parent_id else None
            return list(reversed(parts))

        for node_id, source in source_by_id.items():
            address = address_for(source)
            bounds = source.get("bounds") if isinstance(source.get("bounds"), Mapping) else {}
            node = {
                "id": node_id,
                "kind": str(source.get("kind") or "").upper(),
                "name": str(source.get("name") or node_id),
                "semantic": str(source.get("semantic") or ""),
                "parent_id": str(source.get("parent_id") or "") or None,
                "address": address,
                "bounds": {
                    "x": int(bounds.get("x") or 0),
                    "y": int(bounds.get("y") or 0),
                    "width": max(1, int(bounds.get("width") or 1)),
                    "height": max(1, int(bounds.get("height") or 1)),
                },
                "skill_bindings": copy.deepcopy(source.get("skill_bindings") or []),
                "available_visual_states": [case["value"] for case in (source.get("state_appearance") or {}).get("cases", [])],
            }
            nodes[node_id] = node
            path_to_id[tuple(address)] = node_id
    else:
        kind_by_level = ("WORLD", "SECTOR", "ARENA", "GAME_OBJECT")
        extents: dict[str, list[int]] = {}
        for tile in tiles:
            if not isinstance(tile, Mapping):
                continue
            address = tile.get("address")
            coord = tile.get("coord")
            if not isinstance(address, list) or not isinstance(coord, list) or len(coord) != 2:
                continue
            parent_id = None
            for level, name in enumerate(address[:4]):
                path = tuple(str(part) for part in address[: level + 1])
                kind = kind_by_level[level]
                node_id = path_to_id.setdefault(path, _semantic_node_id(kind, path))
                if node_id not in nodes:
                    nodes[node_id] = {
                        "id": node_id,
                        "kind": kind,
                        "name": str(name),
                        "semantic": "",
                        "parent_id": parent_id,
                        "address": list(path),
                        "bounds": {},
                        "skill_bindings": [],
                    }
                    extents[node_id] = [coord[0], coord[1], coord[0], coord[1]]
                else:
                    extent = extents[node_id]
                    extent[0] = min(extent[0], coord[0])
                    extent[1] = min(extent[1], coord[1])
                    extent[2] = max(extent[2], coord[0])
                    extent[3] = max(extent[3], coord[1])
                parent_id = node_id
        for node_id, extent in extents.items():
            x_min, y_min, x_max, y_max = extent
            nodes[node_id]["bounds"] = {
                "x": x_min,
                "y": y_min,
                "width": x_max - x_min + 1,
                "height": y_max - y_min + 1,
            }

    coordinate_paths: dict[str, list[str]] = {}
    for tile in tiles:
        if not isinstance(tile, dict):
            continue
        address = tile.get("address")
        coord = tile.get("coord")
        if not isinstance(address, list) or not isinstance(coord, list) or len(coord) != 2:
            continue
        node_ids = [
            path_to_id[tuple(str(part) for part in address[:level])]
            for level in range(1, min(4, len(address)) + 1)
            if tuple(str(part) for part in address[:level]) in path_to_id
        ]
        coordinate_paths[f"{coord[0]},{coord[1]}"] = node_ids
        tile["spatial_semantics"] = [
            {
                "id": nodes[node_id]["id"],
                "kind": nodes[node_id]["kind"],
                "name": nodes[node_id]["name"],
                "semantic": nodes[node_id]["semantic"],
            }
            for node_id in node_ids
        ]

    semantic_index = {
        "schema_version": 1,
        "levels": ["WORLD", "SECTOR", "ARENA", "GAME_OBJECT"],
        "nodes": sorted(
            nodes.values(),
            key=lambda node: (len(node["address"]), node["address"], node["id"]),
        ),
        "coordinate_paths": dict(sorted(coordinate_paths.items())),
    }
    definition["semantic_index"] = copy.deepcopy(semantic_index)
    return prepared, semantic_index

