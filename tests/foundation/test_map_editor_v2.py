"""基础能力回归测试：覆盖 ``test_map_editor_v2`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from generative_agents.ga_protocol.schemas.world import GridRect
from generative_agents.ga_studio.resources.map_document import MapEditorDocumentV2
from generative_agents.ga_protocol.schemas.world import MaterialSlice
from generative_agents.ga_protocol.schemas.world import PixelRect
from generative_agents.ga_protocol.schemas.world import TileOverridePart
from generative_agents.ga_protocol.schemas.experiment import WorldConfig
from generative_agents.ga_studio.resources.map_importer import fresh_ville_editor_document
from generative_agents.ga_studio.resources.maps import _compile_editor_v2_runtime_addresses
from generative_agents.ga_studio.resources.maps import _validate_map_editor_v2
from tests.studio_support import create_test_studio


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'src' / 'generative_agents' / 'adapters' / 'web' / 'static'


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


def test_game_object_binding_derives_mode_without_a_separate_switch():
    from generative_agents.ga_protocol.schemas.world import HierarchyNode
    node = HierarchyNode.model_validate({
        "id": "object", "kind": "GAME_OBJECT", "parent_id": "arena", "name": "Door",
        "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
        "skill_bindings": [{"skill_name": "door-skill"}],
    })
    assert node.interaction_mode == "SKILL_BOUND"
    assert node.skill_bindings[0].interaction_key == "interact"
    node.skill_bindings = []
    assert node.interaction_mode == "STATIC"


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
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        document = client.get("/api/studio/resources/map-editor/ville-document")
        source = client.get(
            "/assets/library/village/"
            "tilemap/CuteRPG_Field_B.png"
        )
        editor = client.get("/static/console/resources/map-editor-v2.js")

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
    source = (STATIC / "resources/map-editor-v2.js").read_text(encoding="utf-8")

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
    assert ".filter(item => (item.scripts || []).includes('scripts/main.py'))" not in source
    assert "data-node-initial-state" in source


def test_formal_map_editor_fills_the_remaining_viewport_height():
    """回归验证 ``test_formal_map_editor_fills_the_remaining_viewport_height`` 所描述的业务结果、故障边界和隔离约束。"""
    styles = (STATIC / "resources/map-workspace.css").read_text(encoding="utf-8")

    assert "body.map-editor-mode .content" in styles
    assert "height: calc(100dvh - var(--topbar-height));" in styles
    assert "body.map-editor-mode .map-editor-v2" in styles
    assert "grid-template-rows: 54px auto minmax(0, 1fr);" in styles
    assert "body.map-editor-mode .map-editor-v2 > .me2-build-guide" in styles
    assert "body.map-editor-mode .me2-layout" in styles
    assert "height: min(760px, calc(100vh - 235px));" not in styles


def test_material_canvas_and_world_expose_only_relevant_tools():
    """回归验证 ``test_material_canvas_and_world_expose_only_relevant_tools`` 所描述的业务结果、故障边界和隔离约束。"""
    source = (STATIC / "resources/map-editor-v2.js").read_text(encoding="utf-8")

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
    source = (STATIC / "resources/map-editor-v2.js").read_text(encoding="utf-8")
    styles = (STATIC / "resources/map-workspace.css").read_text(encoding="utf-8")

    assert "type: 'move-node'" in source
    assert "worldInteraction: true" in source
    assert "dragWorldNode(point)" in source
    assert "selected?.kind !== 'WORLD'" in source
    assert ".me2-canvas-host.is-node-move { cursor: move; }" in styles

    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
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


def test_world_canvas_resizes_selected_node_from_bottom_right_handle():
    """非根地址节点应能用右下角手柄调整空间范围并同步表单。"""
    source = (STATIC / "resources/map-editor-v2.js").read_text(encoding="utf-8")
    styles = (STATIC / "resources/map-workspace.css").read_text(encoding="utf-8")

    assert "type: 'resize-node'" in source
    assert "dragWorldNodeResize(point)" in source
    assert "worldNodeResizeHit(point" in source
    assert "selected.kind !== 'WORLD'" in source
    assert ".me2-canvas-host.is-resize { cursor: nwse-resize; }" in styles

    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
global.CustomEvent = class CustomEvent {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const node = {{id:'sector',kind:'SECTOR',bounds:{{x:2,y:3,width:4,height:4}},extensions:{{mask:'old'}}}};
editor.document = {{import_metadata:{{width:10,height:10}},hierarchy_nodes:[node]}};
editor.nodeById = new Map([[node.id,node]]);
editor.selectedNodeId = node.id;
editor.workspace = 'world'; editor.readonly = false; editor.tool = 'world';
editor.zoom = 1; editor.renderTile = 16; editor.offsetX = 0; editor.offsetY = 0;
editor.nodeEditDraft = null;
editor._changed = false; editor._changeRevision = 0; editor._changeNotificationQueued = false;
const inputs = {{width:{{value:'4'}},height:{{value:'4'}}}};
editor.root = {{focus:()=>{{}},dispatchEvent:()=>{{}}}};
editor.inspector = {{querySelector: selector => selector.includes('node-w') ? inputs.width : selector.includes('node-h') ? inputs.height : null}};
editor.canvas = {{setPointerCapture:()=>{{}},hasPointerCapture:()=>false,releasePointerCapture:()=>{{}}}};
editor.canvasHost = null;
editor.renderCanvas = () => {{}};
editor.localPoint = event => ({{x:event.x,y:event.y}});

editor.pointerDown({{button:0,pointerId:1,x:96,y:112}});
assert.equal(editor.drag.type, 'resize-node');
editor.pointerMove({{x:144,y:144}});
assert.deepEqual(node.bounds, {{x:2,y:3,width:7,height:6}});
assert.equal(inputs.width.value, '7');
assert.equal(inputs.height.value, '6');
assert.equal(node.extensions.mask, undefined);
assert.equal(editor.changed, true);

editor.pointerMove({{x:500,y:500}});
assert.deepEqual(node.bounds, {{x:2,y:3,width:8,height:7}}, 'resize must stop at the map edge');
editor.pointerUp({{pointerId:1,x:500,y:500}});
assert.equal(editor.drag, null);
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_world_node_draft_survives_same_node_canvas_reselection_before_save():
    """编辑 World 后点击其画布造成重绘时，第一次保存必须使用用户刚输入的值。"""
    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const worldNode = {{
  id:'world',kind:'WORLD',parent_id:null,name:'未命名世界',sort_order:0,
  bounds:{{x:0,y:0,width:20,height:12}},semantic:'旧语义',material_slice_id:null,
  skill_bindings:[],initial_state:{{}},extensions:{{}},
}};
editor.document = {{
  root_node_id:worldNode.id,
  import_metadata:{{width:20,height:12}},
  hierarchy_nodes:[worldNode],
  material_slices:[],
}};
editor.nodeById = new Map([[worldNode.id, worldNode]]);
editor.childrenByParent = new Map();
editor.sliceById = new Map();
editor.passiveSkillCatalog = [];
editor.nodeEditDraft = null;
editor.nodeMaterialPreview = null;
editor.selectedNodeId = worldNode.id;
editor.expandedNodes = new Set([worldNode.id]);
editor.readonly = false;
editor._changed = false;
editor._changeRevision = 0;
editor._changeNotificationQueued = false;
editor.root = {{}};
const handlers = {{}};
const control = (key, value = '', tag = 'input') => ({{
  value,
  matches: selector => selector === tag,
  addEventListener: (type, callback) => {{ handlers[`${{key}}:${{type}}`] = callback; }},
}});
const controls = {{
  '[data-node-name]': control('name', '更新后的世界'),
  '[data-node-material]': control('material', '', 'select'),
  '[data-node-x]': control('x', '0'),
  '[data-node-y]': control('y', '0'),
  '[data-node-w]': control('w', '20'),
  '[data-node-h]': control('h', '12'),
  '[data-node-semantic]': control('semantic', '第一次输入的世界语义', 'textarea'),
  '[data-save-node]': control('save', '', 'button'),
}};
editor.inspector = {{querySelector: selector => controls[selector] || null}};
editor.reindex = () => {{}};
editor.renderCanvas = () => {{}};
editor.toast = () => {{}};
let rendered = '';
editor.renderAll = () => {{ rendered = editor.nodeInspector(worldNode); }};

editor.bindNodeInspector(worldNode);
handlers['name:input']({{target:controls['[data-node-name]']}});
handlers['semantic:input']({{target:controls['[data-node-semantic]']}});
editor.selectNode(worldNode.id, false);
assert.equal(worldNode.name, '未命名世界', 'same-node re-render must keep the edit as a draft');
assert.match(rendered, /data-node-name value="更新后的世界"/);
assert.match(rendered, />第一次输入的世界语义<\\/textarea>/);

editor.bindNodeInspector(worldNode);
handlers['save:click']();
assert.equal(worldNode.name, '更新后的世界');
assert.equal(worldNode.semantic, '第一次输入的世界语义');
assert.equal(editor.nodeEditDraft, null);
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_node_material_selection_preserves_bounds_and_default_name():
    """地址节点选择素材后只替换默认名称，不能改写空间语义范围。"""
    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
global.CustomEvent = class CustomEvent {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const node = {{
  id:'arena',kind:'ARENA',parent_id:'sector',name:'\\u672a\\u547d\\u540d Arena',sort_order:0,
  bounds:{{x:9,y:9,width:1,height:1}},semantic:'',material_slice_id:null,
  skill_bindings:[],initial_state:{{}},extensions:{{}},
}};
const slice = {{
  id:'slice',source_id:'source',name:'\\u56fe\\u4e66\\u9605\\u89c8\\u533a',pixel_rect:{{x:0,y:0,width:96,height:64}},
  grid_rect:{{x:0,y:0,width:3,height:2}},rotation_degrees:0,
}};
editor.document = {{import_metadata:{{width:10,height:10}},hierarchy_nodes:[node],material_slices:[slice]}};
editor.nodeById = new Map([[node.id,node]]);
editor.sliceById = new Map([[slice.id,slice]]);
editor.sourceById = new Map([['source',{{id:'source',kind:'UPLOADED',width_px:96,height_px:64,tile_width:32,tile_height:32}}]]);
editor.nodeEditDraft = null;
editor.nodeMaterialPreview = null;
editor.readonly = false;
editor._changed = false;
editor._changeRevision = 0;
editor._changeNotificationQueued = false;
const handlers = {{}};
const control = (key, value = '', tag = 'input') => ({{
  value,
  innerHTML:'',
  matches: selector => selector === tag,
  addEventListener: (type, callback) => {{ handlers[`${{key}}:${{type}}`] = callback; }},
}});
const controls = {{
  '[data-node-name]': control('name', node.name),
  '[data-node-material]': control('material', slice.id, 'select'),
  '[data-node-material-preview]': control('preview'),
  '[data-node-x]': control('x', '9'),
  '[data-node-y]': control('y', '9'),
  '[data-node-w]': control('w', '1'),
  '[data-node-h]': control('h', '1'),
  '[data-node-semantic]': control('semantic', '', 'textarea'),
  '[data-save-node]': control('save', '', 'button'),
}};
editor.inspector = {{querySelector: selector => controls[selector] || null}};
editor.nodeMaterialPreviewHtml = () => '<span>preview</span>';
let focused = false;
editor.focusNode = () => {{ focused = true; }};
editor.renderCanvas = () => {{}};
editor.reindex = () => {{}};
editor.renderAll = () => {{}};
editor.toast = () => {{}};

editor.bindNodeInspector(node);
handlers['material:change']({{target:controls['[data-node-material]']}});
assert.equal(controls['[data-node-name]'].value, '\\u56fe\\u4e66\\u9605\\u89c8\\u533a');
assert.equal(controls['[data-node-w]'].value, '1');
assert.equal(controls['[data-node-h]'].value, '1');
assert.equal(controls['[data-node-x]'].value, '9');
assert.equal(controls['[data-node-y]'].value, '9');
assert.deepEqual(editor.nodeDisplayBounds(node), {{x:9,y:9,width:1,height:1}});
assert.equal(editor.pointInNode({{x:9.5,y:9.5}}, node), true, 'the original semantic rectangle remains draggable');
assert.equal(focused, true);

handlers['save:click']();
assert.equal(node.name, '\\u56fe\\u4e66\\u9605\\u89c8\\u533a');
assert.equal(node.material_slice_id, slice.id);
assert.deepEqual(node.bounds, {{x:9,y:9,width:1,height:1}});

const manuallyNamed = {{...node,id:'manual',name:'\\u81ea\\u5b9a\\u4e49\\u9605\\u89c8\\u533a\\u57df',bounds:{{x:1,y:1,width:1,height:1}},material_slice_id:null}};
editor.nodeEditDraft = null;
editor.applyNodeMaterialDraft(manuallyNamed, slice);
assert.equal(editor.nodeDraftValue(manuallyNamed, 'name', manuallyNamed.name), '\\u81ea\\u5b9a\\u4e49\\u9605\\u89c8\\u533a\\u57df');
assert.deepEqual(manuallyNamed.bounds, {{x:1,y:1,width:1,height:1}}, 'manual spatial bounds are independent from material footprint');
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
    source = (STATIC / "resources/map-editor-v2.js").read_text(encoding="utf-8")
    styles = (STATIC / "resources/map-workspace.css").read_text(encoding="utf-8")

    assert "data-delete-node" in source
    assert "data-delete-slice" in source
    assert "data-delete-source" in source
    assert "node.kind === 'WORLD'" in source
    assert "removeMaterialReferences(sliceIds)" in source
    assert ".me2-danger" in styles


def test_deleting_world_nodes_cascades_and_material_deletion_cleans_references():
    """回归验证 ``test_deleting_world_nodes_cascades_and_material_deletion_cleans_references`` 所描述的业务结果、故障边界和隔离约束。"""
    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{confirmResourceDeletion: async () => true}};
(async () => {{
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
let confirmOptions;
window.confirmResourceDeletion = async options => {{ confirmOptions = options; return false; }};
await nodeEditor.deleteWorldNode(object);
assert.equal(confirmOptions.name, 'Object');
assert.equal(nodeEditor.document.hierarchy_nodes.length, 5);
assert.equal(nodeEditor.changed, false);
window.confirmResourceDeletion = async options => {{ confirmOptions = options; return true; }};
await nodeEditor.deleteWorldNode(sector);
assert.equal(confirmOptions.name, 'Sector');
assert.match(confirmOptions.message, /2/);
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
await sourceEditor.deleteMaterialSource(sourceA);
assert.deepEqual(sourceEditor.document.material_sources.map(source => source.id), ['source-b']);
assert.deepEqual(sourceEditor.document.material_slices.map(slice => slice.id), ['keep']);
assert.equal(sourceEditor.selectedSourceId, 'source-b');
assert.equal(sourceEditor.images.has('source-a'), false);
assert.equal(sourceEditor.changed, true);
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
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
    source = (STATIC / "resources/map-editor-v2.js").read_text(encoding="utf-8")
    styles = (STATIC / "resources/map-workspace.css").read_text(encoding="utf-8")

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

    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
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
    source = (STATIC / "resources/map-editor-v2.js").read_text(encoding="utf-8")

    assert "data-node-material" in source
    assert "worldMaterialSlices(node = null)" in source
    assert "node.material_slice_id" in source
    assert "drawWorldMaterials(ctx, tile)" in source
    assert "WORLD: 1, SECTOR: 2, ARENA: 3, GAME_OBJECT: 4" in source
    assert "世界素材按 World → Sector → Arena → Game Object" not in source

    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
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


def test_material_slice_range_uses_source_tile_units_and_internal_pixel_crop():
    """切片表单和地图占地使用格数；像素矩形只由素材网格确定性派生。"""
    source = (STATIC / "resources/map-editor-v2.js").read_text(encoding="utf-8")
    styles = (STATIC / "resources/map-workspace.css").read_text(encoding="utf-8")

    assert "data-material-pan" in source
    assert "<span>Tile 格</span>" in source
    assert "slice.pixel_rect = this.pixelRectFromGridRect(source, slice.grid_rect)" in source
    assert "type: 'crop-select'" in source
    assert "materialGridRect(source, start, end)" in source
    assert "materialResizeHit(point)" in source
    assert ".me2-canvas-host.is-resize { cursor: nwse-resize; }" in styles

    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
editor.world = {{definition:{{tile_size:24}}}};
editor.document = {{import_metadata:{{tile_size:24}}}};
editor.readonly = false;
editor.materialView = 'slice';
editor.editingSlice = false;
editor.sliceNameDraft = null;
editor.sliceRotationPreview = null;
editor.imageUrls = new Map();
editor.sliceApplications = () => [];

const uploaded = {{
  id:'source-uploaded',name:'非整格原图',kind:'UPLOADED',width_px:50,height_px:35,
  tile_width:24,tile_height:16,columns:3,rows:3,margin:0,spacing:0,
}};
const slice = {{
  id:'slice-range',source_id:uploaded.id,name:'两格横条',rotation_degrees:0,
  grid_rect:{{x:1,y:1,width:2,height:1}},
  pixel_rect:{{x:24,y:16,width:26,height:16}},
}};
editor.sourceById = new Map([[uploaded.id, uploaded]]);
assert.deepEqual(editor.pixelRectFromGridRect(uploaded, slice.grid_rect), {{x:24,y:16,width:26,height:16}});
assert.deepEqual(editor.sliceFootprint(slice), {{columns:2,rows:1}});
const inspector = editor.materialInspector(uploaded, slice);
assert.match(inspector, /<strong>切片范围<\\/strong><span>Tile 格<\\/span>/);
assert.match(inspector, /data-slice-x value="1"/);
assert.match(inspector, /data-slice-y value="1"/);
assert.match(inspector, /data-slice-w value="2"/);
assert.match(inspector, /data-slice-h value="1"/);

slice.rotation_degrees = 90;
assert.deepEqual(editor.sliceFootprint(slice), {{columns:1,rows:2}});

const spaced = {{
  id:'source-spaced',kind:'BUNDLED',width_px:55,height_px:28,
  tile_width:16,tile_height:12,columns:3,rows:2,margin:2,spacing:1,
}};
assert.deepEqual(
  editor.pixelRectFromGridRect(spaced, {{x:1,y:0,width:2,height:2}}),
  {{x:19,y:2,width:33,height:25}},
);
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_material_slice_rotation_is_quarter_turn_only_and_rendered_by_the_editor():
    """回归验证 ``test_material_slice_rotation_is_quarter_turn_only_and_rendered_by_the_editor`` 所描述的业务结果、故障边界和隔离约束。"""
    base = {
        "id": "slice-test",
        "source_id": "source-test",
        "name": "测试切片",
        "kind": "STAMP",
        "grid_rect": GridRect(x=0, y=0, width=1, height=1),
        "pixel_rect": PixelRect(x=0, y=0, width=32, height=32),
    }

    assert [MaterialSlice(**base, rotation_degrees=value).rotation_degrees for value in (0, 90, 180, 270)] == [0, 90, 180, 270]
    with pytest.raises(ValidationError):
        MaterialSlice(**base, rotation_degrees=45)

    source = (STATIC / "resources/map-editor-v2.js").read_text(encoding="utf-8")
    assert "data-slice-rotation" in source
    assert "[data-slice-rotation]')?.addEventListener('change'" in source
    assert "this.sliceRotationPreview =" in source
    assert "preview.innerHTML = this.sliceThumb(slice, true)" in source
    assert "ctx.rotate(rotation * Math.PI / 180)" in source
    assert "transform:rotate(${this.sliceRotation(slice)}deg)" in source


def test_slice_rotation_preview_swaps_display_size_without_committing_the_slice():
    """回归验证 ``test_slice_rotation_preview_swaps_display_size_without_committing_the_slice`` 所描述的业务结果、故障边界和隔离约束。"""
    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
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


def test_slice_name_draft_survives_finishing_crop_before_save():
    """完成框选触发检查器重绘时，尚未保存的切片名称仍应留在输入框。"""
    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
global.requestAnimationFrame = callback => callback();
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const source = {{id:'source-table',name:'家具原图',kind:'UPLOADED',width_px:256,height_px:128,tile_width:32,tile_height:32,columns:8,rows:4,margin:0,spacing:0}};
const slice = {{
  id:'slice-table',source_id:source.id,name:'未命名切片',rotation_degrees:0,
  grid_rect:{{x:1,y:1,width:2,height:1}},
  pixel_rect:{{x:32,y:32,width:64,height:32}},
}};
const handlers = {{}};
const control = (key, value = '') => ({{
  value,
  addEventListener: (type, callback) => {{ handlers[`${{key}}:${{type}}`] = callback; }},
}});
const controls = {{
  '[data-slice-name]': control('name', '长桌切片'),
  '[data-edit-crop]': control('crop'),
  '[data-save-slice]': control('save'),
  '[data-slice-rotation]': control('rotation', '0'),
  '[data-slice-x]': control('x', '1'),
  '[data-slice-y]': control('y', '1'),
  '[data-slice-w]': control('w', '2'),
  '[data-slice-h]': control('h', '1'),
}};
editor.inspector = {{querySelector: selector => controls[selector] || null}};
editor.sourceById = new Map([[source.id, source]]);
editor.imageUrls = new Map();
editor.sliceNameDraft = null;
editor.sliceRotationPreview = null;
editor.materialView = 'slice';
editor.editingSlice = true;
editor.readonly = false;
editor._changed = false;
editor._changeRevision = 0;
editor._changeNotificationQueued = false;
editor.root = {{}};
editor.sliceApplications = () => [];
editor.reindex = () => {{}};
editor.fit = () => {{}};
editor.toast = () => {{}};
let rendered = '';
editor.renderAll = () => {{ rendered = editor.materialInspector(source, slice); }};

editor.bindMaterialInspector(source, slice);
handlers['crop:click']();
assert.equal(editor.editingSlice, false);
assert.equal(slice.name, '未命名切片', 'finishing the crop must not prematurely commit the form');
assert.match(rendered, /data-slice-name value="长桌切片"/);

editor.bindMaterialInspector(source, slice);
handlers['save:click']();
assert.equal(slice.name, '长桌切片');
assert.equal(editor.sliceNameDraft, null);
assert.deepEqual(slice.grid_rect, {{x:1,y:1,width:2,height:1}});
assert.deepEqual(slice.pixel_rect, {{x:32,y:32,width:64,height:32}});
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_rectangular_slice_thumbnail_clips_to_the_exact_slice_bounds():
    """矩形切片应按宽高比完整显示，缩略图不能暴露裁剪框之外的白色区域。"""
    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
    styles = (STATIC / "resources/map-workspace.css").read_text(encoding="utf-8")
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const source = {{id:'source-wide',width_px:256,height_px:128}};
const slice = {{
  id:'slice-wide',source_id:source.id,rotation_degrees:0,
  pixel_rect:{{x:32,y:32,width:64,height:32}},
}};
editor.sourceById = new Map([[source.id, source]]);
editor.imageUrls = new Map([[source.id, '/assets/wide.png']]);
editor.sliceRotationPreview = null;

assert.deepEqual(editor.sliceThumbnailLayout(slice, 34), {{
  scale: 0.53125,
  width: 34,
  height: 17,
  left: 0,
  top: 8.5,
}});
const thumbnail = editor.sliceThumb(slice);
assert.match(thumbnail, /left:0px;top:8\\.5px;width:34px;height:17px/);
assert.match(thumbnail, /background-size:136px 68px/);
assert.match(thumbnail, /background-position:-17px -17px/);

editor.sliceRotationPreview = {{sliceId:slice.id,rotation:90}};
assert.match(editor.sliceThumb(slice), /transform:rotate\\(90deg\\)/);
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert ".me2-thumb-image {" in styles
    assert "inset: 0" not in styles[styles.index(".me2-thumb-image {"):].split("}", 1)[0]


def test_large_material_slices_are_stamped_across_their_tile_footprint():
    """多格素材的地图占地由 grid_rect 决定，旋转时交换格数。"""
    source = (STATIC / "resources/map-editor-v2.js").read_text(encoding="utf-8")

    assert "sliceFootprint(slice)" in source
    assert "const gridRect = this.sliceGridRect(slice)" in source
    assert "{ columns: gridRect.height, rows: gridRect.width }" in source
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


def test_material_slice_pixel_crop_must_match_its_tile_grid():
    """服务端拒绝与 Tile 切片范围不一致的内部像素裁剪数据。"""
    document = fresh_ville_editor_document().model_dump(mode="json")
    material_slice = document["material_slices"][0]
    material_slice["pixel_rect"]["width"] += 1

    with pytest.raises(ValidationError, match="must be derived from its Tile grid rect"):
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
            "grid_rect": {"x": 0, "y": 0, "width": 2, "height": 2},
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


def test_map_editor_persists_initial_state_only_on_game_objects():
    document = fresh_ville_editor_document().model_dump(mode="json")
    game_object = next(
        item for item in document["hierarchy_nodes"] if item["kind"] == "GAME_OBJECT"
    )
    game_object["initial_state"] = {"signal": "RED", "powered": True}

    parsed = MapEditorDocumentV2.model_validate(document)

    parsed_object = next(
        item for item in parsed.hierarchy_nodes if item.id == game_object["id"]
    )
    assert parsed_object.initial_state == {"signal": "RED", "powered": True}

    document["hierarchy_nodes"][0]["initial_state"] = {"invalid": True}
    with pytest.raises(ValidationError, match="only GAME_OBJECT nodes"):
        MapEditorDocumentV2.model_validate(document)


def test_new_canvas_uses_confirmed_configuration_and_can_be_undone():
    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
global.requestAnimationFrame = callback => callback();
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
editor.document = {{material_sources:[], material_slices:[], material_canvases:[]}};
editor.workspace = 'world'; editor.selectedCanvasId = null; editor.selectedSourceId = null;
editor.selectedSliceId = null; editor.materialView = 'sources'; editor.readonly = false;
editor.undoStack = []; editor.redoStack = []; editor.images = new Map(); editor.imageUrls = new Map();
editor.expandedMaterialGroups = new Set();
editor.root = {{querySelectorAll: () => [], querySelector: () => null}};
editor.inspector = {{querySelector: () => null}};
editor.reindex = () => {{}}; editor.refreshCanvasImages = () => {{}};
editor.renderAll = () => {{}}; editor.fit = () => {{}}; editor.toast = () => {{}};

editor.createMaterialCanvas({{name:'红绿灯画布', width:12, height:7}});
assert.equal(editor.document.material_canvases.length, 1);
assert.deepEqual(
  [editor.document.material_canvases[0].name, editor.document.material_canvases[0].width_tiles, editor.document.material_canvases[0].height_tiles],
  ['红绿灯画布', 12, 7]
);
assert.equal(editor.undoStack[0].kind, 'canvas-create');
editor.undoMapEdit();
assert.equal(editor.document.material_canvases.length, 0);
assert.equal(editor.workspace, 'world');
editor.redoMapEdit();
assert.equal(editor.document.material_canvases.length, 1);
assert.equal(editor.document.material_canvases[0].name, '红绿灯画布');
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_large_slice_paints_erases_and_restores_as_one_map_stamp():
    """回归验证 ``test_large_slice_paints_erases_and_restores_as_one_map_stamp`` 所描述的业务结果、故障边界和隔离约束。"""
    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const largeSlice = {{
  id: 'slice-large', source_id: 'source', rotation_degrees: 0,
  grid_rect: {{x: 0, y: 0, width: 2, height: 2}},
  pixel_rect: {{x: 0, y: 0, width: 64, height: 64}}
}};
const canvas = {{id:'canvas',source_id:'canvas-source',slice_id:'canvas-slice',width_tiles:6,height_tiles:6,tile_size:32,cells:{{}}}};
editor.document = {{
  material_canvases: [canvas]
}};
editor.sliceById = new Map([[largeSlice.id, largeSlice]]);
editor.sourceById = new Map([['source', {{id:'source',kind:'UPLOADED',width_px:64,height_px:64,tile_width:32,tile_height:32,columns:2,rows:2,margin:0,spacing:0}}]]);
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
largeSlice.grid_rect = {{x: 0, y: 0, width: 2, height: 1}};
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
    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
    script = f"""
const assert = require('node:assert/strict');
global.window = {{}};
require({editor_path});
const editor = Object.create(window.MapEditorV2.prototype);
const road = {{
  id: 'slice-road', source_id: 'source-road', rotation_degrees: 0,
  grid_rect: {{x: 0, y: 0, width: 1, height: 1}},
  pixel_rect: {{x: 0, y: 0, width: 32, height: 32}}
}};
const light = {{
  id: 'slice-light', source_id: 'source-light', rotation_degrees: 0,
  grid_rect: {{x: 0, y: 0, width: 1, height: 2}},
  pixel_rect: {{x: 0, y: 0, width: 32, height: 64}}
}};
const canvas = {{id:'canvas',source_id:'canvas-source',slice_id:'canvas-slice',width_tiles:4,height_tiles:4,tile_size:32,cells:{{}}}};
editor.document = {{
  material_canvases: [canvas]
}};
editor.sliceById = new Map([[road.id, road], [light.id, light]]);
editor.sourceById = new Map([
  [road.source_id, {{id:road.source_id,kind:'UPLOADED',width_px:32,height_px:32,tile_width:32,tile_height:32,columns:1,rows:1,margin:0,spacing:0}}],
  [light.source_id, {{id:light.source_id,kind:'UPLOADED',width_px:32,height_px:64,tile_width:32,tile_height:32,columns:1,rows:2,margin:0,spacing:0}}],
]);
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
    editor_path = json.dumps((STATIC / "resources/map-editor-v2.js").as_posix())
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
    source = (STATIC / "resources/map-editor-v2.js").read_text(encoding="utf-8")
    styles = (STATIC / "resources/map-workspace.css").read_text(encoding="utf-8")

    assert "if (!this.world || this.world.world_key === 'the-ville')" in source
    assert "material_sources: []," in source
    assert "material_slices: []," in source
    assert "material_canvases: []," in source
    assert "used_gid_count: 0" in source
    assert "fetch(window.ResourceScope?.url('/assets') || '/api/studio/resources/assets', { method: 'POST', body })" in source
    assert "/api/studio/resources/assets/${encodeURIComponent(source.asset_id)}/content" in source
    assert "tile.address = node ? this.nodeAddress(node) : [];" in source
    assert "Object.keys(this.document.tile_overrides || {})" in source
    assert "tile_override_layers" in source
    assert "sliceHasTransparency(slice)" in source
    assert "grid-template-columns: minmax(0, 1fr)" in styles
    assert "width: 100% !important" in styles
