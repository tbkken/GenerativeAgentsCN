from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from generative_agents.persistence.models import RunEvent
from tests.architecture.test_run_observability_lifecycle_redlines import (
    _publish_run,
    web_runtime,
)


ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "generative_agents" / "web" / "static" / "console-api.js"


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


async def _read_global_sse(
    endpoint,
    *,
    after_id: int,
    last_event_id: str | None,
    activity_count: int,
) -> list[str]:
    response = await endpoint(
        request=_ConnectedRequest(),
        after_id=after_id,
        last_event_id=last_event_id,
    )
    iterator = response.body_iterator
    chunks: list[str] = []
    try:
        # Every connection starts with retry + sync, followed by the requested facts.
        for _ in range(activity_count + 2):
            chunk = await anext(iterator)
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    finally:
        await iterator.aclose()
    return chunks


def _activity_items(chunks: list[str]) -> list[dict]:
    items = []
    for chunk in chunks:
        if "event: activity" not in chunk:
            continue
        data = next(line[6:] for line in chunk.splitlines() if line.startswith("data: "))
        item = json.loads(data)
        event_id = int(next(line[4:] for line in chunk.splitlines() if line.startswith("id: ")))
        assert item["id"] == event_id
        items.append(item)
    return items


def test_def_046_one_runtime_reconciles_external_run_lifecycle_everywhere():
    """One un-reloaded runtime must converge every Run surface from global activity."""

    node = shutil.which("node")
    assert node, "Node.js is required for the executable global reconciliation contract"
    program = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const cut = (start, end) => {
  const left = source.indexOf(start);
  const right = source.indexOf(end, left + start.length);
  if (left < 0 || right < 0) throw new Error(`missing production slice: ${start} -> ${end}`);
  return source.slice(left, right);
};
const production = [
  cut('function workspaceUrl(', 'function goToPage('),
  cut('function isRunRecoverable(', 'function renderRunActions('),
  cut('function cardTemplate(item)', 'async function loadExperiments()'),
  cut('async function loadExperiments()', 'function renderPages(totalPages)'),
  cut('function renderPages(totalPages)', 'function updateTabCounts(counts)'),
  cut('function updateTabCounts(counts)', 'async function openExperiment('),
  cut('function applyExperimentRuntime(experiment)', 'async function syncSelectedExperiment('),
  cut('async function syncSelectedExperiment(', 'function fillDraft(definition)'),
  cut('async function fillLatestRunSummary(experiment)', 'function setSwitch('),
  cut('async function refreshRunHistoryList(', 'async function loadRunHistory('),
  cut('function renderRunSelect(', 'function resetResultRuntime('),
  cut('function scheduleResultRefresh(', 'async function refreshResultData('),
  cut('async function refreshResultData(', 'function applyRunActivity(activity)'),
  cut('function applyRunActivity(activity)', 'function scheduleGlobalReconcile('),
  cut('function scheduleGlobalReconcile(', 'async function reconcileGlobalState('),
  cut('async function reconcileGlobalState(', 'async function startGlobalActivityStream()'),
  cut('async function startGlobalActivityStream()', 'function renderRunActions(run)'),
  cut('function renderRunActions(run)', 'function renderAgents('),
  cut('function reconcileAfterPageResume()', "document.addEventListener('visibilitychange'"),
].join('\n');
const resumeHooks = cut(
  "document.addEventListener('visibilitychange'",
  "window.addEventListener('beforeunload'"
);

const elements = new Map();
function makeClassList() {
  const values = new Set();
  return {
    add: (...items) => items.forEach(item => values.add(item)),
    remove: (...items) => items.forEach(item => values.delete(item)),
    toggle: (item, force) => force === undefined
      ? (values.has(item) ? (values.delete(item), false) : (values.add(item), true))
      : (force ? values.add(item) : values.delete(item), Boolean(force)),
    contains: item => values.has(item),
  };
}
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    id, textContent: '', innerHTML: '', hidden: false, disabled: false, value: '',
    className: '', dataset: {}, classList: makeClassList(),
    lastChild: { textContent: '' }, previousElementSibling: { value: '' },
  });
  return elements.get(id);
}
const filterTabs = ['all', 'running', 'queued', 'draft', 'paused', 'completed', 'abnormal']
  .map(filter => ({ dataset: { filter }, textContent: '', classList: makeClassList() }));
const documentHandlers = {};
const windowHandlers = {};
global.document = {
  visibilityState: 'visible',
  getElementById: element,
  querySelectorAll: selector => selector === '.filter-tab[data-filter]' ? filterTabs : [],
  querySelector: selector => selector === '[data-result-panel="timeline"]'
    ? { classList: { contains: () => false } }
    : null,
  addEventListener: (name, handler) => { documentHandlers[name] = handler; },
};
global.window = {
  addEventListener: (name, handler) => { windowHandlers[name] = handler; },
  location: { href: 'http://localhost/', pathname: '/', search: '' },
};
global.history = { replaceState: () => {} };
global.location = window.location;

let timerSequence = 0;
let timers = [];
global.setTimeout = handler => { timers.push(handler); return ++timerSequence; };
global.clearTimeout = () => {};
async function drainTimers() {
  for (let round = 0; round < 20; round += 1) {
    const pending = timers; timers = [];
    for (const handler of pending) await handler();
    await new Promise(resolve => setImmediate(resolve));
    if (!timers.length) {
      await new Promise(resolve => setImmediate(resolve));
      if (!timers.length) return;
    }
  }
  throw new Error('global reconciliation timers did not drain');
}

class FakeEventSource {
  static CLOSED = 2;
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.listeners = new Map();
    this.closed = false;
    FakeEventSource.instances.push(this);
  }
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  emit(name, data) { this.listeners.get(name)?.({ type: name, data: JSON.stringify(data) }); }
  close() { this.closed = true; this.readyState = FakeEventSource.CLOSED; }
}
global.EventSource = FakeEventSource;

const statuses = ['DRAFT', 'QUEUED', 'RUNNING', 'COMPLETED'];
let phaseIndex = 0;
const phase = () => statuses[phaseIndex];
const runForPhase = () => ({
  run_id: 'run-00000001', experiment_id: 'exp-1', revision_id: 'rev-1', revision_no: 1,
  definition_hash: 'abc123def456', status: phase(), requested_steps: 10,
  completed_steps: phase() === 'RUNNING' ? 5 : phase() === 'COMPLETED' ? 10 : 0,
  recoverable_step: phase() === 'RUNNING' ? 5 : phase() === 'COMPLETED' ? 10 : 0,
  available_step: phase() === 'RUNNING' ? 5 : phase() === 'COMPLETED' ? 10 : 0,
  created_at: '2026-08-09T00:00:00+08:00', started_at: '2026-08-09T00:00:01+08:00',
  finished_at: phase() === 'COMPLETED' ? '2026-08-09T00:01:00+08:00' : null,
  virtual_time: '2026-02-13T00:00:00+08:00',
});
const experimentForPhase = () => ({
  id: 'exp-1', experiment_key: 'experiment-global-sync', name: '全局同步实验', goal: '验证未刷新收敛',
  status: phase(), revision_no: 1, run_count: phase() === 'DRAFT' ? 0 : 1,
  current_draft: null, current_published: { id: 'rev-1' },
  latest_run: phase() === 'DRAFT' ? null : { id: 'run-00000001' },
});
const experimentListItem = () => ({
  ...experimentForPhase(), updated_at: '2026-08-09T00:00:00+08:00',
  core_parameters: {
    agent_count: 2, chat_model: 'Qwen/test', embedding_model: 'embed/test',
    start_time: '2026-02-13T00:00:00+08:00', stride_minutes: 10,
    world_name: 'the Ville', random_seed: 42,
  },
  progress: phase() === 'DRAFT' ? null : {
    completed_steps: runForPhase().completed_steps, requested_steps: 10,
  },
  latest_run: phase() === 'DRAFT' ? null : runForPhase(),
});
const operationsForPhase = () => ({
  attempts: phase() === 'DRAFT' ? [] : [{ attempt_id: 'attempt-1', status: phase() }],
  artifact_jobs: phase() === 'COMPLETED' ? [
    { job_type: 'BUILD_REPLAY', status: 'SUCCEEDED' },
    { job_type: 'BUILD_REPORT', status: 'SUCCEEDED' },
  ] : [],
});
async function api(path) {
  if (path === '/events?tail=true') return { next_after_id: phaseIndex };
  if (path.startsWith('/experiments?')) {
    const counts = { ALL: 1, DRAFT: 0, QUEUED: 0, RUNNING: 0, COMPLETED: 0, FAILED: 0, CANCELLED: 0 };
    counts[phase()] = 1;
    return { items: [experimentListItem()], total: 1, page: 1, page_size: 10, total_pages: 1, status_counts: counts };
  }
  if (path === '/experiments/exp-1') return experimentForPhase();
  if (path === '/experiments/exp-1/runs?limit=100') return {
    items: phase() === 'DRAFT' ? [] : [runForPhase()], next_cursor: null,
  };
  if (path === '/runs/run-00000001') return runForPhase();
  if (path.startsWith('/runs/run-00000001/results/timeline')) return { events: [] };
  if (path === '/runs/run-00000001/results/agents') return { items: [] };
  if (path.startsWith('/runs/run-00000001/results/conversations')) return { items: [] };
  if (path.startsWith('/runs/run-00000001/results/memories')) return { items: [] };
  if (path === '/runs/run-00000001/results/operations') return operationsForPhase();
  throw new Error(`unexpected API ${path}`);
}

const state = {
  page: 1, pageSize: 10, status: '', query: '', selectedExperimentId: 'exp-1',
  experiment: experimentForPhase(), draft: null, definition: null,
  revision: { id: 'rev-1', state: 'PUBLISHED', definition_hash: 'abc123def456' },
  currentRun: null, latestRunId: null, selectedRunId: null, runHistory: [],
  runHistoryGeneration: 0, experimentListGeneration: 0, selectedExperimentGeneration: 0,
  latestSummaryGeneration: 0, activityGeneration: 0, resultGeneration: 1,
  globalRefreshTimer: null, resultRefreshTimer: null, resultDurationTimer: null,
  pendingActivityExperimentIds: new Set(),
  forceGlobalRefresh: false, activitySource: null, eventSource: null,
  currentExperimentName: '全局同步实验', currentExperimentStatus: '草稿',
  workspacePage: 'results', dirty: false, bootstrapped: true,
  operationsRunId: null, operationsAbortController: null, replayPlayer: null, replayRunId: null,
};
const $ = element;
const statusLabels = {
  DRAFT: '草稿', QUEUED: '排队中', STARTING: '正在启动', RUNNING: '运行中',
  COMPLETED: '已完成', FAILED: '失败', CANCELLED: '已取消', INTERRUPTED: '已中断',
};
const statusClasses = { DRAFT: 'draft', QUEUED: 'queued', RUNNING: 'running', COMPLETED: 'completed' };
const escapeHtml = value => String(value ?? '');
const formatTime = value => value || '—';
const formatDuration = () => '1m';
const startResultDurationTimer = run => {
  $('resultDurationLabel').textContent = run.finished_at ? '实际耗时' : '执行时间';
  $('resultDurationMetric').textContent = formatDuration(run.started_at, run.finished_at);
};
const applyStatusPill = status => { $('statusPill').textContent = status; };
const setWorkspaceMode = status => { $('workspaceMode').textContent = status; };
const executionLocksRevision = () => false;
const fillDefinitionOverview = () => {};
const fillDraft = () => {};
const showToast = () => {};
const reportError = error => { throw error; };
const renderTimeline = () => {};
const renderAgents = () => {};
const renderConversations = () => {};
const renderMemories = () => {};
const renderOperations = operations => {
  $('artifactStatus').textContent = (operations.artifact_jobs || [])
    .map(item => `${item.job_type}:${item.status}`).join('|') || 'NONE';
};
const loadOperationsWorkspace = async (runId, generation) => {
  if (generation !== state.resultGeneration || runId !== state.selectedRunId) return;
  state.operationsRunId = runId;
  renderOperations(operationsForPhase());
};
const refreshOperationFacts = async (runId, generation) => {
  if (generation !== state.resultGeneration || runId !== state.selectedRunId) return;
  renderOperations(operationsForPhase());
};
const ensureReplayPlayer = async () => {};
const closeLogStream = () => {};
const teardownReplay = () => {};

eval(production);
eval(resumeHooks);

const check = (condition, message) => { if (!condition) throw new Error(message); };
const tabText = key => filterTabs.find(tab => tab.dataset.filter === key).textContent;
function assertPhase(expected) {
  const label = statusLabels[expected];
  const run = expected === 'DRAFT' ? null : runForPhase();
  check($('experimentList').innerHTML.includes(label), `${expected}: experiment list stale`);
  check(tabText(expected.toLowerCase()).endsWith(' 1'), `${expected}: filter count stale (${tabText(expected.toLowerCase())})`);
  check($('statusPill').textContent === label, `${expected}: detail status stale`);
  if (run) {
    check($('overviewLatestStep').textContent === `${run.completed_steps}/10`, `${expected}: overview progress stale`);
    check($('overviewLatestMeta').textContent === label, `${expected}: overview status stale`);
    check($('resultRunSelect').innerHTML.includes('run-00000001'), `${expected}: run selector missing`);
    check(state.runHistory[0].status === expected, `${expected}: run selector source stale`);
  }
}
async function emitPhase(nextPhase) {
  phaseIndex = statuses.indexOf(nextPhase);
  const source = state.activitySource;
  source.emit('activity', {
    experiment_id: 'exp-1', run_id: 'run-00000001', event_type: 'state',
    payload: {
      status: nextPhase,
      completed_steps: runForPhase().completed_steps,
      recoverable_step: runForPhase().recoverable_step,
    },
  });
  await drainTimers();
}

(async () => {
  await startGlobalActivityStream();
  assertPhase('DRAFT');

  await emitPhase('QUEUED');
  state.selectedRunId = 'run-00000001';
  await refreshResultData('run-00000001', state.resultGeneration);
  assertPhase('QUEUED');
  check($('runPauseResumeBtn').hidden, 'QUEUED: pause must be hidden');
  check(!$('runCancelBtn').hidden, 'QUEUED: cancel action stale');

  await emitPhase('RUNNING');
  assertPhase('RUNNING');
  check(!$('runPauseResumeBtn').hidden && !$('runCancelBtn').hidden, 'RUNNING: controls stale');

  const staleSource = state.activitySource;
  await startGlobalActivityStream();
  check(staleSource.closed, 'reconnect did not close the old global stream');
  const beforeStale = state.currentRun.status;
  staleSource.emit('activity', {
    experiment_id: 'exp-1', run_id: 'run-00000001', event_type: 'state',
    payload: { status: 'FAILED', completed_steps: 0 },
  });
  await drainTimers();
  check(state.currentRun.status === beforeStale, 'stale EventSource overwrote the active runtime');

  await emitPhase('COMPLETED');
  assertPhase('COMPLETED');
  check($('runPauseResumeBtn').hidden && $('runCancelBtn').hidden, 'COMPLETED: live controls visible');
  check($('artifactStatus').textContent === 'BUILD_REPLAY:SUCCEEDED|BUILD_REPORT:SUCCEEDED', 'COMPLETED: artifacts stale');

  state.activitySource.close();
  windowHandlers.focus();
  await drainTimers();
  check(state.activitySource && !state.activitySource.closed, 'focus did not reconnect closed activity stream');
  document.visibilityState = 'visible';
  documentHandlers.visibilitychange();
  await drainTimers();
  check(state.currentRun.status === 'COMPLETED', 'visibility reconcile regressed terminal state');

  process.stdout.write(JSON.stringify({
    phases: statuses,
    streamCount: FakeEventSource.instances.length,
    finalStatus: state.currentRun.status,
    finalFilter: tabText('completed'),
    finalArtifacts: $('artifactStatus').textContent,
  }));
})().catch(error => { console.error(error.stack || error); process.exit(1); });
"""
    completed = subprocess.run(
        [node, "-e", program, str(CONSOLE)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    evidence = json.loads(completed.stdout)
    assert evidence == {
        "phases": ["DRAFT", "QUEUED", "RUNNING", "COMPLETED"],
        "streamCount": 3,
        "finalStatus": "COMPLETED",
        "finalFilter": "已完成 1",
        "finalArtifacts": "BUILD_REPLAY:SUCCEEDED|BUILD_REPORT:SUCCEEDED",
    }


def test_def_046_global_sse_backlog_reconnect_and_terminal_tail_are_cursor_exact(
    web_runtime,
):
    """Global SSE must drain backlog, resume exactly, and never replay stale history."""

    client, database, var_dir, app = web_runtime
    experiment, _revision, run = _publish_run(
        database, var_dir, "global-sse-reconciliation"
    )
    with database.session_factory.begin() as session:
        previous = session.query(RunEvent).order_by(RunEvent.id.desc()).first()
        baseline = previous.id if previous is not None else 0
        inserted_ids = []
        for sequence in range(207):
            event = RunEvent(
                run_id=run["run_id"],
                event_type="progress",
                payload_json={"status": "RUNNING", "sequence": sequence},
                created_at=datetime.now(timezone.utc),
            )
            session.add(event)
            session.flush()
            inserted_ids.append(event.id)

    # The finite cursor endpoint must page the same ordered, gap-free fact set.
    cursor = baseline
    paged_ids: list[int] = []
    while len(paged_ids) < len(inserted_ids):
        page = client.get(
            "/api/v1/events",
            params={"after_id": cursor, "limit": 31},
        )
        assert page.status_code == 200, page.text
        payload = page.json()
        ids = [item["id"] for item in payload["items"]]
        assert ids == sorted(ids)
        assert all(item["experiment_id"] == experiment["id"] for item in payload["items"])
        assert all(item["run_id"] == run["run_id"] for item in payload["items"])
        paged_ids.extend(ids)
        assert payload["next_after_id"] >= cursor
        if not ids:
            break
        cursor = payload["next_after_id"]
    assert paged_ids == inserted_ids

    endpoint = next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == "/api/v1/events/stream"
    )
    first_chunks = asyncio.run(
        _read_global_sse(
            endpoint,
            after_id=baseline,
            last_event_id=None,
            activity_count=73,
        )
    )
    first_items = _activity_items(first_chunks)
    assert [item["id"] for item in first_items] == inserted_ids[:73]
    reconnect_cursor = first_items[-1]["id"]

    remaining_chunks = asyncio.run(
        _read_global_sse(
            endpoint,
            after_id=0,
            last_event_id=str(reconnect_cursor),
            activity_count=len(inserted_ids) - 73,
        )
    )
    remaining_items = _activity_items(remaining_chunks)
    assert [item["id"] for item in remaining_items] == inserted_ids[73:]
    assert [item["id"] for item in first_items + remaining_items] == inserted_ids

    # A new frontend runtime tails before opening EventSource. Historical RUNNING
    # must not be replayed over the reconciled terminal snapshot.
    tail = client.get("/api/v1/events", params={"tail": True})
    assert tail.status_code == 200, tail.text
    tail_cursor = tail.json()["next_after_id"]
    assert tail_cursor == inserted_ids[-1]
    with database.session_factory.begin() as session:
        terminal = RunEvent(
            run_id=run["run_id"],
            event_type="state",
            payload_json={"status": "COMPLETED", "completed_steps": 100},
            created_at=datetime.now(timezone.utc),
        )
        session.add(terminal)
        session.flush()
        terminal_id = terminal.id

    terminal_chunks = asyncio.run(
        _read_global_sse(
            endpoint,
            after_id=tail_cursor,
            last_event_id=None,
            activity_count=1,
        )
    )
    terminal_items = _activity_items(terminal_chunks)
    assert [(item["id"], item["payload"]["status"]) for item in terminal_items] == [
        (terminal_id, "COMPLETED")
    ]
