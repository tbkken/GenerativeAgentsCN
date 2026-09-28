const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../../src/generative_agents/adapters/web/static/shell/console-api.js'), 'utf8');
const cut = (start, end) => {
  const from = source.indexOf(start);
  assert.notEqual(from, -1, `Missing source marker: ${start}`);
  const to = source.indexOf(end, from);
  assert.notEqual(to, -1, `Missing source marker: ${end}`);
  return source.slice(from, to);
};
const code = cut('  const splitSpatialPath =', '  function releaseAgentImageObjectUrls(')
  + cut('  function requestedAgentBatchChanges(', '  async function previewAgentBatch(');
const plain = value => JSON.parse(JSON.stringify(value));
const ROOM = ['世界', '住宅', '客厅'];
const DESK = [...ROOM, '书桌'];
const OLD = ['世界', '住宅', '卧室', '床'];

function setup() {
  const nodes = new Map(), opened = [], notices = [];
  const node = id => {
    if (!nodes.has(id)) {
      const classes = new Set();
      let value = '';
      nodes.set(id, {
        id, hidden: false, disabled: false, readOnly: false, innerHTML: '', textContent: '', dataset: {},
        get value() { return value; }, set value(next) { value = String(next); },
        classList: {
          contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name),
          toggle(name, active) { if (active) classes.add(name); else classes.delete(name); },
        },
        querySelector: () => null, querySelectorAll: () => [],
        insertAdjacentHTML(position, html) { this.innerHTML += html; },
        focus() {}, setAttribute() {},
      });
    }
    return nodes.get(id);
  };
  const state = {
    selectedExperimentId: 'experiment-a', editingAgentKey: 'agent-a',
    agentEditorContext: {ownerType: 'experiment'},
    draft: {definition: {world: {world_name: '世界', definition: {world: '世界', tiles: [
      {coord: [2, 3], collision: false, address: OLD},
      {coord: [7, 6], collision: false, address: DESK},
      {coord: [6, 6], collision: false, address: ROOM},
    ]}}, agents: []}},
  };
  const context = {
    structuredClone, state, $: node,
    escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    document: {querySelectorAll: () => [], querySelector: () => null},
    window: {SpatialPicker: {open: options => { opened.push(options); }}},
    showToast: (...args) => notices.push(args),
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  node('agentEditorModal').classList.add('open');
  return {context, state, node, opened, notices};
}

test('choosing a new initial location preserves other addresses and previously known places', () => {
  const {context} = setup();
  const original = {
    address: {initial_location: OLD, sleeping: OLD},
    tree: {'世界': {'住宅': {'卧室': ['床'], '车库': []}}},
  };
  const before = structuredClone(original);
  const updated = context.spatialWithInitialLocation(original, DESK);
  assert.deepEqual(plain(updated), {
    address: {initial_location: DESK, sleeping: OLD},
    tree: {'世界': {'住宅': {'卧室': ['床'], '车库': [], '客厅': ['书桌']}}},
  });
  assert.deepEqual(original, before);
  updated.tree['世界']['住宅']['卧室'].push('衣柜');
  assert.deepEqual(original, before);
});

test('an Arena initial location is represented by its own empty known-space branch', () => {
  const {context} = setup();
  const updated = context.spatialWithInitialLocation({}, ROOM);
  assert.deepEqual(plain(updated), {
    address: {initial_location: ROOM}, tree: {'世界': {'住宅': {'客厅': []}}},
  });
});

test('reselecting a known object does not duplicate it or erase other objects', () => {
  const {context} = setup();
  const original = {
    address: {initial_location: OLD},
    tree: {'世界': {'住宅': {'客厅': ['沙发', '书桌'], '卧室': ['床']}}},
  };
  const once = context.spatialWithInitialLocation(original, DESK);
  const twice = context.spatialWithInitialLocation(once, DESK);
  assert.deepEqual(plain(twice), plain(once));
  assert.deepEqual(plain(twice.tree['世界']['住宅']['客厅']), ['沙发', '书桌']);
  const arena = context.spatialWithInitialLocation(twice, ROOM);
  assert.deepEqual(plain(arena.tree['世界']['住宅']['客厅']), ['沙发', '书桌']);
});

test('opening spatial settings keeps the saved coordinate and separates initial location from common addresses', () => {
  const {context, node} = setup();
  node('agentEditX').value = 2;
  node('agentEditY').value = 3;
  context.renderSpatialEditor({
    address: {initial_location: OLD, sleeping: OLD},
    tree: {'世界': {'住宅': {'卧室': ['床']}}},
  });
  assert.equal(node('agentEditX').value, '2');
  assert.equal(node('agentEditY').value, '3');
  assert.equal(node('agentInitialLocationPath').value, OLD.join(' > '));
  assert.doesNotMatch(node('agentAddressRows').innerHTML, /value="(?:initial_location|初始位置)"/);
  assert.match(node('agentAddressRows').innerHTML, /睡觉/);
});

test('selecting an initial location updates coordinates, the address and its required known-space branch', () => {
  const {context, node} = setup();
  context.setAgentInitialLocation({coord: [7, 6], path: DESK});
  assert.equal(node('agentEditX').value, '7');
  assert.equal(node('agentEditY').value, '6');
  assert.equal(node('agentInitialLocationPath').value, DESK.join(' > '));
  assert.match(node('agentSpaceRows').innerHTML, /客厅/);
  assert.match(node('agentSpaceRows').innerHTML, /书桌/);
  const saved = context.readSpatialEditor();
  assert.deepEqual(plain(saved), {
    address: {initial_location: DESK}, tree: {'世界': {'住宅': {'客厅': ['书桌']}}},
  });
  assert.doesNotThrow(() => context.validateInitialLocationAgainstCoord(saved));
  assert.match(node('agentInitialLocationResolved').textContent, /\[7, 6\]/);
});

test('initial, common and known-space selections preserve delimiters inside actual place and object names', () => {
  const {context, state} = setup();
  const path = ['世界/示例', '住区 > A', '起居室，北侧', '书桌,茶台'];
  const objects = [path[3], '窗边 > 茶台/桌'];
  state.draft.definition.world.definition.tiles.push({coord: [8, 8], collision: false, address: path});
  context.setAgentInitialLocation({coord: [8, 8], path});
  const decode = text => text.replaceAll('&quot;', '"').replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&amp;', '&');
  const attribute = (markup, name) => {
    const match = markup.match(new RegExp(`${name}="([^"]*)"`));
    assert.ok(match, `Missing ${name} in ${markup}`);
    return decode(match[1]);
  };
  const addressMarkup = context.agentAddressRowMarkup('living_area', path);
  const spaceMarkup = context.agentSpaceRowMarkup(path.slice(0, 3), objects);
  const addressControl = {value: path.join(' > '), dataset: {path: attribute(addressMarkup, 'data-path')}};
  const spaceControl = {value: path.slice(0, 3).join(' > '), dataset: {path: attribute(spaceMarkup, 'data-path')}};
  const objectsControl = {value: objects.join('，'), dataset: {objects: attribute(spaceMarkup, 'data-objects')}};
  const addressRow = {querySelector: selector => selector === '.agent-address-purpose' ? {value: '居住地'} : addressControl};
  const spaceRow = {querySelector: selector => selector === '.agent-space-path' ? spaceControl : objectsControl};
  context.document.querySelectorAll = selector => selector.startsWith('#agentAddressRows') ? [addressRow] : [spaceRow];
  const saved = context.readSpatialEditor();
  assert.deepEqual(plain(saved.address), {initial_location: path, living_area: path});
  assert.deepEqual(plain(saved.tree), {[path[0]]: {[path[1]]: {[path[2]]: objects}}});
  assert.doesNotThrow(() => context.validateInitialLocationAgainstCoord(saved));
});

test('batch spatial selection moves each Agent with a matching address while preserving their private known places', () => {
  const {context, state, node} = setup();
  state.batchAgentLocation = {coord: [7, 6], path: DESK};
  node('batchAgentTags').value = '新标签';
  const changes = context.requestedAgentBatchChanges();
  assert.deepEqual(plain(changes.coord), [7, 6]);
  assert.deepEqual(plain(changes.initial_location), DESK);
  const agents = ['甲', '乙'].map(name => ({
    agent_key: name, coord: [2, 3], tags: [name],
    spatial: {address: {initial_location: OLD, sleeping: OLD}, tree: {'世界': {'住宅': {'卧室': ['床', `${name}的衣柜`]}}}},
  }));
  const before = structuredClone(agents);
  const updated = agents.map(agent => context.applyRequestedAgentChanges(agent, changes));
  for (const [index, agent] of updated.entries()) {
    assert.deepEqual(plain(agent.coord), [7, 6]);
    assert.deepEqual(plain(agent.spatial.address.initial_location), DESK);
    assert.deepEqual(plain(agent.spatial.address.sleeping), OLD);
    assert.deepEqual(plain(agent.spatial.tree['世界']['住宅']['客厅']), ['书桌']);
    assert.deepEqual(plain(agent.spatial.tree['世界']['住宅']['卧室']), ['床', `${agents[index].agent_key}的衣柜`]);
    assert.deepEqual(plain(agent.tags), [agents[index].agent_key, '新标签']);
  }
  updated[0].spatial.tree['世界']['住宅']['客厅'].push('花瓶');
  assert.deepEqual(plain(updated[1].spatial.tree['世界']['住宅']['客厅']), ['书桌']);
  assert.deepEqual(agents, before);
  changes.coord[0] = 99;
  changes.initial_location[3] = '更改后的对象';
  assert.deepEqual(state.batchAgentLocation, {coord: [7, 6], path: DESK});
});

test('batch changes that leave location alone preserve coordinates and the entire spatial definition', () => {
  const {context, node} = setup();
  node('batchAgentEnabled').value = 'false';
  const changes = context.requestedAgentBatchChanges();
  const agent = {enabled: true, coord: [2, 3], spatial: {
    address: {initial_location: OLD}, tree: {'世界': {'住宅': {'卧室': ['床']}}},
  }};
  const updated = context.applyRequestedAgentChanges(agent, changes);
  assert.deepEqual(plain(changes), {enabled: false});
  assert.deepEqual(plain(updated), {...agent, enabled: false});
});

test('all address entry modes open the current experiment world and deliver the current selection', () => {
  const {context, state, opened} = setup();
  for (const mode of ['spawn', 'address', 'space']) {
    const selected = [];
    context.chooseAgentSpatialLocation({mode, title: '选择空间', selectedPath: OLD, onSelect: value => selected.push(value)});
    const options = opened.at(-1);
    assert.equal(options.world, state.draft.definition.world);
    assert.equal(options.mode, mode);
    assert.equal(options.title, '选择空间');
    assert.deepEqual(options.selectedPath, OLD);
    const value = {path: DESK, coord: [7, 6]};
    options.onSelect(value);
    assert.deepEqual(selected, [value]);
  }
});

for (const [name, alter] of [
  ['another experiment is selected', ({state}) => { state.selectedExperimentId = 'experiment-b'; }],
  ['the selected Agent changes', ({state}) => { state.editingAgentKey = 'agent-b'; }],
  ['another Agent editor replaces it', ({state}) => { state.editingAgentKey = 'agent-b'; state.agentEditorContext = {ownerType: 'experiment'}; }],
  ['the same Agent editor is closed and reopened', ({state}) => { state.agentEditorContext = {ownerType: 'experiment'}; }],
  ['the Agent editor is closed', ({node}) => { node('agentEditorModal').classList.remove('open'); }],
  ['the experiment becomes read-only', ({state}) => { state.draft = null; state.agentEditorContext = {ownerType: 'experiment-readonly'}; }],
  ['the current editor becomes read-only', ({state}) => { state.agentEditorContext.ownerType = 'experiment-readonly'; }],
  ['an Agent save is in progress', ({state}) => { state.agentSaving = true; }],
]) {
  test(`a pending spatial selection cannot write back after ${name}`, () => {
    const env = setup(), selected = [];
    env.context.chooseAgentSpatialLocation({mode: 'spawn', title: '选择初始位置', onSelect: value => selected.push(value)});
    assert.equal(env.opened.length, 1);
    alter(env);
    env.opened[0].onSelect({path: DESK, coord: [7, 6]});
    assert.deepEqual(selected, []);
  });
}

for (const ownerType of ['experiment-readonly', 'public', 'public-readonly']) {
  test(`${ownerType} cannot open a map-bound spatial selector`, () => {
    const {context, state, opened} = setup();
    state.agentEditorContext = {ownerType};
    context.chooseAgentSpatialLocation({mode: 'spawn', title: '选择初始位置', onSelect() { assert.fail('unexpected selection'); }});
    assert.equal(opened.length, 0);
  });
}
