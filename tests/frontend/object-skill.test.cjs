const {test} = require('node:test');
const assert = require('node:assert/strict');
global.window = {};
require('../../src/generative_agents/adapters/web/static/resources/map-editor-v2.js');

function fixture() {
  const node = {id: 'camera', name: 'camera', kind: 'GAME_OBJECT', parent_id: 'road',
    bounds: {x: 5, y: 0, width: 1, height: 1}, skill_bindings: [], initial_state: {}};
  const editor = Object.create(window.MapEditorV2.prototype);
  Object.assign(editor, {
    document: {hierarchy_nodes: [node], import_metadata: {width: 13, height: 5}},
    passiveSkillCatalog: [{name: 'camera-skill', description: '观察附近并记录活动'}],
    sliceById: new Map(), nodeDraftValue: (_node, _key, value) => value,
    nodeAddress: () => ['world', 'sector', 'road', 'camera'],
    worldMaterialSlices: () => [], nodeMaterialSlice: () => null,
    stateAppearanceHtml: () => '', nodeMaterialPreviewHtml: () => '',
    clearNodeEditDraft() {}, reindex() {}, renderAll() {}, toast() {},
  });
  return {editor, node};
}

test('object authoring presents one Skill selection with optional folded parameters', () => {
  const {editor, node} = fixture();
  const html = editor.nodeInspector(node);
  assert.match(html, /对象 Skill/);
  assert.match(html, /绑定后，对象每轮/);
  assert.match(html, /<details><summary>感知与交互参数/);
  assert.doesNotMatch(html, /被动 Skill|只有 Agent 明确选择|data-node-interaction-key/);
});

test('saving only a selected Skill activates both capabilities with defaults', () => {
  const {editor, node} = fixture();
  const values = {
    'data-node-name': 'camera', 'data-node-x': '5', 'data-node-y': '0',
    'data-node-w': '1', 'data-node-h': '1', 'data-node-semantic': '道路观察设施',
    'data-node-material': '', 'data-node-initial-state': '{}', 'data-node-skill': 'camera-skill',
    'data-node-interaction-description': '与对象交互', 'data-node-interaction-request': '当前状态？',
    'data-node-interaction-radius': '2', 'data-node-vision-radius': '4', 'data-node-attention-bandwidth': '8',
  };
  const elements = new Map(Object.entries(values).map(([key, value]) => [key, {
    value, addEventListener() {}, matches: () => false,
  }]));
  let save;
  elements.set('data-save-node', {addEventListener: (_event, handler) => {save = handler;}});
  editor.inspector = {
    querySelectorAll: () => [],
    querySelector: selector => elements.get(selector.slice(1, -1)) || null,
  };
  editor.bindNodeInspector(node);
  assert.equal(typeof save, 'function');
  save();
  assert.equal(node.interaction_mode, 'SKILL_BOUND');
  assert.equal(node.skill_bindings.length, 1);
  assert.equal(node.skill_bindings[0].skill_name, 'camera-skill');
  assert.equal(node.skill_bindings[0].interaction_key, 'interact');
  assert.equal(node.skill_bindings[0].vision_radius, 4);
  assert.equal(node.skill_bindings[0].attention_bandwidth, 8);
});
