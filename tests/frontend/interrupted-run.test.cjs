const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/generative_agents/adapters/web/static/shell/console-api.js', 'utf8');
const cut = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));

function fixture(run) {
  const nodes = {};
  const context = {
    state: {selectedRunId: run.run_id, currentRun: run, workspacePage: 'results', resultGeneration: 2, runHistory: [], resultTab: 'parameters'},
    $: id => nodes[id] ||= {}, document: {visibilityState: 'visible'},
    renderRunSelect() {}, startResultDurationTimer() {}, syncWorkspaceUrl() {}, openModal() {},
  };
  vm.createContext(context);
  vm.runInContext(cut('  function isRunRecoverable(', '  function renderAgents(') +
    cut('  async function openResumeRunModal(', '  async function api(') +
    cut('  async function refreshResultDataUnlocked(', '  function resultPageControls('), context);
  return context;
}

for (const [step, action, expected, button] of [
  [0, 'restart', 'Step 1', '从第 1 步开始'],
  [2, 'resume', 'Step 3', '从第 3 步继续'],
  [5, 'finalize', '生成报告并完成收尾', '完成收尾'],
]) {
  test(`recovery confirmation uses the verified ${action} action at Step ${step}`, async () => {
    const run = {run_id: 'local', status: 'FAILED', committed_step: step, requested_steps: 5, recovery_check_required: true};
    const f = fixture(run);
    f.api = async path => {
      assert.equal(path, '/runs/local/recovery');
      return {...run, recoverable: true, recoverable_step: step, recovery_action: action, active_attempt_id: 'old'};
    };
    await f.openResumeRunModal();
    assert.equal(f.$('resumeRunNextStep').textContent, expected);
    assert.equal(f.state.pendingResumeStep, step);
    assert.equal(f.state.pendingResumeAttemptId, 'old');
    assert.equal(f.$('runContinueBtn').hidden, false);
    assert.equal(f.$('runContinueBtn').textContent, button);
  });
}

test('Web outage retains the last known boundary, hides controls, and clears on reconnection', async () => {
  const run = {run_id: 'local', status: 'RUNNING', committed_step: 3};
  const f = fixture(run);
  f.api = async () => { throw Object.assign(new Error('offline'), {code:'CONTROL_PLANE_NETWORK_ERROR'}); };
  await assert.rejects(f.refreshResultDataUnlocked('local', 2));
  assert.equal(f.state.currentRun.status, 'RUNNING');
  assert.match(f.$('runSupervisionNotice').textContent, /Web 连接中断.*Step 3/);
  assert.equal(f.$('runPauseResumeBtn').hidden, true);
  f.api = async () => ({...run, status: 'FAILED', reason: '仿真执行进程已退出', recovery_check_required: true});
  await f.refreshResultDataUnlocked('local', 2);
  assert.equal(f.state.runConnectionLost, false);
  assert.equal(f.$('runSupervisionNotice').textContent, '仿真执行进程已退出（已提交到 Step 3）');
  assert.equal(f.$('runContinueBtn').hidden, false);
});

test('continuing a failed Run restarts detail polling until the newly completed boundary is shown', async () => {
  const run = {run_id: 'local', status: 'FAILED', committed_step: 2, requested_steps: 4};
  const f = fixture(run);
  Object.assign(f.state, {bootstrapped: true, selectedExperimentId: 'exp', resultRequestGeneration: 8});
  let nextPoll;
  let polls = 0;
  Object.assign(f, {
    setTimeout(fn) { nextPoll = fn; return 1; }, clearTimeout() { nextPoll = null; },
    async reconcileGlobalState() {
      polls++;
      f.state.currentRun = {...run, status: polls === 1 ? 'RUNNING' : 'COMPLETED', committed_step: 2 + polls};
    },
    async api(path, {body}) {
      assert.equal(path, '/runs/local/resume');
      assert.equal(JSON.parse(body).checkpoint_step, 2);
      return {...run, status: 'QUEUED'};
    },
    showToast() {}, syncSelectedExperiment() {}, refreshRunHistoryList() {}, scheduleResultRefresh() {},
  });
  vm.runInContext(cut('  function needsDetailPoll(', '  async function startGlobalActivityStream(') +
    cut('  async function controlRun(', '  function reportError('), f);
  f.scheduleDetailPoll();
  assert.equal(nextPoll, null);
  await f.controlRun('resume', {checkpointStep:2});
  assert.equal(f.state.resultRequestGeneration, 9);
  await nextPoll();
  assert.equal(f.state.currentRun.status, 'RUNNING');
  await nextPoll();
  assert.equal(f.state.currentRun.status, 'COMPLETED');
  assert.equal(f.state.currentRun.committed_step, 4);
  assert.equal(nextPoll, null);
});

test('a standalone Run link opens the result page and polls the Run without an experiment', async () => {
  const f = fixture({run_id:'local', status:'FAILED', committed_step:2, requested_steps:4});
  const opened = [];
  const location = new URL('http://localhost/runs/local?result_tab=operations&tab=traces');
  Object.assign(f, {
    URL, URLSearchParams, location, window:{location}, document:{body:{classList:{add() {}}}},
    goToPage(name) { opened.push(name); }, setOperationTab(tab) { f.state.operationTab = tab; },
    setResultTab(tab) { f.state.resultTab = tab; }, loadResults(runId) { opened.push(runId); },
    refreshResultData(runId) { opened.push(runId); },
  });
  f.$('experimentScopeNotice').querySelector = () => ({});
  vm.runInContext(cut('  async function bootstrapConsole(', "  window.addEventListener('popstate'") +
    cut('  function workspaceUrl(', '  function syncWorkspaceUrl(') +
    cut('  async function reconcileGlobalState(', '  function needsDetailPoll('), f);
  await f.bootstrapConsole();
  await f.reconcileGlobalState({full:true});
  assert.deepEqual(opened, ['results', 'local', 'local']);
  assert.equal(f.state.standaloneRunId, 'local');
  assert.equal(f.workspaceUrl('results'), location.pathname + location.search);
});
