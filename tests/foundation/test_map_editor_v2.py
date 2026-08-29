"""基础能力回归测试：覆盖 ``test_map_editor_v2`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from generative_agents.config.map_editor import (
    GridRect,
    MapEditorDocumentV2,
    MaterialSlice,
    TileOverridePart,
)
from generative_agents.config.schema import WorldConfig
from generative_agents.services.map_importer import fresh_ville_editor_document
from generative_agents.services.maps import (
    _compile_editor_v2_runtime_addresses,
    _validate_map_editor_v2,
)
from generative_agents.web.app import create_app


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "generative_agents" / "web" / "static"


def test_publish_compiler_turns_editor_tree_into_runtime_tile_addresses():
    """回归验证 ``test_publish_compiler_turns_editor_tree_into_runtime_tile_addresses`` 所描述的业务结果、故障边界和隔离约束。"""
    nodes = [
        {
            "id": "world",
            "kind": "WORLD",
            "parent_id": None,
            "name": "晨间通勤世界",
            "sort_order": 0,
            "bounds": {"x": 0, "y": 0, "width": 4, "height": 4},
        },
        {
            "id": "sector-wide",
            "kind": "SECTOR",
            "parent_id": "world",
            "name": "宽街区",
            "sort_order": 0,
            "bounds": {"x": 0, "y": 0, "width": 4, "height": 4},
        },
        {
            "id": "sector-specific",
            "kind": "SECTOR",
            "parent_id": "world",
            "name": "中央大道",
            "sort_order": 1,
            "bounds": {"x": 1, "y": 1, "width": 2, "height": 2},
        },
        {
            "id": "arena",
            "kind": "ARENA",
            "parent_id": "sector-specific",
            "name": "人行横道",
            "sort_order": 0,
            "bounds": {"x": 1, "y": 1, "width": 2, "height": 2},
        },
        {
            "id": "object",
            "kind": "GAME_OBJECT",
            "parent_id": "arena",
            "name": "等待区",
            "sort_order": 0,
            "bounds": {"x": 1, "y": 1, "width": 1, "height": 1},
        },
    ]
    world = WorldConfig.model_validate(
        {
            "world_key": "commute",
            "world_name": "旧世界名",
            "definition": {
                "world": "旧世界名",
                "tile_size": 32,
                "size": [4, 4],
                "tile_address_keys": ["world", "sector", "arena", "game_object"],
                "tiles": [
                    {"coord": [x, y], "collision": False, "address": ["旧世界名"]}
                    for y in range(4)
                    for x in range(4)
                ],
                "editor_v2": {
                    "schema_version": "ga-map-editor/v2",
                    "root_node_id": "world",
                    "hierarchy_nodes": nodes,
                },
            },
        }
    )

    compiled = _compile_editor_v2_runtime_addresses(world)
    tiles = {tuple(tile["coord"]): tile for tile in compiled.definition["tiles"]}

    assert compiled.world_name == "晨间通勤世界"
    assert compiled.definition["world"] == "晨间通勤世界"
    assert tiles[(0, 0)]["address"] == ["晨间通勤世界", "宽街区"]
    assert tiles[(1, 1)]["address"] == [
        "晨间通勤世界",
        "中央大道",
        "人行横道",
        "等待区",
    ]


def test_map_validation_warns_when_every_game_object_is_static():
    world = WorldConfig.model_validate(
        {
            "world_key": "static-world",
            "world_name": "Static World",
            "definition": {
                "world": "Static World",
                "tile_size": 32,
                "size": [1, 1],
                "tile_address_keys": ["world", "sector", "arena", "game_object"],
                "tiles": [
                    {"coord": [0, 0], "collision": False, "address": ["Static World"]}
                ],
                "editor_v2": {
                    "schema_version": "ga-map-editor/v2",
                    "root_node_id": "world",
                    "hierarchy_nodes": [
                        {
                            "id": "world",
                            "kind": "WORLD",
                            "name": "Static World",
                            "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                        },
                        {
                            "id": "sector",
                            "kind": "SECTOR",
                            "parent_id": "world",
                            "name": "Street",
                            "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                        },
                        {
                            "id": "arena",
                            "kind": "ARENA",
                            "parent_id": "sector",
                            "name": "Square",
                            "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                        },
                        {
                            "id": "object",
                            "kind": "GAME_OBJECT",
                            "parent_id": "arena",
                            "name": "Bench",
                            "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                            "interaction_mode": "STATIC",
                        },
                    ],
                },
            },
        }
    )

    errors, warnings = _validate_map_editor_v2(world)

    assert errors == []
    assert [warning["code"] for warning in warnings] == ["ALL_GAME_OBJECTS_STATIC"]


def test_skill_bound_game_object_requires_a_passive_skill_binding():
    with pytest.raises(ValidationError, match="require a passive Skill"):
        MapEditorDocumentV2.model_validate(
            {
                "root_node_id": "world",
                "hierarchy_nodes": [
                    {
                        "id": "world",
                        "kind": "WORLD",
                        "name": "World",
                        "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                    },
                    {
                        "id": "object",
                        "kind": "GAME_OBJECT",
                        "parent_id": "world",
                        "name": "Door",
                        "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                        "interaction_mode": "SKILL_BOUND",
                    },
                ],
            }
        )


def test_ville_import_is_lossless_for_used_visual_materials():
    """回归验证 ``test_ville_import_is_lossless_for_used_visual_materials`` 所描述的业务结果、故障边界和隔离约束。"""
    document = fresh_ville_editor_document()

    assert document.schema_version == "ga-map-editor/v2"
    assert document.import_metadata["width"] == 140
    assert document.import_metadata["height"] == 100
    assert document.import_metadata["used_gid_count"] == 1272
    assert len(document.material_slices) == 1272
    assert not any("purpose" in item.model_dump() for item in document.material_slices)
    assert document.material_canvases == []
    assert len(document.visual_layers) == 10
    assert all(len(layer.raw_gids) == 14_000 for layer in document.visual_layers)
    assert all(source.bundled_path.startswith("tilemap/") for source in document.material_sources)
    assert all("map_assets/" not in source.bundled_path for source in document.material_sources)


def test_ville_import_builds_exact_four_level_address_tree():
    """回归验证 ``test_ville_import_builds_exact_four_level_address_tree`` 所描述的业务结果、故障边界和隔离约束。"""
    document = fresh_ville_editor_document()
    kinds = Counter(node.kind for node in document.hierarchy_nodes)

    assert kinds == {
        "WORLD": 1,
        "SECTOR": 19,
        "ARENA": 63,
        "GAME_OBJECT": 222,
    }
    assert not any(node.kind not in kinds for node in document.hierarchy_nodes)
    assert all(
        node.parent_id is not None
        for node in document.hierarchy_nodes
        if node.kind != "WORLD"
    )


def test_map_editor_document_and_real_tiles_are_served(database_url):
    """回归验证 ``test_map_editor_document_and_real_tiles_are_served`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        document = client.get("/api/v1/map-editor/ville-document")
        source = client.get(
            "/generative_agents/frontend/static/assets/village/"
            "tilemap/CuteRPG_Field_B.png"
        )
        editor = client.get("/static/console/map-editor-v2.js")

    assert document.status_code == 200
    assert document.json()["schema_version"] == "ga-map-editor/v2"
    assert len(document.json()["material_slices"]) == 1272
    assert not any("purpose" in item for item in document.json()["material_slices"])
    assert document.json()["material_canvases"] == []
    assert source.status_code == 200
    assert source.headers["content-type"] == "image/png"
    assert editor.status_code == 200
    assert "class MapEditorV2" in editor.text


def test_formal_map_editor_contains_only_world_and_material_tabs():
    """回归验证 ``test_formal_map_editor_contains_only_world_and_material_tabs`` 所描述的业务结果、故障边界和隔离约束。"""
    source = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")

    assert 'data-me2-tab="map"' not in source
    assert 'data-me2-tab="world"' in source
    assert 'data-me2-tab="materials"' in source
    assert 'data-me2-tab="layers"' not in source
    assert 'data-me2-tab="assets"' not in source
    assert "视觉变体" not in source
    assert "Game Object 配置" not in source
    assert "空间资产" not in source
    assert "显示至" in source
    assert "四层地址树" in source
    assert "应用列表" in source
    assert "data-new-canvas" in source
    assert "data-upload-source" in source
    assert "group('canvases', '画布'" in source
    assert "group('sources', '原图'" in source
    assert "kind: 'CANVAS'" in source
    assert "显示素材<select" in source
    assert "世界素材<select" not in source
    assert "this.requestEditableAction('new-canvas')" in source
    assert "map-editor-v2:request-edit" in source
    assert "data-new-canvas ${this.readonly ? 'disabled' : ''}" not in source
    assert "this.workspace = 'materials'; this.selectedCanvasId = id" in source


def test_formal_map_editor_fills_the_remaining_viewport_height():
    """回归验证 ``test_formal_map_editor_fills_the_remaining_viewport_height`` 所描述的业务结果、故障边界和隔离约束。"""
    styles = (STATIC / "map-workspace.css").read_text(encoding="utf-8")

    assert "body.map-editor-mode .content" in styles
    assert "height: calc(100dvh - var(--topbar-height));" in styles
    assert "body.map-editor-mode .map-editor-v2" in styles
    assert "grid-template-rows: 54px auto minmax(0, 1fr);" in styles
    assert "body.map-editor-mode .map-editor-v2 > .me2-build-guide" in styles
    assert "body.map-editor-mode .me2-layout" in styles
    assert "height: min(760px, calc(100vh - 235px));" not in styles


def test_material_canvas_and_world_expose_only_relevant_tools():
    """回归验证 ``test_material_canvas_and_world_expose_only_relevant_tools`` 所描述的业务结果、故障边界和隔离约束。"""
    source = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")

    assert 'data-me2-tool="select"' not in source
    for tool in ("brush", "fill", "eraser", "pan"):
        assert f'data-me2-tool="{tool}"' in source
    assert 'data-map-undo title="撤回上一次画布绘制"' in source
    assert 'data-map-redo title="重做上一次画布绘制"' in source
    assert "this.root.querySelector('[data-map-tools]').hidden = !canvasEditing" in source
    assert "button.hidden = !canvasEditing" in source
    assert "this.root.querySelector('[data-map-history]').hidden = !canvasEditing" in source
    assert "beginMapEdit('绘制画布')" in source
    assert "beginMapEdit('擦除画布')" in source
    assert "beginMapEdit('填充画布')" in source
    assert "undoMapEdit()" in source
    assert "redoMapEdit()" in source
    assert "data-brush-popover" in source
    assert "mouseenter" in source
    assert "this.brushPaletteOpen = !this.brushPaletteOpen" not in source
    assert "clearTimeout(this.brushCloseTimer);" in source
    assert "popover?.contains(document.activeElement)" in source
    assert "this.refreshCanvasImages();\n      if (this.inspector) this.renderInspector();" in source


def test_world_canvas_moves_only_the_selected_non_root_node_and_pans_elsewhere():
    """回归验证 ``test_world_canvas_moves_only_the_selected_non_root_node_and_pans_elsewhere`` 所描述的业务结果、故障边界和隔离约束。"""
    source = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")
    styles = (STATIC / "map-workspace.css").read_text(encoding="utf-8")

    assert "type: 'move-node'" in source
    assert "worldInteraction: true" in source
    assert "dragWorldNode(point)" in source
    assert "selected?.kind !== 'WORLD'" in source
    assert ".me2-canvas-host.is-node-move { cursor: move; }" in styles

    editor_path = json.dumps((STATIC / "map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
global.CustomEvent = class CustomEvent {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const root = {{id:'root', kind:'WORLD', bounds:{{x:0,y:0,width:20,height:15}}}};
const selected = {{id:'sector', kind:'SECTOR', bounds:{{x:5,y:4,width:4,height:3}}, extensions:{{mask:'old'}}}};
const other = {{id:'arena', kind:'ARENA', bounds:{{x:12,y:8,width:2,height:2}}}};
editor.document = {{import_metadata:{{width:20,height:15}}, hierarchy_nodes:[root, selected, other]}};
editor.nodeById = new Map([[root.id,root],[selected.id,selected],[other.id,other]]);
editor.selectedNodeId = selected.id; editor.workspace = 'world'; editor.readonly = false;
editor.tool = 'world'; editor.zoom = 1; editor.renderTile = 16; editor.offsetX = 0; editor.offsetY = 0;
editor._changed = false; editor._changeRevision = 0; editor._changeNotificationQueued = false;
const inputs = {{x:{{value:''}},y:{{value:''}}}};
editor.root = {{focus:()=>{{}}, dispatchEvent:()=>{{}}}};
editor.inspector = {{querySelector: selector => selector.includes('node-x') ? inputs.x : selector.includes('node-y') ? inputs.y : null}};
editor.canvas = {{setPointerCapture:()=>{{}},hasPointerCapture:()=>false,releasePointerCapture:()=>{{}}}};
editor.canvasHost = null; editor.renderCanvas = () => {{}};
editor.localPoint = event => ({{x:event.x,y:event.y}});
let selectedByClick = '';
editor.selectNode = id => {{ selectedByClick = id; editor.selectedNodeId = id; }};

editor.pointerDown({{button:0,pointerId:1,x:88,y:72}});
assert.equal(editor.drag.type, 'move-node');
editor.pointerMove({{x:120,y:88}});
assert.deepEqual(selected.bounds, {{x:7,y:5,width:4,height:3}});
assert.equal(inputs.x.value, '7'); assert.equal(inputs.y.value, '5');
assert.equal(selected.extensions.mask, undefined);
assert.equal(editor.changed, true);
editor.pointerUp({{pointerId:1,x:120,y:88}});

editor.offsetX = 0; editor.offsetY = 0; editor.selectedNodeId = selected.id;
editor.pointerDown({{button:0,pointerId:2,x:200,y:136}});
assert.equal(editor.drag.type, 'pan');
editor.pointerMove({{x:216,y:152}});
assert.equal(editor.offsetX, 16); assert.equal(editor.offsetY, 16);
editor.pointerUp({{pointerId:2,x:216,y:152}});
assert.equal(selectedByClick, '', 'dragging over an unselected node must pan without changing selection');

editor.offsetX = 0; editor.offsetY = 0; editor.selectedNodeId = selected.id;
editor.pointerDown({{button:0,pointerId:3,x:200,y:136}});
editor.pointerUp({{pointerId:3,x:200,y:136}});
assert.equal(selectedByClick, other.id, 'a click without dragging still selects the node');

editor.selectedNodeId = root.id; editor.offsetX = 0; editor.offsetY = 0;
editor.pointerDown({{button:0,pointerId:4,x:32,y:32}});
assert.equal(editor.drag.type, 'pan', 'the World root is fixed and its surface pans the canvas');
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_world_and_material_inspectors_expose_safe_delete_actions():
    """回归验证 ``test_world_and_material_inspectors_expose_safe_delete_actions`` 所描述的业务结果、故障边界和隔离约束。"""
    source = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")
    styles = (STATIC / "map-workspace.css").read_text(encoding="utf-8")

    assert "data-delete-node" in source
    assert "data-delete-slice" in source
    assert "data-delete-source" in source
    assert "node.kind === 'WORLD'" in source
    assert "removeMaterialReferences(sliceIds)" in source
    assert ".me2-danger" in styles


def test_deleting_world_nodes_cascades_and_material_deletion_cleans_references():
    """回归验证 ``test_deleting_world_nodes_cascades_and_material_deletion_cleans_references`` 所描述的业务结果、故障边界和隔离约束。"""
    editor_path = json.dumps((STATIC / "map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{confirm: () => true}};
global.requestAnimationFrame = callback => callback();
require({editor_path});
const proto = window.MapEditorV2.prototype;

const nodeEditor = Object.create(proto);
const root = {{id:'root',kind:'WORLD',parent_id:null,name:'World',bounds:{{x:0,y:0,width:20,height:20}}}};
const sector = {{id:'sector',kind:'SECTOR',parent_id:'root',name:'Sector',bounds:{{x:1,y:1,width:5,height:5}}}};
const arena = {{id:'arena',kind:'ARENA',parent_id:'sector',name:'Arena',bounds:{{x:2,y:2,width:2,height:2}}}};
const object = {{id:'object',kind:'GAME_OBJECT',parent_id:'arena',name:'Object',bounds:{{x:2,y:2,width:1,height:1}}}};
const sibling = {{id:'sibling',kind:'SECTOR',parent_id:'root',name:'Sibling',bounds:{{x:8,y:8,width:3,height:3}}}};
nodeEditor.document = {{root_node_id:'root', hierarchy_nodes:[root,sector,arena,object,sibling]}};
nodeEditor.nodeById = new Map(nodeEditor.document.hierarchy_nodes.map(node => [node.id,node]));
nodeEditor.expandedNodes = new Set(['root','sector','arena']); nodeEditor.readonly = false;
nodeEditor._changed = false; nodeEditor._changeRevision = 0; nodeEditor._changeNotificationQueued = false;
nodeEditor.root = {{}}; nodeEditor.renderAll = () => {{}}; nodeEditor.toast = () => {{}};
nodeEditor.reindex = function () {{ this.nodeById = new Map(this.document.hierarchy_nodes.map(node => [node.id,node])); }};
nodeEditor.deleteWorldNode(sector);
assert.deepEqual(nodeEditor.document.hierarchy_nodes.map(node => node.id), ['root','sibling']);
assert.equal(nodeEditor.selectedNodeId, 'root');
assert.equal(nodeEditor.changed, true);

const materialEditor = Object.create(proto);
const target = {{id:'target',source_id:'source-a',name:'Target',indexed_gid:7}};
const keep = {{id:'keep',source_id:'source-b',name:'Keep',indexed_gid:8}};
materialEditor.document = {{
  material_slices:[target,keep],
  visual_layers:[{{id:'layer',display_level:'MAP',raw_gids:[7,8,0],cell_overrides:[{{index:0,slice_id:'target'}},{{index:1,slice_id:'keep'}}]}}],
  render_recipes:[{{id:'recipe',entries:[{{slice_id:'target'}},{{slice_id:'keep'}}]}}],
  hierarchy_nodes:[{{id:'root',kind:'WORLD',material_slice_id:'target'}}],
  tile_overrides:{{0:'target',1:'target',2:'keep'}},
  tile_override_parts:{{0:{{placement_id:'stamp',anchor_index:0}},1:{{placement_id:'stamp',anchor_index:0}}}},
  tile_override_layers:{{
    0:[{{slice_id:'keep',part:null}},{{slice_id:'target',part:{{placement_id:'stamp',anchor_index:0}}}}],
    1:[{{slice_id:'target',part:{{placement_id:'stamp',anchor_index:0}}}}],
    2:[{{slice_id:'keep',part:null}}],
  }},
}};
materialEditor.selectedPaintSliceId = 'target';
materialEditor.nodeMaterialPreview = {{nodeId:'root',sliceId:'target'}};
materialEditor.removeMaterialReferences(new Set(['target']));
assert.deepEqual(materialEditor.document.visual_layers[0].raw_gids, [0,8,0]);
assert.deepEqual(materialEditor.document.visual_layers[0].cell_overrides.map(item => item.slice_id), ['keep']);
assert.deepEqual(materialEditor.document.render_recipes[0].entries.map(item => item.slice_id), ['keep']);
assert.equal(materialEditor.document.hierarchy_nodes[0].material_slice_id, null);
assert.deepEqual(materialEditor.document.tile_override_layers[0].map(item => item.slice_id), ['keep']);
assert.equal(materialEditor.document.tile_override_layers[1], undefined);
assert.equal(materialEditor.document.tile_overrides[1], undefined);
assert.equal(materialEditor.document.tile_override_parts[1], undefined);
assert.equal(materialEditor.document.tile_overrides[2], 'keep');
assert.equal(materialEditor.selectedPaintSliceId, '');
assert.equal(materialEditor.nodeMaterialPreview, null);

const sourceEditor = Object.create(proto);
const sourceA = {{id:'source-a',name:'Source A'}}; const sourceB = {{id:'source-b',name:'Source B'}};
sourceEditor.document = {{material_sources:[sourceA,sourceB],material_slices:[target,keep],visual_layers:[],render_recipes:[],hierarchy_nodes:[],tile_overrides:{{}},tile_override_parts:{{}},tile_override_layers:{{}}}};
sourceEditor.sourceById = new Map([[sourceA.id,sourceA],[sourceB.id,sourceB]]);
sourceEditor.sliceById = new Map([[target.id,target],[keep.id,keep]]); sourceEditor.layerUsage = new Map();
sourceEditor.images = new Map([[sourceA.id,{{}}]]); sourceEditor.imageUrls = new Map([[sourceA.id,'url']]); sourceEditor.sliceTransparency = new Map();
sourceEditor.expandedSources = new Set([sourceA.id]); sourceEditor.readonly = false;
sourceEditor._changed = false; sourceEditor._changeRevision = 0; sourceEditor._changeNotificationQueued = false; sourceEditor.root = {{}};
sourceEditor.reindex = function () {{ this.sourceById = new Map(this.document.material_sources.map(source => [source.id,source])); this.sliceById = new Map(this.document.material_slices.map(slice => [slice.id,slice])); }};
sourceEditor.renderAll = () => {{}}; sourceEditor.fit = () => {{}}; sourceEditor.toast = () => {{}};
sourceEditor.deleteMaterialSource(sourceA);
assert.deepEqual(sourceEditor.document.material_sources.map(source => source.id), ['source-b']);
assert.deepEqual(sourceEditor.document.material_slices.map(slice => slice.id), ['keep']);
assert.equal(sourceEditor.selectedSourceId, 'source-b');
assert.equal(sourceEditor.images.has('source-a'), false);
assert.equal(sourceEditor.changed, true);
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_brushes_use_only_original_source_slices_and_group_them_by_source():
    """回归验证 ``test_brushes_use_only_original_source_slices_and_group_them_by_source`` 所描述的业务结果、故障边界和隔离约束。"""
    source = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")
    styles = (STATIC / "map-workspace.css").read_text(encoding="utf-8")

    assert 'data-slice-purpose' not in source
    assert "slice.purpose === 'MAP'" not in source
    assert "slice.purpose === 'WORLD'" not in source
    assert "delete slice.purpose" in source
    assert "paintableMaterialSlices()" in source
    assert ".filter(slice => this.sourceById?.get(slice.source_id)?.kind !== 'CANVAS')" in source
    assert "data-brush-source-group" in source
    assert "data-brush-source" in source
    assert "搜索原图或切片" in source
    assert "选择切片" in source
    assert "me2-brush-grid" not in source
    assert ".me2-brush-tree" in styles
    assert ".me2-brush-source-row" in styles
    assert ".me2-brush-slices" in styles

    editor_path = json.dumps((STATIC / "map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
editor.document = {{material_slices: [
  {{id:'raw-b',source_id:'source-b',name:'床'}},
  {{id:'canvas-output',source_id:'canvas-source',name:'房间画布'}},
  {{id:'raw-a',source_id:'source-a',name:'道路'}},
]}};
editor.sourceById = new Map([
  ['source-a',{{id:'source-a',kind:'UPLOADED',name:'道路原图'}}],
  ['source-b',{{id:'source-b',kind:'BUNDLED',name:'家具原图'}}],
  ['canvas-source',{{id:'canvas-source',kind:'CANVAS',name:'房间画布'}}],
]);
assert.deepEqual(editor.paintableMaterialSlices().map(slice => slice.id).sort(), ['raw-a','raw-b']);
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_world_nodes_select_world_slices_and_render_parent_before_child_layers():
    """回归验证 ``test_world_nodes_select_world_slices_and_render_parent_before_child_layers`` 所描述的业务结果、故障边界和隔离约束。"""
    source = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")

    assert "data-node-material" in source
    assert "worldMaterialSlices(node = null)" in source
    assert "node.material_slice_id" in source
    assert "drawWorldMaterials(ctx, tile)" in source
    assert "WORLD: 1, SECTOR: 2, ARENA: 3, GAME_OBJECT: 4" in source
    assert "世界素材按 World → Sector → Arena → Game Object" not in source

    editor_path = json.dumps((STATIC / "map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const slices = ['world', 'sector', 'arena', 'object'].map((name, index) => ({{
  id: `slice-${{name}}`, source_id: 'source', name,
  rotation_degrees: 0, pixel_rect: {{x: index * 32, y: 0, width: 32, height: 32}}
}}));
editor.document = {{
  material_slices: slices,
  hierarchy_nodes: [
    {{id:'node-world', kind:'WORLD', sort_order:0, bounds:{{x:0,y:0}}, material_slice_id:'slice-world'}},
    {{id:'node-sector', kind:'SECTOR', sort_order:0, bounds:{{x:1,y:1}}, material_slice_id:'slice-sector'}},
    {{id:'node-arena', kind:'ARENA', sort_order:0, bounds:{{x:2,y:2}}, material_slice_id:'slice-arena'}},
    {{id:'node-object', kind:'GAME_OBJECT', sort_order:0, bounds:{{x:3,y:3}}, material_slice_id:'slice-object'}},
  ],
}};
editor.sliceById = new Map(slices.map(slice => [slice.id, slice]));
editor.nodeMaterialPreview = null;
editor.depth = 4;
const calls = [];
editor.drawSliceRect = (_ctx, slice, _raw, x, y, width, height) => calls.push([slice.id, x, y, width, height]);

assert.deepEqual(editor.worldMaterialSlices().map(slice => slice.id), ['slice-arena', 'slice-object', 'slice-sector', 'slice-world']);
editor.drawWorldMaterials({{}}, 16);
assert.deepEqual(calls.map(call => call[0]), ['slice-world', 'slice-sector', 'slice-arena', 'slice-object']);
assert.deepEqual(calls.map(call => call.slice(1, 3)), [[0,0], [16,16], [32,32], [48,48]]);

calls.length = 0;
editor.depth = 2;
editor.drawWorldMaterials({{}}, 16);
assert.deepEqual(calls.map(call => call[0]), ['slice-world', 'slice-sector']);
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_material_canvas_uses_32px_grid_pan_and_resize_interactions():
    """回归验证 ``test_material_canvas_uses_32px_grid_pan_and_resize_interactions`` 所描述的业务结果、故障边界和隔离约束。"""
    source = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")
    styles = (STATIC / "map-workspace.css").read_text(encoding="utf-8")

    assert "const MATERIAL_GRID_SIZE = 32" in source
    assert "data-material-pan" in source
    assert "32 × 32 px / 格" in source
    assert "type: 'crop-select'" in source
    assert "materialGridRect(source, start, end)" in source
    assert "snapMaterialSpan(value, available)" in source
    assert "materialResizeHit(point)" in source
    assert ".me2-canvas-host.is-resize { cursor: nwse-resize; }" in styles


def test_material_slice_rotation_is_quarter_turn_only_and_rendered_by_the_editor():
    """回归验证 ``test_material_slice_rotation_is_quarter_turn_only_and_rendered_by_the_editor`` 所描述的业务结果、故障边界和隔离约束。"""
    base = {
        "id": "slice-test",
        "source_id": "source-test",
        "name": "测试切片",
        "kind": "STAMP",
        "pixel_rect": GridRect(x=0, y=0, width=32, height=32),
    }

    assert [MaterialSlice(**base, rotation_degrees=value).rotation_degrees for value in (0, 90, 180, 270)] == [0, 90, 180, 270]
    with pytest.raises(ValidationError):
        MaterialSlice(**base, rotation_degrees=45)

    source = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")
    assert "data-slice-rotation" in source
    assert "[data-slice-rotation]')?.addEventListener('change'" in source
    assert "this.sliceRotationPreview =" in source
    assert "preview.innerHTML = this.sliceThumb(slice, true)" in source
    assert "ctx.rotate(rotation * Math.PI / 180)" in source
    assert "transform:rotate(${this.sliceRotation(slice)}deg)" in source


def test_slice_rotation_preview_swaps_display_size_without_committing_the_slice():
    """回归验证 ``test_slice_rotation_preview_swaps_display_size_without_committing_the_slice`` 所描述的业务结果、故障边界和隔离约束。"""
    editor_path = json.dumps((STATIC / "map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const slice = {{
  id: 'slice-light', source_id: 'source-light', rotation_degrees: 0,
  pixel_rect: {{x: 0, y: 0, width: 32, height: 64}}
}};
editor.sliceRotationPreview = null;
assert.equal(editor.sliceRotation(slice), 0);
assert.deepEqual(editor.sliceDisplaySize(slice), {{width: 32, height: 64}});

editor.sliceRotationPreview = {{sliceId: slice.id, rotation: 90}};
assert.equal(editor.sliceRotation(slice), 90);
assert.deepEqual(editor.sliceDisplaySize(slice), {{width: 64, height: 32}});
assert.equal(slice.rotation_degrees, 0, 'preview must not commit before Save');

editor.clearSliceRotationPreview(slice.id);
assert.equal(editor.sliceRotation(slice), 0);
assert.deepEqual(editor.sliceDisplaySize(slice), {{width: 32, height: 64}});
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_large_material_slices_are_stamped_across_their_32px_map_footprint():
    """回归验证 ``test_large_material_slices_are_stamped_across_their_32px_map_footprint`` 所描述的业务结果、故障边界和隔离约束。"""
    source = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")

    assert "sliceFootprint(slice)" in source
    assert "Math.ceil(display.width / MATERIAL_GRID_SIZE)" in source
    assert "Math.ceil(display.height / MATERIAL_GRID_SIZE)" in source
    assert "tile_override_parts" in source
    assert "stampSliceAt(x, y, slice, mapWidth, mapHeight" in source
    assert "drawTilePart(ctx, slice, part, dx, dy, size)" in source
    assert "removeMapPlacement(placementId)" in source
    assert "rotation_degrees: this.sliceRotation(slice)" in source

    part = TileOverridePart(
        placement_id="placement-test",
        anchor_index=0,
        column=1,
        row=1,
        columns=2,
        rows=2,
        rotation_degrees=90,
    )
    assert (part.columns, part.rows, part.rotation_degrees) == (2, 2, 90)
    with pytest.raises(ValidationError):
        TileOverridePart(
            placement_id="placement-invalid",
            anchor_index=0,
            column=2,
            row=0,
            columns=2,
            rows=1,
        )


def test_map_editor_contract_persists_bottom_to_top_override_layers():
    """回归验证 ``test_map_editor_contract_persists_bottom_to_top_override_layers`` 所描述的业务结果、故障边界和隔离约束。"""
    document = fresh_ville_editor_document().model_dump(mode="json")
    bottom, top = document["material_slices"][:2]
    part = {
        "placement_id": "placement-transparent",
        "anchor_index": 0,
        "column": 0,
        "row": 0,
        "columns": 1,
        "rows": 1,
        "rotation_degrees": 0,
    }
    document["tile_overrides"] = {0: top["id"]}
    document["tile_override_parts"] = {0: part}
    document["tile_override_layers"] = {
        0: [
            {"slice_id": bottom["id"], "part": None},
            {"slice_id": top["id"], "part": part},
        ]
    }

    parsed = MapEditorDocumentV2.model_validate(document)

    assert [layer.slice_id for layer in parsed.tile_override_layers[0]] == [
        bottom["id"],
        top["id"],
    ]
    document["tile_override_layers"][0][-1]["slice_id"] = "missing-slice"
    with pytest.raises(ValidationError, match="missing material slice"):
        MapEditorDocumentV2.model_validate(document)


def test_material_canvas_is_persisted_as_a_reusable_acyclic_material():
    """回归验证 ``test_material_canvas_is_persisted_as_a_reusable_acyclic_material`` 所描述的业务结果、故障边界和隔离约束。"""
    document = fresh_ville_editor_document().model_dump(mode="json")
    brush = document["material_slices"][0]
    source_id = "source-user-canvas"
    slice_id = "slice-user-canvas"
    document["material_sources"].append(
        {
            "id": source_id,
            "name": "道路画布",
            "kind": "CANVAS",
            "asset_id": None,
            "asset_hash": None,
            "bundled_path": None,
            "generated_color": None,
            "media_type": "image/png",
            "width_px": 64,
            "height_px": 64,
            "tile_width": 32,
            "tile_height": 32,
            "columns": 2,
            "rows": 2,
            "tile_count": 4,
            "margin": 0,
            "spacing": 0,
            "first_gid": None,
        }
    )
    document["material_slices"].append(
        {
            "id": slice_id,
            "source_id": source_id,
            "name": "道路画布",
            "kind": "STAMP",
            "rotation_degrees": 0,
            "grid_rect": None,
            "pixel_rect": {"x": 0, "y": 0, "width": 64, "height": 64},
            "trim_transparent": True,
            "indexed_gid": None,
            "local_tile_id": None,
            "readonly_indexed": False,
        }
    )
    document["material_canvases"] = [
        {
            "id": "canvas-road",
            "source_id": source_id,
            "slice_id": slice_id,
            "name": "道路画布",
            "width_tiles": 2,
            "height_tiles": 2,
            "tile_size": 32,
            "cells": {0: [{"slice_id": brush["id"], "part": None}]},
        }
    ]

    parsed = MapEditorDocumentV2.model_validate(document)

    assert parsed.material_canvases[0].cells[0][0].slice_id == brush["id"]
    document["material_canvases"][0]["cells"][0][0]["slice_id"] = slice_id
    with pytest.raises(ValidationError, match="only paint with non-canvas slices"):
        MapEditorDocumentV2.model_validate(document)


def test_map_editor_contract_persists_node_material_assignments():
    """回归验证 ``test_map_editor_contract_persists_node_material_assignments`` 所描述的业务结果、故障边界和隔离约束。"""
    document = fresh_ville_editor_document().model_dump(mode="json")
    world_slice = document["material_slices"][0]
    root = next(item for item in document["hierarchy_nodes"] if item["kind"] == "WORLD")
    root["material_slice_id"] = world_slice["id"]

    parsed = MapEditorDocumentV2.model_validate(document)

    assert parsed.hierarchy_nodes[0].material_slice_id == world_slice["id"]
    root["material_slice_id"] = "missing-slice"
    with pytest.raises(ValidationError, match="references missing material slice"):
        MapEditorDocumentV2.model_validate(document)


def test_large_slice_paints_erases_and_restores_as_one_map_stamp():
    """回归验证 ``test_large_slice_paints_erases_and_restores_as_one_map_stamp`` 所描述的业务结果、故障边界和隔离约束。"""
    editor_path = json.dumps((STATIC / "map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const largeSlice = {{
  id: 'slice-large', source_id: 'source', rotation_degrees: 0,
  pixel_rect: {{x: 0, y: 0, width: 64, height: 64}}
}};
const canvas = {{id:'canvas',source_id:'canvas-source',slice_id:'canvas-slice',width_tiles:6,height_tiles:6,tile_size:32,cells:{{}}}};
editor.document = {{
  material_canvases: [canvas]
}};
editor.sliceById = new Map([[largeSlice.id, largeSlice]]);
editor.canvasById = new Map([[canvas.id, canvas]]);
editor.selectedPaintSliceId = largeSlice.id;
editor.selectedCanvasId = canvas.id; editor.materialView = 'canvas';
editor.workspace = 'materials'; editor.readonly = false; editor.changed = false;
editor.undoStack = []; editor.redoStack = []; editor.root = {{querySelector: () => null}};
editor.renderCanvas = () => {{}}; editor.toast = () => {{}}; editor.refreshCanvasImages = () => {{}};

editor.beginMapEdit('paint'); editor.paintAt(1, 1); editor.commitMapEdit();
assert.deepEqual(Object.keys(canvas.cells).map(Number).sort((a,b) => a-b), [7, 8, 13, 14]);
assert.equal(new Set(Object.values(canvas.cells).flat().map(layer => layer.part.placement_id)).size, 1);
assert.deepEqual(editor.sliceFootprint(largeSlice), {{columns: 2, rows: 2}});

editor.beginMapEdit('erase'); editor.eraseAt(2, 2); editor.commitMapEdit();
assert.equal(Object.keys(canvas.cells).length, 0);
editor.undoMapEdit();
assert.equal(Object.keys(canvas.cells).length, 4);
editor.redoMapEdit();
assert.equal(Object.keys(canvas.cells).length, 0);

largeSlice.pixel_rect = {{x: 0, y: 0, width: 64, height: 32}};
largeSlice.rotation_degrees = 90;
assert.deepEqual(editor.sliceFootprint(largeSlice), {{columns: 1, rows: 2}});
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_transparent_map_slice_is_composited_without_replacing_the_painted_base():
    """回归验证 ``test_transparent_map_slice_is_composited_without_replacing_the_painted_base`` 所描述的业务结果、故障边界和隔离约束。"""
    editor_path = json.dumps((STATIC / "map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const road = {{
  id: 'slice-road', source_id: 'source-road', rotation_degrees: 0,
  pixel_rect: {{x: 0, y: 0, width: 32, height: 32}}
}};
const light = {{
  id: 'slice-light', source_id: 'source-light', rotation_degrees: 0,
  pixel_rect: {{x: 0, y: 0, width: 32, height: 64}}
}};
const canvas = {{id:'canvas',source_id:'canvas-source',slice_id:'canvas-slice',width_tiles:4,height_tiles:4,tile_size:32,cells:{{}}}};
editor.document = {{
  material_canvases: [canvas]
}};
editor.sliceById = new Map([[road.id, road], [light.id, light]]);
editor.canvasById = new Map([[canvas.id, canvas]]);
editor.sliceHasTransparency = slice => slice.id === light.id;
editor.selectedCanvasId = canvas.id; editor.materialView = 'canvas';
editor.workspace = 'materials'; editor.readonly = false; editor.changed = false;
editor.undoStack = []; editor.redoStack = []; editor.root = {{querySelector: () => null}};
editor.renderCanvas = () => {{}}; editor.toast = () => {{}}; editor.refreshCanvasImages = () => {{}};

editor.selectedPaintSliceId = road.id;
editor.beginMapEdit('road'); editor.paintAt(1, 1); editor.commitMapEdit();
assert.deepEqual(editor.readPaintCellLayers(5).map(layer => layer.slice_id), ['slice-road']);

editor.selectedPaintSliceId = light.id;
editor.beginMapEdit('light'); editor.paintAt(1, 1); editor.commitMapEdit();
assert.deepEqual(editor.readPaintCellLayers(5).map(layer => layer.slice_id), ['slice-road', 'slice-light']);
assert.deepEqual(editor.readPaintCellLayers(9).map(layer => layer.slice_id), ['slice-light']);

editor.beginMapEdit('erase light'); editor.eraseAt(1, 1); editor.commitMapEdit();
assert.deepEqual(editor.readPaintCellLayers(5).map(layer => layer.slice_id), ['slice-road']);
assert.deepEqual(editor.readPaintCellLayers(9), []);
editor.undoMapEdit();
assert.deepEqual(editor.readPaintCellLayers(5).map(layer => layer.slice_id), ['slice-road', 'slice-light']);
assert.deepEqual(editor.readPaintCellLayers(9).map(layer => layer.slice_id), ['slice-light']);
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_map_editor_change_revisions_protect_edits_made_during_a_save():
    """回归验证 ``test_map_editor_change_revisions_protect_edits_made_during_a_save`` 所描述的业务结果、故障边界和隔离约束。"""
    editor_path = json.dumps((STATIC / "map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
global.CustomEvent = class CustomEvent {{
  constructor(type, options = {{}}) {{ this.type = type; this.detail = options.detail; }}
}};
require({editor_path});

(async () => {{
  const events = [];
  const editor = Object.create(window.MapEditorV2.prototype);
  editor.root = {{dispatchEvent: event => events.push(event)}};
  editor._changed = false;
  editor._changeRevision = 0;
  editor._changeNotificationQueued = false;
  editor.world = {{definition: {{value: 'server'}}}};

  editor.changed = true;
  const savingRevision = editor.changeRevision;
  editor.changed = true;
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'map-editor-v2:change');
  assert.equal(events[0].detail.revision, 2);
  editor.acceptSavedWorld({{definition: {{value: 'first-save'}}}}, savingRevision);
  assert.equal(editor.changed, true, 'a stale save response must not mark newer edits clean');
  editor.acceptSavedWorld({{definition: {{value: 'second-save'}}}}, editor.changeRevision);
  assert.equal(editor.changed, false);
  assert.equal(editor.world.definition.value, 'second-save');
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_custom_blank_map_does_not_inherit_ville_materials_or_nodes():
    """回归验证 ``test_custom_blank_map_does_not_inherit_ville_materials_or_nodes`` 所描述的业务结果、故障边界和隔离约束。"""
    source = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")
    styles = (STATIC / "map-workspace.css").read_text(encoding="utf-8")

    assert "if (!this.world || this.world.world_key === 'the-ville')" in source
    assert "material_sources: []," in source
    assert "material_slices: []," in source
    assert "material_canvases: []," in source
    assert "used_gid_count: 0" in source
    assert "fetch('/api/v1/assets', { method: 'POST', body })" in source
    assert "/api/v1/assets/${encodeURIComponent(source.asset_id)}/content" in source
    assert "tile.address = node ? this.nodeAddress(node) : [];" in source
    assert "Object.keys(this.document.tile_overrides || {})" in source
    assert "tile_override_layers" in source
    assert "sliceHasTransparency(slice)" in source
    assert "grid-template-columns: minmax(0, 1fr)" in styles
    assert "width: 100% !important" in styles
