const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
const cut = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const code = cut('  async function openExperiment(', '  function fillDraft(')
  + cut('  async function acceptSavedDraft(', '  function applyRunEstimateToOverview(')
  + cut('  async function prepareExperimentBrainChoices(', '  const modalFocusableSelector');

function experiment(id, brain = `${id}-brain`, map = `${id}地图`) {
  return {
    id, experiment_id: id, name: id, editable: true, status: 'DRAFT', content_sha256: `${id}-digest`,
    definition: {engine: {brain_skill: brain}, simulation: {max_steps: 120}},
    core_parameters: {world_name: map, agent_count: 20},
  };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

function setup() {
  const nodes = {}, requests = [];
  const node = id => nodes[id] ||= {
    dataset: {}, options: [{textContent: '正在加载…', value: ''}], disabled: true,
    replaceChildren(...children) { this.options = children; this.value = children[0]?.value || ''; },
  };
  const state = {experimentOpenGeneration: 0, selectedExperimentGeneration: 0, workspacePage: 'overview'};
  const context = {
    state, $: node, window: {}, statusLabels: {DRAFT: '草稿'},
    Option: function(text, value) { this.textContent = text; this.value = value; },
    api: async url => { requests.push(url); return experiment(url.split('/').at(-1)); },
    navigateToExperiment: () => false, clearDuplicateFeedback() {}, resetResultRuntime() {},
    clearDirty() { state.dirty = false; }, fillDraft() {}, applyStatusPill() {}, setWorkspaceMode() {},
    goToPage(page) { state.workspacePage = page; }, fillLatestRunSummary: async () => {},
    reportError(error) { throw error; }, isGlobalPage: () => false, scheduleGlobalReconcile() {},
    renderWizardStep() {}, applyRunEstimateToOverview() {},
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  return {context, state, nodes, requests};
}

function assertComposition(nodes, brain, map) {
  for (const [id, name] of [['experimentBrainRevisionSelect', brain], ['experimentMapSelect', map]]) {
    assert.equal(nodes[id].options.length, 1);
    assert.equal(nodes[id].options[0].textContent, name);
    assert.equal(nodes[id].disabled, true);
  }
}

test('opening an experiment renders its embedded Brain and map from the lightweight overview', async () => {
  const {context, nodes, requests} = setup();
  await context.openExperiment('hall');
  assertComposition(nodes, 'hall-brain', 'hall地图');
  assert.equal(nodes.experimentBrainRevisionMeta.textContent, '当前实验内的独立副本');
  assert.equal(nodes.experimentMapMeta.textContent, '当前实验内的独立副本');
  assert.equal(nodes.overviewResourceAgents.textContent, '20 个 Agent');
  assert.deepEqual(requests, ['/experiments/hall']);
});

test('saving settings and refreshing the current experiment preserve then update resource labels', async () => {
  const {context, state, nodes, requests} = setup();
  await context.openExperiment('hall');
  await context.acceptSavedDraft({
    id: 'hall', definition_hash: 'saved-digest', definition: {simulation: {max_steps: 180}},
  });
  assertComposition(nodes, 'hall-brain', 'hall地图');
  assert.equal(state.definition.simulation.max_steps, 180);
  context.api = async url => {
    requests.push(url);
    const updated = experiment('hall', 'updated-brain', '已重命名地图');
    return {...updated, content_sha256: 'updated-digest'};
  };
  await context.syncSelectedExperiment();
  assertComposition(nodes, 'updated-brain', '已重命名地图');
  assert.deepEqual(requests, ['/experiments/hall', '/experiments/hall/status', '/experiments/hall']);

  state.definition.world = {world_name: '完整地图副本的新名称'};
  context.fillDefinitionOverview(state.definition, state.revision);
  assertComposition(nodes, 'updated-brain', '完整地图副本的新名称');
});

test('missing resource data clears previous labels instead of leaving a loading or stale state', async () => {
  const {context, nodes} = setup();
  await context.openExperiment('hall');
  context.api = async () => experiment('empty', '', '');
  await context.openExperiment('empty');
  assertComposition(nodes, '未配置大脑', '未配置地图');
  assert.equal(nodes.experimentBrainRevisionSelect.value, '');
  assert.equal(nodes.experimentMapSelect.value, '');
  assert.equal(nodes.experimentBrainRevisionMeta.textContent, '当前实验未提供大脑配置');
  assert.equal(nodes.experimentMapMeta.textContent, '当前实验未提供地图配置');
});

test('a late experiment response and an obsolete overview cannot replace the current composition', async () => {
  const {context, state, nodes} = setup();
  const old = deferred();
  context.api = async url => url === '/experiments/old' ? old.promise : experiment('current');
  const opening = context.openExperiment('old');
  await context.openExperiment('current');
  old.resolve(experiment('old'));
  await opening;
  context.fillDefinitionOverview({engine: {brain_skill: 'old-brain'}, world: {world_name: '旧地图'}}, {id: 'old'});
  assert.equal(state.selectedExperimentId, 'current');
  assertComposition(nodes, 'current-brain', 'current地图');
});

test('a delayed creation-wizard catalogue load does not change the experiment resource summary', async () => {
  const {context, nodes} = setup();
  const choices = deferred();
  context.window.ResourceList = {choices: async () => choices.promise};
  const preparing = context.prepareExperimentBrainChoices();
  await context.openExperiment('hall');
  choices.resolve();
  await preparing;
  assertComposition(nodes, 'hall-brain', 'hall地图');
});
