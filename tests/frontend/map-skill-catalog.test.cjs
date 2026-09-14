const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const editorSource = fs.readFileSync('generative_agents/web/static/map-editor-v2.js', 'utf8');
const workspaceSource = fs.readFileSync('generative_agents/web/static/map-workspace.js', 'utf8');
const activateSource = workspaceSource.slice(workspaceSource.indexOf('    async activate()'), workspaceSource.indexOf('    async loadBlueprints()'));
const response = items => ({ok: true, json: async () => ({items})});

function setup(experimentId = null) {
  const requests = [], notices = [], openedMaps = [];
  let catalog = [{name: 'old-signal-skill'}];
  const scope = {experimentId,
    base: experimentId ? `/api/studio/experiments/${experimentId}/resources` : '/api/studio/resources',
    url(path) { return this.base + path; },
  };
  let markup = '', selected = '', options = [];
  const selectionChanged = () => {};
  const selector = {handlers: {change: selectionChanged}};
  Object.defineProperties(selector, {
    innerHTML: {get: () => markup, set(value) {
      markup = value;
      options = [...value.matchAll(/<option value="([^"]*)"/g)].map(match => match[1]);
      if (!options.includes(selected)) selected = options[0] || '';
    }},
    value: {get: () => selected, set(value) {selected = value;}},
  });
  const semanticField = {value: '尚未保存的观察范围说明', selectionStart: 3, selectionEnd: 6};
  const nodes = {mapSearch: {value: ''}};
  const context = {
    window: {ResourceScope: scope, ResourceList: {read: () => ({query: '', page: 1})}},
    document: {getElementById: id => nodes[id], activeElement: semanticField},
    location: {search: '?view=maps&map_id=traffic-map'}, URLSearchParams, AbortController,
    experimentScope: Boolean(experimentId),
    fetch: async (url, options) => {requests.push({url, options}); return response(catalog);},
  };
  vm.createContext(context);
  vm.runInContext(editorSource, context);
  vm.runInContext(`this.manager = {${activateSource}}`, context);
  const editor = Object.create(context.window.MapEditorV2.prototype);
  Object.assign(editor, {
    passiveSkillCatalog: [], skillCatalogScope: '', skillCatalogGeneration: 0,
    world: {world_key: 'traffic-world'}, document: {hierarchy_nodes: [{id: 'camera'}]},
    selectedNodeId: 'camera', nodeEditDraft: {nodeId: 'camera', name: '未保存摄像头名称', semantic: semanticField.value},
    _changed: true, _changeRevision: 7, zoom: 1.5, offsetX: 23,
    inspector: {querySelector: key => key === '[data-node-skill]' ? selector : semanticField},
    toast: (...args) => notices.push(args),
    renderInspector() {throw new Error('catalog refresh replaced the inspector');},
    renderAll() {throw new Error('catalog refresh redrew the editor');},
    setWorld() {throw new Error('catalog refresh replaced the map');},
  });
  Object.defineProperty(editor.inspector, 'innerHTML', {set() {throw new Error('inspector form replaced');}});
  const manager = context.manager;
  Object.assign(manager, {publicEditor: editor, selectedMapId: experimentId || 'traffic-map',
    init() {}, loadMaps: async () => {}, loadBlueprints: async () => {},
    openMap: async (...args) => openedMaps.push(args),
  });
  return {context, editor, manager, scope, selector, semanticField, requests, notices, openedMaps,
    optionValues: () => options,
    setCatalog: value => {catalog = value;},
    setFetch: fn => {context.fetch = (url, options) => {requests.push({url, options}); return fn(url, options);};},
  };
}

test('returning to the same author map discovers a newly created Skill without replacing map or form', async () => {
  const f = setup();
  await f.manager.activate();
  assert.ok(!f.optionValues().includes('traffic-camera-observe'));
  f.selector.value = 'old-signal-skill';
  const original = {world: f.editor.world, document: f.editor.document, draft: f.editor.nodeEditDraft,
    changeHandler: f.selector.handlers.change};
  // Another resource workspace saves a new Skill while this map editor stays mounted.
  f.setCatalog([{name: 'old-signal-skill'}, {name: 'traffic-camera-observe', description: '记录可见活动'}]);
  await f.manager.activate();
  assert.ok(f.optionValues().includes('traffic-camera-observe'));
  assert.equal(f.selector.value, 'old-signal-skill');
  assert.equal(f.selector.handlers.change, original.changeHandler);
  assert.equal(f.context.document.activeElement, f.semanticField);
  assert.equal(f.semanticField.value, '尚未保存的观察范围说明');
  assert.equal(f.semanticField.selectionStart, 3);
  assert.equal(f.editor.world, original.world);
  assert.equal(f.editor.document, original.document);
  assert.equal(f.editor.nodeEditDraft, original.draft);
  assert.equal(f.editor.selectedNodeId, 'camera');
  assert.equal(f.editor.changed, true);
  assert.equal(f.editor.changeRevision, 7);
  assert.equal(f.editor.zoom, 1.5);
  assert.equal(f.editor.offsetX, 23);
  assert.deepEqual(f.openedMaps, []);
  assert.equal(f.requests.length, 2);
});

test('refresh keeps an unsaved selected Skill even if it is absent from the refreshed catalog', async () => {
  const f = setup();
  await f.editor.refreshSkillCatalog();
  f.selector.value = 'pending-binding';
  f.editor.nodeEditDraft.skillName = 'pending-binding';
  f.setCatalog([{name: 'new-camera'}]);
  await f.editor.refreshSkillCatalog();
  assert.deepEqual(f.optionValues(), ['', 'new-camera', 'pending-binding']);
  assert.equal(f.selector.value, 'pending-binding');
  assert.equal(f.editor.nodeEditDraft.skillName, 'pending-binding');
});

test('a late response cannot overwrite a newer catalog, including an already parsing response', async () => {
  const f = setup();
  let releaseBody;
  f.setFetch(async () => ({ok: true, json: () => new Promise(resolve => {releaseBody = resolve;})}));
  const first = f.editor.refreshSkillCatalog();
  await new Promise(resolve => setImmediate(resolve));
  f.setFetch(async () => response([{name: 'new-camera'}]));
  assert.equal(await f.editor.refreshSkillCatalog(), true);
  assert.equal(f.requests[0].options.signal.aborted, true);
  releaseBody({items: [{name: 'stale-signal'}]});
  assert.equal(await first, false);
  assert.deepEqual(f.optionValues(), ['', 'new-camera']);
  assert.equal(f.notices.length, 0);
});

test('scope changes clear the old catalog and ignore late author responses', async () => {
  const f = setup();
  await f.editor.refreshSkillCatalog();
  let releaseAuthor, releaseExperiment;
  f.setFetch(() => new Promise(resolve => {releaseAuthor = resolve;}));
  const author = f.editor.refreshSkillCatalog();
  f.scope.experimentId = 'experiment-a';
  f.scope.base = '/api/studio/experiments/experiment-a/resources';
  f.setFetch(() => new Promise(resolve => {releaseExperiment = resolve;}));
  const experiment = f.editor.refreshSkillCatalog();
  assert.deepEqual(f.optionValues(), ['']);
  releaseExperiment(response([{name: 'package-camera'}]));
  assert.equal(await experiment, true);
  releaseAuthor(response([{name: 'author-camera'}]));
  assert.equal(await author, false);
  assert.deepEqual(f.optionValues(), ['', 'package-camera']);
  assert.equal(f.requests.at(-1).url, '/api/studio/experiments/experiment-a/resources/skills?kind=atomic');
});

test('a request from an abandoned scope is ignored even before a replacement refresh starts', async () => {
  const f = setup('experiment-a');
  let release;
  f.setFetch(() => new Promise(resolve => {release = resolve;}));
  const pending = f.editor.refreshSkillCatalog();
  f.scope.base = '/api/studio/experiments/experiment-b/resources';
  release(response([{name: 'only-in-a'}]));
  assert.equal(await pending, false);
  assert.equal(f.editor.passiveSkillCatalog.length, 0);
});

test('refresh failure is visible, preserves the form, and can be retried on reentry', async () => {
  const f = setup();
  await f.editor.refreshSkillCatalog();
  f.setFetch(async () => ({ok: false, status: 503}));
  assert.equal(await f.editor.refreshSkillCatalog(), false);
  assert.match(f.notices[0][0], /刷新失败/);
  assert.ok(f.optionValues().includes('old-signal-skill'));
  assert.equal(f.semanticField.value, '尚未保存的观察范围说明');
  f.setFetch(async () => response([{name: 'recovered-camera'}]));
  await f.manager.activate();
  assert.deepEqual(f.optionValues(), ['', 'recovered-camera']);
});

test('experiment activation keeps its existing map reload while refreshing only its package Skill catalog', async () => {
  const f = setup('experiment-a');
  f.setCatalog([{name: 'package-camera'}]);
  await f.manager.activate();
  assert.deepEqual(f.openedMaps, [['experiment-a', false]]);
  assert.equal(f.requests[0].url, '/api/studio/experiments/experiment-a/resources/skills?kind=atomic');
  assert.deepEqual(f.optionValues(), ['', 'package-camera']);
});
