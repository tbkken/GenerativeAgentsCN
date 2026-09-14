const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('generative_agents/web/static/console-api.js', 'utf8');
const cut = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const code = cut('  function isGlobalPage(', '  function workspaceUrl(') + cut('  function goToPage(', '  function renderDirtyState(')
  + cut('  async function openExperiment(', '  function applyExperimentRuntime(')
  + cut('  async function createExperiment(', '  const splitSpatialPath =')
  + cut('  async function deleteExperimentById(', '  async function deleteCurrentRun(');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function setup() {
  const nodes = {}, notices = [], records = [], requests = [];
  const node = id => nodes[id] ||= {
    dataset: {}, hidden: true, value: 'configured',
    selectedOptions: [{dataset: {skillName: 'diagnostic-brain'}}],
    classList: {toggle() {}, remove() {}}, setAttribute() {},
  };
  const state = {
    workspacePage: 'overview', selectedExperimentId: 'source',
    currentExperimentName: '源实验', selectedExperimentIds: new Set(),
    duplicateFeedbackGeneration: 0, duplicateInProgress: false,
    experimentOpenGeneration: 0, resultGeneration: 0, runHistory: [],
  };
  const experiment = id => ({
    experiment_id: id, name: id === 'copy' ? '源实验 副本' : id,
    editable: true, status: 'DRAFT', run_count: 0, definition: null,
  });
  const context = {
    state, $: node, document: {querySelectorAll: () => [], body: node('body')},
    window: {scrollTo() {}}, navigateToExperiment: () => false, statusLabels: {DRAFT: '草稿'},
    requestAnimationFrame: callback => callback(),
    clearTimeout() {}, clearInterval() {},
    stopExperimentListRefresh() {},
    syncMapEditorTopbar() {}, latestRunHasPendingExecution: () => false,
    clearResultDurationTimer() {}, closeLogStream() {}, scheduleGlobalReconcile() {},
    syncWorkspaceUrl() {}, resetResultRuntime() {}, fillDraft() {},
    fillDefinitionOverview() {}, applyStatusPill() {}, setWorkspaceMode() {},
    fillLatestRunSummary: async () => {}, loadExperiments: async () => {},
    closeModal() {}, confirmResourceDeletion: async () => true,
    showToast: (message, title) => notices.push({message, title}),
    recordOperation: (title, message, level) => records.push({title, message, level}),
    reportError: error => { throw error; },
    api: async (url, options = {}) => {
      requests.push({url, method: options.method});
      if (options.method === 'DELETE') return {};
      if (url.endsWith('/duplicate')) return experiment('copy');
      if (url === '/experiments') return experiment('diagnostic');
      return experiment(url.split('/').at(-1));
    },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  return {context, state, nodes, notices, records, requests, experiment};
}

test('copy feedback is cleared through delete, resource centre and fresh experiment creation', async () => {
  const {context, state, nodes} = setup();
  await context.duplicateExperiment('source');
  assert.equal(state.selectedExperimentId, 'copy');
  assert.equal(nodes.duplicateStatus.textContent, '已完成：源实验 副本');
  assert.equal(nodes.duplicateStatus.hidden, false);

  await context.deleteExperimentById('copy', '源实验 副本');
  assert.equal(nodes.duplicateStatus.hidden, true);
  assert.equal(nodes.duplicateStatus.textContent, '');
  context.goToPage('brains');
  await context.createExperiment();
  assert.equal(state.selectedExperimentId, 'diagnostic');
  assert.equal(state.currentExperimentStatus, '草稿');
  assert.equal(nodes.navRunCount.textContent, 0);
  assert.equal(nodes.duplicateStatus.textContent, '');
  assert.equal(nodes.duplicateRetryBtn.hidden, true);
  assert.equal(state.duplicateExperimentId, null);

  await context.duplicateExperiment('diagnostic');
  assert.equal(nodes.duplicateStatus.textContent, '已完成：源实验 副本');
  assert.equal(nodes.duplicateStatus.hidden, false);
});

test('late copy completion records its result without navigating back or replacing new feedback', async () => {
  const {context, state, nodes, notices, records, experiment} = setup();
  const response = deferred(), started = deferred();
  const api = context.api;
  context.api = async (url, options) => {
    if (url.endsWith('/duplicate')) { started.resolve(); return response.promise; }
    return api(url, options);
  };
  const copy = context.duplicateExperiment('source');
  await started.promise;
  context.goToPage('brains');
  await context.openExperiment('new');
  context.setDuplicateStatus('当前实验的提示');
  response.resolve(experiment('copy'));
  await copy;
  assert.equal(state.selectedExperimentId, 'new');
  assert.equal(nodes.duplicateStatus.textContent, '当前实验的提示');
  assert.equal(state.duplicateInProgress, false);
  assert.equal(records.length, 1);
  assert.match(records[0].message, /源实验 副本/);
  assert.equal(notices.filter(item => item.title === '实验已复制').length, 0);
});

test('late copy failure cannot restore an old retry target or show an error in another experiment', async () => {
  const {context, state, nodes, notices, records} = setup();
  const response = deferred(), started = deferred();
  const api = context.api;
  context.api = async (url, options) => {
    if (url.endsWith('/duplicate')) { started.resolve(); return response.promise; }
    return api(url, options);
  };
  const copy = context.duplicateExperiment('source');
  await started.promise;
  await context.openExperiment('new');
  response.reject(new Error('old copy failed'));
  await copy;
  assert.equal(state.selectedExperimentId, 'new');
  assert.equal(nodes.duplicateStatus.hidden, true);
  assert.equal(nodes.duplicateRetryBtn.hidden, true);
  assert.equal(state.duplicateExperimentId, null);
  assert.equal(records[0].level, 'error');
  assert.equal(notices.filter(item => item.title === '实验复制失败').length, 0);
});

test('leaving while the copied experiment detail loads also prevents the delayed page switch', async () => {
  const {context, state, nodes, experiment, records} = setup();
  const detail = deferred(), started = deferred();
  const api = context.api;
  context.api = async (url, options) => {
    if (url === '/experiments/copy') { started.resolve(); return detail.promise; }
    return api(url, options);
  };
  const copy = context.duplicateExperiment('source');
  await started.promise;
  context.goToPage('brains');
  detail.resolve(experiment('copy'));
  await copy;
  assert.equal(state.workspacePage, 'brains');
  assert.equal(state.selectedExperimentId, 'source');
  assert.equal(nodes.duplicateStatus.hidden, true);
  assert.equal(records.length, 1);
});

test('opening another experiment invalidates copy feedback before its detail request completes', async () => {
  const {context, state, nodes, experiment} = setup();
  const response = deferred(), started = deferred(), detail = deferred();
  const api = context.api;
  context.api = async (url, options) => {
    if (url.endsWith('/duplicate')) { started.resolve(); return response.promise; }
    if (url === '/experiments/new') return detail.promise;
    return api(url, options);
  };
  const copy = context.duplicateExperiment('source');
  await started.promise;
  const opening = context.openExperiment('new');
  assert.equal(nodes.duplicateStatus.hidden, true);
  response.resolve(experiment('copy'));
  await copy;
  detail.resolve(experiment('new'));
  await opening;
  assert.equal(state.selectedExperimentId, 'new');
  assert.equal(nodes.duplicateStatus.hidden, true);
});

test('current copy failures retain feedback and retry, then switching experiments clears both', async () => {
  const {context, state, nodes} = setup();
  const api = context.api;
  context.api = async (url, options) => {
    if (url.endsWith('/duplicate')) throw new Error('copy unavailable');
    return api(url, options);
  };
  await assert.rejects(context.duplicateExperiment('source'), /copy unavailable/);
  assert.match(nodes.duplicateStatus.textContent, /复制失败/);
  assert.equal(nodes.duplicateRetryBtn.hidden, false);
  assert.equal(state.duplicateExperimentId, 'source');
  await context.openExperiment('new');
  assert.equal(nodes.duplicateStatus.hidden, true);
  assert.equal(nodes.duplicateRetryBtn.hidden, true);
  assert.equal(state.duplicateExperimentId, null);
});
