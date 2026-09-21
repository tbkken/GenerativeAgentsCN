const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../../src/generative_agents/adapters/web/static/shell/console-api.js'), 'utf8');
const editorSource = source.slice(source.indexOf('  function setAgentEditorReadOnly('), source.indexOf('  window.SharedAgentEditor ='));

function agent(key, displaySize) {
  return {
    agent_key: key, enabled: true, name: key, sprite_display_tiles: displaySize,
    portrait_asset: `assets/${key}-portrait.png`, sprite_asset: `assets/${key}-sprite.png`,
    sprite_layout: '4x4', model_override: null, tags: ['traffic'], goals: ['到达报到入口'],
    coord: [8, 26], currently: '骑行报到',
    scratch: { age: 28, innate: '谨慎', learned: '熟悉规则', lifestyle: '早起', daily_plan: '前往报到入口' },
    spatial: { address: { home: ['街区', '沿街街区', '非机动车道', '出发区'] }, tree: {} },
    perception: { mode: 'box', vision_radius: 20, attention_bandwidth: 8 },
  };
}

function setup(agents) {
  const nodes = new Map(), savedDrafts = [], savedPublic = [], imageRenders = [], modalCalls = [];
  let spatial;
  const node = id => {
    if (!nodes.has(id)) {
      let value = '';
      nodes.set(id, {
        id, hidden: false, disabled: false, textContent: '',
        get value() { return value; }, set value(next) { value = String(next); },
        classList: { toggle() {} }, querySelectorAll: () => [],
        focus() { context.document.activeElement = this; },
      });
    }
    return nodes.get(id);
  };
  const context = {
    structuredClone, CSS: { escape: value => value }, $: node,
    state: { draft: { definition: { agents: structuredClone(agents), crowds: [] } }, agentImageFiles: {} },
    document: {
      activeElement: node('opener'),
      querySelector: selector => node(selector),
    },
    window: { CrowdWorkspace: {
      saveSharedAgent: async payload => { savedPublic.push(structuredClone(payload.definition)); return payload.definition; },
      afterSharedAgentSaved: async () => {},
    } },
    renderAgentImageEditor(value, hasExisting) {
      imageRenders.push({ key: value.agent_key, hasExisting });
      context.state.agentImageFiles = {};
      node('agentEditPortrait').value = value.portrait_asset || value.portrait_asset_id || '';
      node('agentEditSprite').value = value.sprite_asset || value.sprite_asset_id || '';
      context.state.agentSpriteLayout = value.sprite_layout;
    },
    renderSpatialEditor: value => { spatial = structuredClone(value); },
    readSpatialEditor: () => structuredClone(spatial), validateInitialLocationAgainstCoord() {},
    setContentTab() {},
    openModal: (...args) => { modalCalls.push(args); }, closeModal() {},
    requestAnimationFrame: callback => callback(), releaseAgentImageObjectUrls() {}, showToast() {},
    uploadStagedAgentImages: async () => ({
      portrait: node('agentEditPortrait').value || null, sprite: node('agentEditSprite').value || null,
      sprite_layout: context.state.agentSpriteLayout,
    }),
    saveDraft: async () => { savedDrafts.push(structuredClone(context.state.draft.definition)); },
  };
  node('agentEditorModal').querySelectorAll = selector => selector.startsWith('.content-tab-panel')
    ? [...nodes.values()].filter(control => control.id.startsWith('agentEdit')) : [];
  vm.createContext(context);
  vm.runInContext(editorSource, context);
  return { context, node, savedDrafts, savedPublic, imageRenders, modalCalls };
}

for (const displaySize of [2, null]) {
  test(`opening an existing draft Agent and saving untouched preserves display size ${displaySize}`, async () => {
    const original = agent('commuter', displaySize);
    const { context, node, savedDrafts, imageRenders, modalCalls } = setup([original]);
    context.openAgentEditor(original.agent_key);
    assert.equal(node('agentEditSpriteDisplayTiles').value, displaySize == null ? '' : String(displaySize));
    assert.equal(node('agentEditSpriteDisplayTiles').disabled, false);
    assert.equal(node('[data-content-tab="space"]').hidden, false);
    assert.equal(context.document.activeElement.id, 'agentEditName');
    assert.equal(modalCalls[0][2].id, 'opener');
    assert.deepEqual(imageRenders, [{ key: original.agent_key, hasExisting: true }]);
    await context.saveAgentEditor();
    assert.equal(savedDrafts.length, 1);
    assert.deepEqual(savedDrafts[0].agents[0], original);
    context.state.draft.definition = structuredClone(savedDrafts[0]);
    context.openAgentEditor(original.agent_key);
    assert.equal(node('agentEditSpriteDisplayTiles').value, displaySize == null ? '' : String(displaySize));
  });
}

test('switching Agents and adding a new Agent clears the previous display size', () => {
  const { context, node, imageRenders } = setup([agent('two', 2), agent('default', null), agent('three', 3)]);
  for (const [key, expected] of [['two', '2'], ['default', ''], ['three', '3'], [null, '']]) {
    context.openAgentEditor(key);
    assert.equal(node('agentEditSpriteDisplayTiles').value, expected);
  }
  assert.equal(context.state.editingAgentKey, null);
  assert.equal(imageRenders.at(-1).hasExisting, false);
});

test('public Agent retains its display size and does not leak it into a draft Agent', async () => {
  const publicAgent = agent('public', 2);
  publicAgent.portrait_asset_id = publicAgent.portrait_asset;
  publicAgent.sprite_asset_id = publicAgent.sprite_asset;
  const { context, node, savedPublic } = setup([agent('draft', null)]);
  await context.openPublicAgentEditor({ agentDraft: { definition: publicAgent } });
  assert.equal(node('agentEditSpriteDisplayTiles').value, '2');
  assert.equal(node('[data-content-tab="space"]').hidden, true);
  await context.saveAgentEditor();
  assert.equal(savedPublic[0].sprite_display_tiles, 2);
  context.openAgentEditor('draft');
  assert.equal(node('agentEditSpriteDisplayTiles').value, '');
  assert.equal(node('[data-content-tab="space"]').hidden, false);
});

test('sealed Agent shows its size read-only and reopening a draft restores editing', async () => {
  const original = agent('sealed', 2);
  const { context, node, savedDrafts, savedPublic } = setup([]);
  context.state.draft = null;
  context.state.definition = { agents: [original] };
  context.openAgentEditor('sealed');
  assert.equal(node('agentEditSpriteDisplayTiles').value, '2');
  assert.equal(node('agentEditSpriteDisplayTiles').disabled, true);
  assert.equal(node('saveAgentEditor').hidden, true);
  await context.saveAgentEditor();
  assert.equal(savedDrafts.length + savedPublic.length, 0);
  assert.deepEqual(context.state.definition.agents[0], original);
  context.state.draft = { definition: { agents: [agent('draft', null)] } };
  context.openAgentEditor('draft');
  assert.equal(node('agentEditSpriteDisplayTiles').value, '');
  assert.equal(node('agentEditSpriteDisplayTiles').disabled, false);
  assert.equal(node('saveAgentEditor').hidden, false);
});
