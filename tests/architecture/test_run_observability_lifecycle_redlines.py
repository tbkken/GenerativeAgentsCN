"""Regressions for current shared kernel and Studio surfaces."""

from __future__ import annotations
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from generative_agents.ga_runtime.engine.context import SimulationClock
from generative_agents.ga_protocol.packages.byte_windows import read_utf8_window

ROOT = Path(__file__).resolve().parents[2]


SHELL = ROOT / 'src' / 'generative_agents' / 'adapters' / 'web' / 'static' / 'shell/experiment-console.html'


CONSOLE = ROOT / 'src' / 'generative_agents' / 'adapters' / 'web' / 'static' / 'shell/console-api.js'


PLAYER = ROOT / 'src' / 'generative_agents' / 'adapters' / 'web' / 'static' / 'replay/replay-player.js'


PHASER = ROOT / 'src' / 'generative_agents' / 'adapters' / 'web' / 'static' / 'vendor' / 'phaser.min.js'


def test_def_048_operations_ui_has_real_logs_checkpoints_traces_and_stale_guards():
    """ROL-LOG/TRACE/CHK/SYNC cannot be represented by aggregate cards only."""

    shell = SHELL.read_text(encoding="utf-8")
    script = CONSOLE.read_text(encoding="utf-8")
    required_ids = {
        "operationsSubtabs",
        "attemptLogSelect",
        "logViewport",
        "logSearch",
        "logLevelFilter",
        "logAutoFollow",
        "logDownload",
        "modelTraceRows",
        "checkpointRows",
        "checkpointDetail",
    }
    for element_id in required_ids:
        assert f'id="{element_id}"' in shell, f"missing operations UI contract: {element_id}"
    assert "AbortController" in script
    assert "closeLogStream" in script
    assert "logGeneration" in script and "checkpointGeneration" in script
    assert "runId !== state.selectedRunId" in script


def test_log_polling_is_bounded_and_cannot_reuse_previous_attempt():
    script = CONSOLE.read_text(encoding="utf-8")
    poll = script[script.index("function startLogStream"):script.index("async function selectAttemptLog")]
    assert "new EventSource" not in poll
    assert "attemptId !== state.selectedAttemptId" in poll
    assert "generation !== state.logGeneration" in poll
    assert "if (!source.closed) source.timer = setTimeout(poll, 1000)" in poll
    assert "page.eof && page.terminal" in poll


def test_def_048_periodic_operations_refresh_cannot_destroy_attempt_dom_owner():
    """ROL-LOG-001/SYNC-001 only the Attempt renderer owns interactive rows."""

    node = shutil.which("node")
    assert node, "Node.js is required for the executable operations DOM contract"
    source = CONSOLE.read_text(encoding="utf-8")
    attempts = source[
        source.index("function renderAttempts") : source.index(
            "async function loadModelTraces"
        )
    ]
    operations = source[
        source.index("function renderOperations") : source.index(
            "function closeLogStream"
        )
    ]
    program = r"""
const [renderAttemptsSource, renderOperationsSource] = process.argv.slice(1);
const elements = Object.fromEntries(
  ['attemptLogSelect','traceAttemptSelect','attemptRows','modelUsageRows','artifactRows','artifactMeta']
    .map(id => [id, { innerHTML: '', value: '' }])
);
const state = { selectedAttemptId: 'attempt-1', selectedRunId: 'run-a' };
const $ = id => elements[id];
const escapeHtml = value => String(value ?? '');
const formatTime = value => String(value ?? '');
const renderModelUsage = () => {};
eval(renderAttemptsSource);
eval(renderOperationsSource);
const first = {attempt_id:'attempt-1',attempt_no:1,status:'ENDED',start_step:1,end_step:10,started_at:'t1',stop_reason:null,error_message:null,log:{available:true,size_bytes:128}};
const second = {attempt_id:'attempt-2',attempt_no:2,status:'RUNNING',start_step:11,end_step:null,started_at:'t2',stop_reason:null,error_message:null,log:{available:true,size_bytes:64}};
renderAttempts({default_attempt_id:'attempt-2',items:[first]});
if (!elements.attemptRows.innerHTML.includes('data-attempt-id="attempt-1"')) throw new Error('initial Attempt row is not interactive');
renderOperations({model_usage:[],attempts:[first],artifact_jobs:[],artifacts:[]});
if (!elements.attemptRows.innerHTML.includes('data-attempt-id="attempt-1"')) throw new Error('periodic aggregate renderer destroyed Attempt DOM ownership');
renderAttempts({default_attempt_id:'attempt-2',items:[first,second]});
if (!elements.attemptRows.innerHTML.includes('attempt-2')) throw new Error('new Attempt is not visible');
if (state.selectedAttemptId !== 'attempt-1' || elements.attemptLogSelect.value !== 'attempt-1') throw new Error('refresh forced selection away from current Attempt');
"""
    result = subprocess.run(
        [node, "-e", program, attempts, operations],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr or result.stdout
    refresh = source[
        source.index("async function refreshOperationFacts") : source.index(
            "async function loadOperationsWorkspace"
        )
    ]
    assert "/attempts" in refresh and "renderAttempts" in refresh, (
        "periodic sync never discovers a newly created Attempt"
    )


def test_def_057_checkpoint_ui_exposes_full_detail_and_preview_pagination():
    """ROL-CHK-001/002 detail cannot collapse validated facts into three counts."""

    shell = SHELL.read_text(encoding="utf-8")
    script = CONSOLE.read_text(encoding="utf-8")
    detail_start = script.index("async function showCheckpointDetail")
    detail_end = script.index("async function refreshOperationFacts", detail_start)
    detail = script[detail_start:detail_end]
    list_start = script.index("function renderCheckpoints")
    listing = script[list_start:detail_start]

    for token in (
        "item.attempt_id",
        "item.bundle_sha256",
        "item.status",
        "item.validation",
    ):
        assert token in listing, f"checkpoint list does not expose {token}"
    for token in (
        "item.coord",
        "item.action",
        "item.schedule_item_count",
        "detail.conversations.items",
        "detail.storage.groups",
        "detail.files",
        "detail.validation",
    ):
        assert token in detail, f"checkpoint detail does not render {token}"
    assert 'id="checkpointPreview"' in shell
    assert "page.next_cursor" in script[detail_start:], (
        "a checkpoint JSON preview larger than 32 KiB needs a continue/load-more path"
    )
    assert "checkpointGeneration" in detail and "runId !== state.selectedRunId" in detail


def test_def_058_trace_detail_and_operation_collections_are_pageable():
    """ROL-TRACE-001/SYNC-001 rows beyond fixed first pages remain reachable."""

    shell = SHELL.read_text(encoding="utf-8")
    script = CONSOLE.read_text(encoding="utf-8")
    trace_start = script.index("async function loadModelTraces")
    events_start = script.index("function renderSystemEvents", trace_start)
    operation_end = script.index("function simulationStartTime", events_start)
    trace = script[trace_start:events_start]
    operations = script[events_start:operation_end]

    assert 'id="modelTraceDetail"' in shell
    assert "modelTraceRows').addEventListener('click'" in script
    assert "model-traces/${" in script, "trace row click must call the detail route"
    assert "page.next_cursor" in trace and ("loadMore" in trace or "while" in trace)
    assert "next_after_id" in operations and ("loadMore" in operations or "while" in operations)
    assert "runId !== state.selectedRunId" in trace
    assert "attemptId !== state.selectedAttemptId" in trace
    refresh = script[
        script.index("async function refreshOperationFacts") : script.index(
            "async function loadOperationsWorkspace"
        )
    ]
    assert "loadModelTraces" in refresh, "RUNNING trace facts never refresh"


def test_def_058_periodic_event_refresh_does_not_roll_back_a_loaded_tail():
    """ROL-SYNC-001 a first-page refresh cannot erase pages 201+."""

    node = shutil.which("node")
    assert node, "Node.js is required for the executable event merge contract"
    source = CONSOLE.read_text(encoding="utf-8")
    function = source[
        source.index("function renderSystemEvents") : source.index(
            "function renderCheckpoints"
        )
    ]
    program = r"""
const renderSource = process.argv[1];
const state = { operationEvents: [] };
const elements = {eventSearch:{value:''},systemEventRows:{innerHTML:''}};
const $ = id => elements[id];
const escapeHtml = value => String(value ?? '');
const formatTime = value => String(value ?? '');
eval(renderSource);
const rows = Array.from({length:250}, (_,i) => ({id:i+1,event_type:'event',payload:{id:i+1},created_at:'t'}));
renderSystemEvents(rows);
renderSystemEvents(rows.slice(0,200));
if (state.operationEvents.length !== 250 || state.operationEvents.at(-1).id !== 250) throw new Error('periodic first-page refresh rolled back the loaded tail');
"""
    result = subprocess.run(
        [node, "-e", program, function],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_def_058_trace_refresh_rejects_a_response_from_the_previous_attempt():
    """ROL-TRACE-001/SYNC-001 an in-flight Attempt response cannot pollute the new one."""

    node = shutil.which("node")
    assert node, "Node.js is required for the executable trace refresh contract"
    source = CONSOLE.read_text(encoding="utf-8")
    render = source[
        source.index("function renderModelTraces") : source.index(
            "async function loadModelTraces"
        )
    ]
    loader = source[
        source.index("async function loadModelTraces") : source.index(
            "async function loadTraceDetail"
        )
    ]
    program = r"""
const [renderSource, loaderSource] = process.argv.slice(1);
const elements = {
  tracePurposeFilter:{value:''}, modelTraceRows:{innerHTML:''}, loadMoreTraces:{hidden:false},
  modelTraceDetail:{hidden:false}, tracePayloadMore:{hidden:false}
};
const $ = id => elements[id];
const escapeHtml = value => String(value ?? '');
const state = {
  selectedRunId:'run-a', selectedTraceAttemptId:'attempt-1', traceItems:[], traceCursor:0,
  traceEof:false, traceDetailState:null
};
const signal = {aborted:false};
let calls = [];
let queue = [
  {items:[{event_seq:1,trace_id:'t1'}],next_cursor:137,eof:true},
  {items:[{event_seq:2,trace_id:'t2'}],next_cursor:251,eof:true},
  {items:[{event_seq:1,trace_id:'t1'}],next_cursor:137,eof:true},
];
let staleResolve;
const api = async url => {
  calls.push(url);
  if (queue.length) return queue.shift();
  return new Promise(resolve => { staleResolve = resolve; });
};
eval(renderSource);
eval(loaderSource);
(async () => {
  await loadModelTraces('run-a','attempt-1',signal);
  if (!calls[0].includes('cursor=0') || state.traceCursor !== 137) throw new Error('initial EOF did not preserve its byte cursor');
  await loadModelTraces('run-a','attempt-1',signal,{append:true});
  if (!calls[1].includes('cursor=137')) throw new Error('append refresh restarted at byte zero');
  if (state.traceItems.map(item => item.event_seq).join(',') !== '1,2') throw new Error('append refresh duplicated or lost trace facts');
  elements.tracePurposeFilter.value = 'chat';
  await loadModelTraces('run-a','attempt-1',signal);
  if (!calls[2].includes('cursor=0') || state.traceItems.length !== 1) throw new Error('filter change did not reset the trace query');
  const pending = loadModelTraces('run-a','attempt-1',signal,{append:true});
  while (!staleResolve) await Promise.resolve();
  state.selectedTraceAttemptId = 'attempt-2';
  staleResolve({items:[{event_seq:99,trace_id:'stale'}],next_cursor:999,eof:true});
  await pending;
  if (state.traceItems.some(item => item.trace_id === 'stale')) throw new Error('previous Attempt response polluted the selected Attempt');
})().catch(error => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(
        [node, "-e", program, render, loader],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_def_054_replay_player_is_packaged_external_and_not_dom_dot_fallback():
    """ROL-RPL-002 formal replay cannot keep the prototype dot renderer alive."""

    shell = SHELL.read_text(encoding="utf-8")
    console = CONSOLE.read_text(encoding="utf-8")
    assert PLAYER.is_file(), "formal replay-player.js is not packaged"
    assert PHASER.is_file(), "the replay runtime cannot depend on a CDN Phaser build"
    assert shell.count('/static/console/vendor/phaser.min.js') == 1
    assert shell.count('/static/console/replay/replay-player.js') == 1
    assert not [
        body
        for body in __import__("re").findall(r"<script[^>]*>(.*?)</script>", shell, __import__("re").S)
        if body.strip()
    ]
    assert ".map-agent" not in shell
    assert "button.className = 'map-agent'" not in console
    assert "new GAReplayPlayer" in console
    assert "resultMapCanvas" in console


def test_def_064_replay_player_uses_an_explicit_renderer_in_custom_browser_environment():
    """ROL-RPL-002: Phaser.AUTO is invalid in the in-app custom environment."""

    player_source = PLAYER.read_text(encoding="utf-8")
    assert "type: PhaserRuntime.AUTO" not in player_source
    assert (
        "type: PhaserRuntime.CANVAS" in player_source
        or "type: PhaserRuntime.WEBGL" in player_source
    )


def test_def_065_switch_run_reconciles_replay_selection_and_inspector():
    """ROL-RPL-002/SYNC-001: select value and inspector must share one owner."""

    node = shutil.which("node")
    assert node, "Node.js is required for the executable Run-switch UI contract"
    source = CONSOLE.read_text(encoding="utf-8")
    teardown = source[
        source.index("function teardownReplay") : source.index(
            "async function ensureReplayPlayer"
        )
    ]
    ensure = source[
        source.index("async function ensureReplayPlayer") : source.index(
            "function renderReplayStep"
        )
    ]
    clear_inspector = source[
        source.index("function clearReplayInspector") : source.index(
            "function renderOperations"
        )
    ]
    program = r"""
const [teardownSource, ensureSource, clearInspectorSource] = process.argv.slice(1);
const inspectorIds = [
  'replayInspectorLocation','replayInspectorAction','replayInspectorCurrently',
  'replayInspectorConversation','replayInspectorMemories','replayInspectorSchedule'
];
const select = {
  _value:'resident-001', _html:'',
  get value(){ return this._value; }, set value(value){ this._value=String(value); },
  get innerHTML(){ return this._html; },
  set innerHTML(value){ this._html=String(value); this._value=''; }
};
const elements = {
  replayAgentSelect:select, replayTimelineMarkers:{innerHTML:''}, replayStatus:{textContent:''},
  replayCameraMode:{value:'free'}, replayCameraState:{textContent:''}, replayAgentRoster:{innerHTML:''},
  timelinePlay:{textContent:''}, timelineRange:{max:0,min:0,value:0,disabled:false},
  ...Object.fromEntries(inspectorIds.map(id => [id,{textContent:`OLD:${id}`}]))
};
const $ = id => elements[id];
const escapeHtml = value => String(value ?? '');
const state = {
  selectedRunId:'run-new', resultGeneration:2, replayRunId:'run-old',
  replayPlayer:{destroy(){}}, replayAbortController:{abort(){}}, replayPlaying:false,
  replayMarkerFacts:new Map(), selectedReplayAgentKey:'resident-001',
  selectedReplayExperimentId:'revision-1', currentRun:{experiment_id:'revision-1'},
  replayAgentDefinitions:[], agentResults:[]
};
let activePlayer = null;
class GAReplayPlayer {
  static resolveAgentSelection(selectedKey, selectedRevisionId, runRevisionId, agents){
    if (!selectedKey || !selectedRevisionId || selectedRevisionId !== runRevisionId) return null;
    return agents.some(agent => agent.agent_key === selectedKey) ? selectedKey : null;
  }
  constructor(options){ this.options=options; this.availableStep=3; this.currentStep=3; this.selectedAgentKey=null; activePlayer=this; }
  async loadRun(runId){
    this.runId=runId;
    this.manifest={agents: runId === 'run-new'
      ? [{agent_key:'resident-001',display_name:'George'}]
      : [{agent_key:'resident-002',display_name:'Maria'}]};
  }
  async refreshAvailable(){}
  destroy(){}
  selectAgent(key){ this.selectedAgentKey=key || null; }
  followAgent(key){ this.followedAgentKey=key || null; }
}
const renderReplayStep = () => {};
const renderReplayInspector = () => {};
const syncReplayControls = () => {};
eval(clearInspectorSource);
eval(teardownSource);
eval(ensureSource);
(async () => {
  await ensureReplayPlayer('run-new',2);
  if (select.value !== 'resident-001' || activePlayer.selectedAgentKey !== 'resident-001' || activePlayer.followedAgentKey !== 'resident-001') {
    throw new Error('same-definition Agent selection was not restored after switching Run');
  }
  select.value='resident-999';
  state.selectedReplayAgentKey='resident-999';
  state.selectedReplayExperimentId='revision-1';
  inspectorIds.forEach(id => { elements[id].textContent=`STALE:${id}`; });
  state.selectedRunId='run-other'; state.resultGeneration=3;
  await ensureReplayPlayer('run-other',3);
  if (select.value !== '' || activePlayer.selectedAgentKey !== null || activePlayer.followedAgentKey !== null) {
    throw new Error('missing Agent remained selected in the new Run');
  }
  if (inspectorIds.some(id => elements[id].textContent.startsWith('STALE:'))) {
    throw new Error('new Run kept an Inspector fact owned by the previous Run/Agent');
  }
})().catch(error => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(
        [node, "-e", program, teardown, ensure, clear_inspector],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_def_067_switch_run_keeps_exactly_one_externally_owned_replay_canvas():
    """ROL-RPL-002/SYNC-001: Phaser teardown cannot remove the shell canvas."""

    shell = SHELL.read_text(encoding="utf-8")
    assert shell.count('id="resultMapCanvas"') == 1
    node = shutil.which("node")
    assert node, "Node.js is required for the executable canvas lifecycle contract"
    program = r"""
global.window = global;
global.Phaser = {};
const imported = require(process.argv[1]);
const Player = imported.GAReplayPlayer || global.GAReplayPlayer;
const host = {children:[]};
const canvas = {
  id:'resultMapCanvas', parentElement:host,
  remove(){ const index=host.children.indexOf(this); if(index >= 0) host.children.splice(index,1); this.parentElement=null; }
};
host.children.push(canvas);
const player = new Player({canvas});
player.runId='run-a';
player.game={destroy(removeCanvas){ if(removeCanvas) canvas.remove(); }};
player.destroy();
const owned = host.children.filter(item => item.id === 'resultMapCanvas');
if (owned.length !== 1 || owned[0] !== canvas || canvas.parentElement !== host) {
  throw new Error('switching Run destroyed or duplicated the shell-owned replay canvas');
}
"""
    result = subprocess.run(
        [node, "-e", program, str(PLAYER)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr or result.stdout
    player_source = PLAYER.read_text(encoding="utf-8")
    assert "destroy(true" not in player_source
    assert "canvas: player.canvas" in player_source and "parent: host" in player_source


def test_def_054_simulation_replay_exposes_the_full_control_contract():
    """ROL-RPL-002 the formal player UX is controllable and fact-backed."""

    shell = SHELL.read_text(encoding="utf-8")
    required_controls = {
        "timelinePrev",
        "timelinePlay",
        "timelineNext",
        "timelineRange",
        "timelineTime",
        "replaySpeed",
        "replayCameraMode",
        "replayAgentSelect",
        "replayAgentRoster",
        "replayCameraState",
        "replayLayerTrails",
        "replayLayerKeyEvents",
        "replayTimelineMarkers",
        "replayInspector",
        "replayInspectorLocation",
        "replayInspectorAction",
        "replayInspectorCurrently",
        "replayInspectorConversation",
        "replayInspectorMemories",
        "replayInspectorSchedule",
    }
    missing = [name for name in sorted(required_controls) if f'id="{name}"' not in shell]
    assert not missing, f"time explorer controls are unreachable: {missing}"
    replay = shell[shell.index('data-result-panel="timeline"') : shell.index('data-result-panel="agents"')]
    assert 'data-result-tab="timeline">仿真回放</button>' in shell
    assert replay.index('id="resultMap"') < replay.index('id="timelineRange"') < replay.index('id="replayInspector"') < replay.index('id="timelineStreamItems"') < replay.index('id="replayAgentRoster"')
    assert 'class="timeline-toolbar replay-sidebar-controls"' in replay
    assert 'class="replay-sidebar-events"' in replay
    assert 'id="replayLayerAgentNames"' not in replay
    assert 'id="replayLayerActionBubbles"' not in replay
    assert 'id="replayLayerConversations"' not in replay

    assert PLAYER.is_file(), "formal replay-player.js is not packaged"
    player = PLAYER.read_text(encoding="utf-8")
    required_player_contract = {
        "GAReplayPlayer",
        "loadRun",
        "destroy",
        "play",
        "pause",
        "stepBy",
        "setSpeed",
        "followAgent",
        "selectAgent",
        "setLayerVisibility",
        "attempt_boundary",
        "checkpoint",
        "conversations",
        "domain_events",
        "memory_deltas",
        "schedule_revisions",
        "AbortController",
    }
    absent = [token for token in sorted(required_player_contract) if token not in player]
    assert not absent, f"formal replay player omits required semantics: {absent}"
    assert "/replay/steps" in player


def test_def_054_switching_run_destroys_the_old_player_and_aborts_old_windows():
    """ROL-RPL-002/SYNC-001 Run switching has an explicit teardown boundary."""

    script = CONSOLE.read_text(encoding="utf-8")
    teardown_start = script.index("function teardownReplay")
    teardown_end = script.index("async function ensureReplayPlayer", teardown_start)
    teardown = script[teardown_start:teardown_end]
    assert "state.replayPlayer?.destroy()" in teardown
    assert "state.replayAbortController?.abort()" in teardown
    load_start = script.index("async function loadResults")
    load_end = script.index("function applyRunActivity", load_start)
    assert "teardownReplay()" in script[load_start:load_end]
    assert "runId !== state.selectedRunId" in script


def test_def_054_replay_player_has_an_executable_external_module_contract():
    """ROL-RPL-002 control wiring is executable without an inline prototype."""

    assert PLAYER.is_file(), "formal replay-player.js is not packaged"
    node = shutil.which("node")
    assert node, "Node.js is required for the executable replay module contract"
    program = r"""
global.window = global;
global.Phaser = {};
const imported = require(process.argv[1]);
const Player = imported.GAReplayPlayer || imported.default || imported || global.GAReplayPlayer;
if (typeof Player !== 'function') throw new Error('GAReplayPlayer is not externally constructible');
const required = ['loadRun','destroy','play','pause','stepBy','setSpeed','followAgent','selectAgent','setLayerVisibility'];
for (const name of required) {
  if (typeof Player.prototype[name] !== 'function') throw new Error(`missing player method ${name}`);
}
"""
    result = subprocess.run(
        [node, "-e", program, str(PLAYER)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_def_055_log_byte_window_never_reads_the_entire_file(
    monkeypatch, tmp_path: Path
):
    """ROL-LOG-001 paging must be a physical I/O window, not a response-only slice."""

    path = tmp_path / "large-attempt.log"
    path.write_bytes(("INFO bounded window\n" * 200_000).encode("utf-8"))
    real_open = Path.open
    limit = 4096
    read_sizes: list[int] = []

    class GuardedReader:
        """为 ``GuardedReader`` 相关场景组织共享测试状态、输入或断言。"""
        def __init__(self, handle):
            """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
            self._handle = handle

        def __enter__(self):
            """为本测试模块封装 ``__enter__`` 辅助步骤，减少重复的场景搭建代码。"""
            self._handle.__enter__()
            return self

        def __exit__(self, *args):
            """为本测试模块封装 ``__exit__`` 辅助步骤，减少重复的场景搭建代码。"""
            return self._handle.__exit__(*args)

        def __getattr__(self, name):
            """为本测试模块封装 ``__getattr__`` 辅助步骤，减少重复的场景搭建代码。"""
            return getattr(self._handle, name)

        def read(self, size=-1):
            """为本测试模块封装 ``read`` 辅助步骤，减少重复的场景搭建代码。"""
            assert 0 <= size <= limit + 4, (
                "byte-window reader attempted an unbounded or oversized physical read"
            )
            read_sizes.append(size)
            return self._handle.read(size)

    def guarded_open(target, *args, **kwargs):
        """为本测试模块封装 ``guarded_open`` 辅助步骤，减少重复的场景搭建代码。"""
        handle = real_open(target, *args, **kwargs)
        return GuardedReader(handle) if target == path else handle

    monkeypatch.setattr(Path, "open", guarded_open)
    window = read_utf8_window(path, cursor=1_000_000, limit_bytes=limit)
    assert window.content
    assert window.next_cursor - window.start_cursor <= limit
    assert read_sizes and sum(read_sizes) <= limit + 4


def test_def_055_tail_limit_inside_utf8_returns_the_complete_final_character(
    tmp_path: Path,
):
    """ROL-LOG-001 tail aligns backward, never skips a partial final code point."""

    path = tmp_path / "unicode-tail.log"
    path.write_text("prefix🙂", encoding="utf-8")
    window = read_utf8_window(path, cursor=0, limit_bytes=2, tail=True)
    assert window.content == "🙂"
    assert window.next_cursor == path.stat().st_size
    assert window.eof is True
