from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]
CONSOLE = ROOT / "generative_agents" / "web" / "static" / "console-api.js"


def test_same_run_refresh_and_activity_backlog_cannot_regress_authoritative_facts():
    node = shutil.which("node")
    assert node, "Node.js is required for the executable state-sync contract"
    program = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const cut = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const production = [
  cut('async function refreshResultData(', 'function applyRunActivity(activity)'),
  cut('function applyRunActivity(activity)', 'function scheduleGlobalReconcile('),
].join('\n');

const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {textContent:'', classList:{contains:()=>false}});
  return elements.get(id);
};
global.history = {replaceState(){}};
global.document = {querySelector(){return null}};
const state = {
  resultGeneration: 4, resultRequestGeneration: 0, selectedRunId: 'run-1',
  selectedExperimentId: 'exp-1', workspacePage: 'results', currentRun: null,
  operationsRunId: 'run-1', replayPlayer: null, replayRunId: null,
  runHistory: [{run_id:'run-1',status:'COMPLETED',completed_steps:10}],
};
const statusLabels = {RUNNING:'运行中',COMPLETED:'已完成',FAILED:'失败'};
const formatTime = value => value || '—';
const formatDuration = () => '1m';
const startResultDurationTimer = run => { $('duration').textContent = run.status; };
const renderTimeline = () => {};
const renderAgents = () => {};
const renderConversations = () => {};
const renderMemories = () => {};
const renderRunActions = run => { $('actions').textContent = run.status; };
const renderRunSelect = () => {};
const renderOperations = operations => { $('artifacts').textContent = operations.marker; };
const syncWorkspaceUrl = () => {};
const refreshOperationFacts = async () => {};
const loadOperationsWorkspace = async () => {};
const ensureReplayPlayer = async () => {};
const reportError = error => { throw error; };
let resultRefreshes = 0;
let globalRefreshes = 0;
const scheduleResultRefresh = () => { resultRefreshes += 1; };
const scheduleGlobalReconcile = () => { globalRefreshes += 1; };
const deferred = [];
const api = path => new Promise(resolve => deferred.push({path, resolve}));

eval(production);

function response(path, status, marker) {
  if (path === '/runs/run-1') return {
    run_id:'run-1',status,revision_no:1,definition_hash:'hash',requested_steps:10,
    virtual_time:null,started_at:null,finished_at:null,
  };
  if (path.includes('/summary')) return {result_state:'COMPLETE',result_version:2,available_step:10,counts:{conversations:0,memories:0,model_calls:0}};
  if (path.includes('/timeline')) return {steps:[]};
  if (path.includes('/agents')) return {items:[]};
  if (path.includes('/conversations') || path.includes('/memories')) return {items:[]};
  if (path.includes('/operations')) return {marker};
  throw new Error(`unexpected path ${path}`);
}
function resolveBatch(batch, status, marker) {
  batch.forEach(item => item.resolve(response(item.path, status, marker)));
}

(async () => {
  const older = refreshResultData('run-1', 4);
  const olderBatch = deferred.splice(0, 6);
  const newer = refreshResultData('run-1', 4);
  const newerBatch = deferred.splice(0, 6);
  resolveBatch(newerBatch, 'COMPLETED', 'new');
  await newer;
  resolveBatch(olderBatch, 'RUNNING', 'old');
  await older;
  if (state.currentRun.status !== 'COMPLETED') throw new Error('older same-Run response regressed currentRun');
  if ($('artifacts').textContent !== 'new') throw new Error('older same-Run response regressed artifacts');

  applyRunActivity({experiment_id:'exp-1',run_id:'run-1',payload:{status:'FAILED',completed_steps:0}});
  if (state.currentRun.status !== 'COMPLETED' || state.runHistory[0].status !== 'COMPLETED') {
    throw new Error('backlog event overwrote authoritative API facts');
  }
  if (resultRefreshes !== 1 || globalRefreshes !== 1) throw new Error('activity was not treated as one invalidation signal');
})().catch(error => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        [node, "-e", program, str(CONSOLE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_result_actions_capture_run_ownership_before_network_wait():
    source = CONSOLE.read_text(encoding="utf-8")
    assert "const runId = state.selectedRunId;" in source
    assert "const experimentId = state.selectedExperimentId;" in source
    assert "runId === state.selectedRunId" in source
    assert "experimentId === state.selectedExperimentId" in source
    assert "requestGeneration !== state.resultRequestGeneration" in source
    assert "factsGeneration !== state.operationFactsGeneration" in source
    assert "Persisted events are invalidation signals" in source
