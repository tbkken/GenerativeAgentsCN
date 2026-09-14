from copy import deepcopy
import pytest
from pydantic import ValidationError
from generative_agents.config.map_editor import MapEditorDocumentV2, HierarchyNode
from generative_agents.persistence import create_database
from generative_agents.persistence.models import Base
from generative_agents.ga_studio.resources import StudioResourceService


def document():
    rect = {"x": 0, "y": 0, "width": 1, "height": 1}
    nodes = []
    parent = None
    for key, kind in [("world", "WORLD"), ("sector", "SECTOR"), ("arena", "ARENA"), ("desk", "GAME_OBJECT")]:
        nodes.append({"id": key, "kind": kind, "parent_id": parent, "name": key, "bounds": rect})
        parent = key
    nodes[-1].update(material_slice_id="before", state_appearance={"cases": [{"value": "已整理", "material_slice_id": "after"}]})
    return {"root_node_id": "world", "hierarchy_nodes": nodes, "material_sources": [{"id": "source", "name": "image", "kind": "BUNDLED", "bundled_path": "assets/image.png", "media_type": "image/png", "width_px": 32, "height_px": 32, "tile_width": 32, "tile_height": 32, "columns": 1, "rows": 1, "tile_count": 1}], "material_slices": [{"id": name, "source_id": "source", "name": name, "kind": "PIXEL", "grid_rect": rect, "pixel_rect": {**rect, "width": 32, "height": 32}} for name in ["before", "after"]]}


def test_all_visual_branches_are_validated_even_with_empty_initial_state():
    data = document()
    result = MapEditorDocumentV2.model_validate(data)
    assert result.hierarchy_nodes[-1].initial_state == {}
    assert result.hierarchy_nodes[-1].state_appearance.cases[0].value == "已整理"
    data["material_slices"].pop()
    with pytest.raises(ValidationError, match="已不可用"):
        MapEditorDocumentV2.model_validate(data)


def test_duplicate_blank_and_non_object_state_appearance_rejected():
    data = document()
    node = data["hierarchy_nodes"][-1]
    duplicate = deepcopy(node["state_appearance"]["cases"][0])
    node["state_appearance"]["cases"].append(duplicate)
    with pytest.raises(ValidationError, match="重复"):
        HierarchyNode.model_validate(node)
    node["state_appearance"]["cases"] = [{**duplicate, "value": " "}]
    with pytest.raises(ValidationError):
        HierarchyNode.model_validate(node)
    node["state_appearance"]["cases"] = [duplicate]
    node["kind"] = "ARENA"
    with pytest.raises(ValidationError, match="Game Object"):
        HierarchyNode.model_validate(node)


def test_chinese_crowds_receive_independent_stable_keys(tmp_path):
    database = create_database(f"sqlite:///{tmp_path / 'studio.sqlite'}")
    Base.metadata.create_all(database.engine)
    try:
        service = StudioResourceService(database)
        first = service.create_crowd(name="独立单人", agent_ids=[])
        second = service.create_crowd(name="独立单人", agent_ids=[])
        assert first["id"] != second["id"]
        assert first["crowd_key"] != second["crowd_key"]
    finally:
        database.close()


def test_visual_names_survive_package_semantic_index_and_maze():
    from generative_agents.ga_studio.builder import build_semantic_index
    from generative_agents.modules.maze import Maze
    import logging, random
    definition = {"world": "world", "size": [1,1], "tile_size": 32, "tile_address_keys": ["world", "sector", "arena", "game_object"], "editor_v2": document(), "tiles": [{"coord": [0,0], "address": ["world", "sector", "arena", "desk"], "collision": False}]}
    world, index = build_semantic_index({"definition": definition})
    assert next(node for node in index["nodes"] if node["id"] == "desk")["available_visual_states"] == ["已整理"]
    maze = Maze(world["definition"], logging.getLogger(__name__), random.Random(1))
    assert next(node for node in maze.semantic_nodes_in_scope((0,0),0) if node["id"] == "desk")["available_visual_states"] == ["已整理"]
