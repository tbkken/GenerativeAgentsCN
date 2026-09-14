const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('generative_agents/web/static/console-api.js', 'utf8');
const cut = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));

test('experiment header remains sealed while Run status changes or historical Run is selected', () => {
  const nodes = {}, labels = [];
  const context = {
    window: {}, isGlobalPage: () => false,
    state: {selectedExperimentId: 'experiment', selectedRunId: 'historical', workspacePage: 'results'},
    $: id => nodes[id] ||= {},
    statusLabels: {DRAFT: '草稿', SEALED: '已封存', RUNNING: '运行中', PAUSED: '已暂停'},
    applyStatusPill: status => labels.push(status), setWorkspaceMode() {},
  };
  vm.createContext(context);
  vm.runInContext(cut('  function applyExperimentRuntime(', '  async function syncSelectedExperiment('), context);
  for (const status of ['QUEUED', 'RUNNING', 'PAUSED', 'RUNNING', 'COMPLETED']) {
    context.applyExperimentRuntime({id: 'experiment', name: '案例', status: 'SEALED', latest_run: {id: 'latest', status}});
    assert.equal(context.state.selectedRunId, 'historical');
    assert.equal(context.state.currentExperimentStatus, '已封存');
  }
  assert.deepEqual(labels, Array(5).fill('已封存'));
  context.applyExperimentRuntime({id: 'other', name: '错误实验', status: 'DRAFT'});
  assert.equal(context.state.currentExperimentName, '案例');
});

test('history response cannot switch back to an older selection or undo a newer live poll', async () => {
  let finish;
  const nodes = {};
  const context = {
    state: {selectedExperimentId: 'experiment', selectedRunId: 'old', workspacePage: 'results', runHistoryGeneration: 0},
    $: id => nodes[id] ||= {}, escapeHtml: String, statusLabels: {RUNNING: '运行中', PAUSED: '已暂停'},
    api: async () => new Promise(resolve => finish = resolve),
  };
  vm.createContext(context);
  vm.runInContext(cut('  async function refreshRunHistoryList(', '  async function reconcileSelectedRunHistory(')
    + cut('  function renderRunSelect(', '  function resetResultRuntime('), context);
  const request = context.refreshRunHistoryList('experiment');
  context.state.selectedRunId = 'new';
  context.state.currentRun = {run_id: 'new', status: 'RUNNING', completed_steps: 2, requested_steps: 6};
  finish({items: [
    {run_id: 'new', status: 'PAUSED', completed_steps: 1, requested_steps: 6},
    {run_id: 'old', status: 'PAUSED', completed_steps: 1, requested_steps: 6},
  ], next_cursor: null});
  await request;
  assert.equal(nodes.resultRunSelect.value, 'new');
  assert.equal(context.state.runHistory[0].status, 'RUNNING');
  assert.match(nodes.resultRunSelect.innerHTML, /运行中 · 2\/6 步/);
});

test('workspace editability depends on experiment lifecycle, not a Run state', () => {
  const nodes = {};
  const context = {
    state: {draft: {}, workspacePage: 'overview', experiment: {run_count: 1, latest_run: {id: 'run', status: 'RUNNING'}}},
    $: id => nodes[id] ||= {dataset: {}, setAttribute() {}},
    document: {body: {classList: {toggle() {}}}, querySelectorAll: () => []},
  };
  vm.createContext(context);
  vm.runInContext(cut('  function experimentHasRun(', '  function setContentTab('), context);
  context.setWorkspaceMode();
  assert.equal(context.state.workspaceReadonly, false);
  assert.equal(nodes.saveBtn.textContent, '保存配置');
  context.state.draft = null;
  context.state.experiment.latest_run.status = 'PAUSED';
  context.setWorkspaceMode();
  assert.equal(context.state.workspaceReadonly, true);
  assert.equal(nodes.saveBtn.textContent, '查看仿真');
});
