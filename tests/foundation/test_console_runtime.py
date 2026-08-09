from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from generative_agents.web import create_app


def _png_size(payload: bytes) -> tuple[int, int]:
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    assert payload[12:16] == b"IHDR"
    return struct.unpack(">II", payload[16:24])


def test_console_shell_and_api_script_form_one_self_contained_runtime(database_url):
    """The packaged shell must satisfy every eagerly-bound production DOM lookup."""

    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/experiments",
            json={
                "name": "Dynamic console experiment",
                "goal": "Exercise the production list-to-detail path",
                "source": {"type": "BUILTIN_DEFAULT"},
            },
        )
        assert created.status_code == 201
        shell = client.get("/").text
        script_response = client.get("/static/console/console-api.js")
        focus_script_response = client.get("/static/console/modal-focus.js")
        listing = client.get("/api/v1/experiments").json()

    assert script_response.status_code == 200
    assert focus_script_response.status_code == 200
    script = script_response.text
    assert listing["items"][0]["id"] == created.json()["id"]
    assert shell.count('/static/console/console-api.js') == 1
    assert shell.count('/static/console/modal-focus.js') == 1
    assert not [body for body in re.findall(r"<script[^>]*>(.*?)</script>", shell, re.S) if body.strip()]

    shell_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', shell))
    eager_lookups = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", script))
    assert eager_lookups <= shell_ids, (
        "console-api.js binds an element removed from the neutral production shell: "
        f"{sorted(eager_lookups - shell_ids)}"
    )


def test_agent_result_page_is_agent_owned_and_switches_structured_outputs_by_tab():
    root = Path(__file__).parents[2]
    shell = (root / "generative_agents" / "web" / "static" / "experiment-console.html").read_text(
        encoding="utf-8"
    )
    script = (root / "generative_agents" / "web" / "static" / "console-api.js").read_text(
        encoding="utf-8"
    )

    assert 'data-result-tab="agents">Agent</button>' in shell
    assert 'data-result-tab="conversations"' not in shell
    assert 'data-result-tab="memories"' not in shell
    assert 'data-result-tab="operations">运行诊断</button>' in shell
    assert 'data-result-tab="artifacts">结果与导出</button>' in shell
    assert 'class="agent-result-list" id="resultAgentButtons"' in shell
    assert 'id="resultAgentButtons" role="tablist"' in shell
    assert 'id="resultAgentDetail" role="tabpanel"' in shell
    assert 'id="agentTabPrev"' in shell and 'id="agentTabNext"' in shell
    assert 'data-tooltip="每个 Agent 是一个独立结果单元；切换上方 Tab' in shell
    assert '.agent-result-card' not in shell
    assert "function renderAgentTabs()" in script
    assert 'role="tab" class="agent-result-tab' in script
    assert "['ArrowLeft', 'ArrowRight', 'Home', 'End']" in script
    assert "String(a.display_name || a.agent_key).localeCompare" in script
    assert "b.updated_step - a.updated_step" not in script
    assert "function renderAgentPlanSection" in script
    assert "function renderAgentEventSection" in script
    assert "function renderAgentActionSection" in script
    assert "function renderAgentConversationSection" in script
    assert "function renderAgentMemorySection" in script
    assert "function renderAgentStateSection" in script
    assert "decision_context" in script
    assert "Agent 轨迹" not in shell


def test_dynamic_card_and_error_paths_are_owned_by_the_production_script():
    script = (
        Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "console-api.js"
    ).read_text(encoding="utf-8")

    assert "function showToast(message, title" in script
    assert "function reportError(error)" in script
    assert "showToast(error.message || String(error), '操作失败')" in script
    assert "openExperiment(card.dataset.id).catch(reportError)" in script
    assert "state.currentExperimentName = experiment.name" in script
    assert "state.currentExperimentStatus = statusLabels[experiment.status]" in script


def test_modal_focus_runtime_traps_both_tab_directions_and_restores_focus():
    node = shutil.which("node")
    assert node, "Node.js is required for the executable production JS contract"
    root = Path(__file__).parents[2]
    focus_module = root / "generative_agents" / "web" / "static" / "modal-focus.js"
    program = r"""
const { tabTarget } = require(process.argv[1]);
const first = { id: 'first' };
const middle = { id: 'middle' };
const last = { id: 'last' };
const focusables = [first, middle, last];
const outcomes = {
  forwardWrap: tabTarget(focusables, last, false)?.id,
  backwardWrap: tabTarget(focusables, first, true)?.id,
  outsideForward: tabTarget(focusables, {}, false)?.id,
  outsideBackward: tabTarget(focusables, {}, true)?.id,
  middleForward: tabTarget(focusables, middle, false)?.id,
  middleBackward: tabTarget(focusables, middle, true)?.id,
};
if (JSON.stringify(outcomes) !== JSON.stringify({
  forwardWrap: 'first', backwardWrap: 'last', outsideForward: 'first',
  outsideBackward: 'last', middleForward: 'last', middleBackward: 'first',
})) process.exit(1);
"""
    subprocess.run(
        [node, "-e", program, str(focus_module)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    console = (root / "generative_agents" / "web" / "static" / "console-api.js").read_text(
        encoding="utf-8"
    )
    assert "shell.inert = inert" in console
    assert "openModal('agentEditorModal', agentEditorInitialFocus.id, agentEditorReturnFocus)" in console
    assert "closeModal('agentEditorModal')" in console
    assert "modalFocus.tabTarget(focusables, document.activeElement, event.shiftKey)" in console


def test_console_owns_global_activity_reconciliation_and_resume_hooks():
    source = (
        Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "console-api.js"
    ).read_text(encoding="utf-8")

    assert "async function startGlobalActivityStream()" in source
    assert "new EventSource(`/api/v1/events/stream?after_id=" in source
    assert "source.addEventListener('activity'" in source
    assert "source.addEventListener('sync'" in source
    assert "scheduleGlobalReconcile({ experimentId: activity.experiment_id })" in source
    assert "scheduleGlobalReconcile({ full: true })" in source
    assert "document.addEventListener('visibilitychange'" in source
    assert "window.addEventListener('focus', reconcileAfterPageResume)" in source
    assert "window.addEventListener('online', reconcileAfterPageResume)" in source
    assert "window.addEventListener('pageshow', reconcileAfterPageResume)" in source


def test_selected_result_run_is_not_confused_with_the_experiment_latest_run():
    source = (
        Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "console-api.js"
    ).read_text(encoding="utf-8")
    load_results = source[
        source.index("async function loadResults") : source.index(
            "function scheduleResultRefresh"
        )
    ]

    assert "state.selectedRunId = runId" in load_results
    assert "state.latestRunId = runId" not in load_results
    assert "runId !== state.selectedRunId" in source
    assert "state.latestRunId = experiment.latest_run?.id || null" in source
    assert "refreshRunHistoryList(state.selectedExperimentId, state.selectedRunId)" in source


def test_console_url_tracks_the_selected_experiment_workspace_and_run():
    source = (
        Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "console-api.js"
    ).read_text(encoding="utf-8")

    route = source[source.index("function workspaceUrl") : source.index("function markDirty")]
    bootstrap = source[source.index("async function bootstrapConsole") :]

    assert "url.searchParams.set('experiment_id', state.selectedExperimentId)" in route
    assert "url.searchParams.set('view', pageName)" in route
    assert "pageName === 'results' && state.selectedRunId" in route
    assert "url.searchParams.set('run_id', state.selectedRunId)" in route
    assert "if (pageName !== 'experiments'" in route
    assert "history[push ? 'pushState' : 'replaceState'](null, '', nextUrl)" in route
    assert "syncWorkspaceUrl();" in route
    assert "openExperiment(id, targetPage = 'overview', preferredRunId = null)" in source
    assert "preferredRunId || state.latestRunId" in source
    assert "requestedView !== 'experiments' && $(`page-${requestedView}`)" in bootstrap
    assert "openExperiment(experimentId, targetPage, params.get('run_id'))" in bootstrap


def test_content_workspaces_use_tabs_while_metric_strips_remain_visible():
    root = Path(__file__).parents[2]
    shell = (
        root
        / "generative_agents"
        / "web"
        / "static"
        / "experiment-console.html"
    ).read_text(encoding="utf-8")
    script = (
        root / "generative_agents" / "web" / "static" / "console-api.js"
    ).read_text(encoding="utf-8")

    for group in ("overview", "models", "summary", "world", "advanced", "agent-editor"):
        assert f'data-content-tabs="{group}"' in shell
    assert 'class="stats"' in shell
    assert 'class="result-metrics"' in shell
    assert "function setContentTab" in script
    assert "url.searchParams.set('tab'," in script
    assert "window.addEventListener('popstate'" in script
    assert "Apply deep-link state before loading the experiment" in script
    assert script.index("state.selectedAgentContent = requestedTab") < script.index(
        "await openExperiment(experimentId"
    )


def test_running_duration_uses_utc_instants_and_a_live_execution_label():
    root = Path(__file__).parents[2]
    shell = (
        root / "generative_agents" / "web" / "static" / "experiment-console.html"
    ).read_text(encoding="utf-8")
    script_path = root / "generative_agents" / "web" / "static" / "console-api.js"
    script = script_path.read_text(encoding="utf-8")

    assert 'id="resultDurationLabel">执行时间' in shell
    assert "terminal ? '实际耗时' : '执行时间'" in script
    assert "function startResultDurationTimer(run)" in script
    assert "`${text}Z`" in script

    node = shutil.which("node")
    assert node, "Node.js is required for the duration timezone contract"
    program = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('function parseApiInstant');
const end = source.indexOf('function clearResultDurationTimer');
eval(source.slice(start, end));
Date.now = () => Date.parse('2026-08-09T06:09:27Z');
if (formatDuration('2026-08-09T05:20:11', null) !== '49m 16s') process.exit(1);
if (formatDuration('2026-08-09T05:20:11+00:00', '2026-08-09T06:09:27+00:00') !== '49m 16s') process.exit(2);
"""
    subprocess.run([node, "-e", program, str(script_path)], check=True)


def test_agent_results_use_content_tabs_instead_of_an_all_sections_waterfall():
    source = (
        Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "console-api.js"
    ).read_text(encoding="utf-8")
    detail = source[
        source.index("function renderAgentDetail") : source.index("function agentPlanText")
    ]

    assert 'role="tablist"' in detail
    assert 'role="tabpanel"' in detail
    assert "agentContentChip('all'" not in detail
    assert "state.selectedAgentContent" in detail


def test_recoverable_run_action_is_visible_before_rerun_and_uses_resume():
    node = shutil.which("node")
    assert node, "Node.js is required for the executable Run action contract"
    root = Path(__file__).parents[2]
    shell_path = root / "generative_agents" / "web" / "static" / "experiment-console.html"
    script_path = root / "generative_agents" / "web" / "static" / "console-api.js"
    shell = shell_path.read_text(encoding="utf-8")
    source = script_path.read_text(encoding="utf-8")

    assert shell.index('id="runContinueBtn"') < shell.index('id="runAgainBtn"')
    assert 'id="resumeRunModal"' in shell
    assert 'id="resumeRunStep"' in shell
    assert 'id="resumeRunNextStep"' in shell
    assert "function openResumeRunModal()" in source
    assert "['PAUSED', 'FAILED', 'INTERRUPTED'].includes(run.status)" in source
    assert "controlRun('resume')" in source
    assert "state.pendingResumeRunId !== state.selectedRunId" in source

    program = r"""
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('function isRunRecoverable(');
const end = source.indexOf('function renderAgents(', start);
const values = new Set(['btn-primary']);
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {
    id, hidden: true, textContent: '',
    classList: { toggle(name, force) { if (force) values.add(name); else values.delete(name); } },
  });
  return elements.get(id);
};
const $ = element;
eval(source.slice(start, end));
const snapshot = run => {
  renderRunActions(run);
  return {
    pauseHidden: element('runPauseResumeBtn').hidden,
    cancelHidden: element('runCancelBtn').hidden,
    continueHidden: element('runContinueBtn').hidden,
    continueText: element('runContinueBtn').textContent,
    againHidden: element('runAgainBtn').hidden,
    replayPrimary: values.has('btn-primary'),
  };
};
const failed = snapshot({status:'FAILED', recoverable:true, recoverable_step:30});
const unavailable = snapshot({status:'FAILED', recoverable:false, recoverable_step:0});
const paused = snapshot({status:'PAUSED', recoverable:true, recoverable_step:7});
const running = snapshot({status:'RUNNING', recoverable:false, recoverable_step:7});
if (JSON.stringify(failed) !== JSON.stringify({
  pauseHidden:true, cancelHidden:true, continueHidden:false,
  continueText:'继续执行 · Step 30', againHidden:false, replayPrimary:false,
})) process.exit(1);
if (!unavailable.continueHidden || unavailable.againHidden || !unavailable.replayPrimary) process.exit(2);
if (paused.continueHidden || paused.continueText !== '继续执行 · Step 7' || paused.cancelHidden) process.exit(3);
if (running.pauseHidden || running.cancelHidden || !running.continueHidden || !running.againHidden) process.exit(4);
"""
    subprocess.run(
        [node, "-e", program, str(script_path)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_console_reconciles_publish_actions_and_renders_artifact_job_states():
    source = (
        Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "console-api.js"
    ).read_text(encoding="utf-8")

    publish = source[
        source.index("async function publishAndRun") : source.index(
            "async function createResultBundle"
        )
    ]
    assert "syncSelectedExperiment({ refreshDefinition: true" in publish
    assert "state.selectedRunId = run.run_id" in publish
    assert "/draft/validate" not in publish
    assert "/actions/publish-and-run" in publish
    assert "operations.artifact_jobs" in source
    assert "artifact_queued" in source
    assert "artifact_running" in source
    assert "result_rewound" in source


def test_chat_output_limit_is_not_presented_as_the_model_context_window():
    root = Path(__file__).parents[2]
    shell = (
        root
        / "generative_agents"
        / "web"
        / "static"
        / "experiment-console.html"
    ).read_text(encoding="utf-8")
    script = (
        root / "generative_agents" / "web" / "static" / "console-api.js"
    ).read_text(encoding="utf-8")

    assert "单次最大输出" in shell
    assert "不是模型的上下文窗口" in shell
    assert 'id="chatServiceCapability"' in shell
    assert "result.service?.context_window" in script
    assert "chat.context_window" in script


def test_replay_player_uses_an_explicit_canvas_renderer_for_custom_browsers():
    source = (
        Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "replay-player.js"
    ).read_text(encoding="utf-8")

    assert "type: PhaserRuntime.CANVAS" in source
    assert "type: PhaserRuntime.AUTO" not in source


def test_replay_agent_selection_is_revision_owned_and_executable():
    node = shutil.which("node")
    assert node, "Node.js is required for the replay selection contract"
    root = Path(__file__).parents[2]
    player = root / "generative_agents" / "web" / "static" / "replay-player.js"
    program = r"""
global.window = global;
global.Phaser = {};
const { GAReplayPlayer } = require(process.argv[1]);
const agents = [{agent_key:'resident-001'}, {agent_key:'resident-002'}];
const outcomes = {
  sameRevision: GAReplayPlayer.resolveAgentSelection('resident-001', 'rev-a', 'rev-a', agents),
  otherRevision: GAReplayPlayer.resolveAgentSelection('resident-001', 'rev-a', 'rev-b', agents),
  removedAgent: GAReplayPlayer.resolveAgentSelection('resident-999', 'rev-a', 'rev-a', agents),
};
if (JSON.stringify(outcomes) !== JSON.stringify({sameRevision:'resident-001',otherRevision:null,removedAgent:null})) process.exit(1);
"""
    subprocess.run(
        [node, "-e", program, str(player)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    console = (root / "generative_agents" / "web" / "static" / "console-api.js").read_text(
        encoding="utf-8"
    )
    assert "clearReplayInspector();" in console
    assert "selectedReplayRevisionId" in console
    assert "replayPlayer.selectAgent(restoredAgentKey)" in console


def test_replay_phaser_canvas_stays_owned_by_the_result_map_container():
    node = shutil.which("node")
    assert node, "Node.js is required for the replay canvas ownership contract"
    root = Path(__file__).parents[2]
    player = root / "generative_agents" / "web" / "static" / "replay-player.js"
    program = r"""
global.window = global;
global.ResizeObserver = undefined;
let captured;
const layer = {setDepth(){return this},setVisible(){return this}};
const scene = {
  make:{tilemap(){return {widthInPixels:3200,heightInPixels:2400,addTilesetImage(name){return name},createLayer(){return layer}}}},
  cameras:{main:{setBounds(){},setZoom(){}}},
  input:{on(){}},
  scale:{resize(){}},
};
global.Phaser = {
  CANVAS:'CANVAS', Scale:{RESIZE:'RESIZE'}, Math:{Clamp:value=>value},
  Game:function(config){
    captured = config;
    this.events = {once(){}};
    config.scene.create.call(scene);
  },
};
const { GAReplayPlayer } = require(process.argv[1]);
const host = {id:'resultMap',clientWidth:900,clientHeight:520};
const canvas = {id:'resultMapCanvas',parentElement:host};
const instance = new GAReplayPlayer({canvas});
instance.runId = 'run-1'; instance.generation = 1; instance.abortController = {signal:{aborted:false}};
const manifest = {run_id:'run-1',world:{render_asset:{status:'READY',base_url:'/tilemap',tilemap_url:'/tilemap.json'}},agents:[]};
instance._createGame(manifest, 1).then(() => {
  if (captured.parent !== host) throw new Error('root Phaser parent is not resultMap');
  if (captured.canvas !== canvas) throw new Error('existing resultMapCanvas was replaced');
  if (Object.hasOwn(captured.scale, 'parent')) throw new Error('parent was incorrectly nested under scale');
  if (!String(process.argv[1])) process.exit(1);
}).catch(error => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        [node, "-e", program, str(player)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    source = player.read_text(encoding="utf-8")
    assert "'Interior Furniture L2 '" in source
    assert "'Interior Furniture L2'," not in source
    assert "new ResizeObserver" in source


def test_replay_canvas_survives_running_completed_running_switches():
    node = shutil.which("node")
    assert node, "Node.js is required for the replay lifecycle contract"
    root = Path(__file__).parents[2]
    player = root / "generative_agents" / "web" / "static" / "replay-player.js"
    program = r"""
global.window = global;
global.ResizeObserver = undefined;
const host = {id:'resultMap',children:[]};
const canvas = {id:'resultMapCanvas',parentElement:host};
host.children.push(canvas);
const configs = [];
const destroyArgs = [];
const chain = {
  setDepth(){return this}, setVisible(){return this}, setInteractive(){return this},
  setOrigin(){return this}, on(){return this}, setTint(){return this}, clearTint(){return this},
};
const layer = {setDepth(){return this},setVisible(){return this}};
function sceneFor(config) {
  return {
    make:{tilemap(){return {widthInPixels:3200,heightInPixels:2400,addTilesetImage(name){return name},createLayer(){return layer}}}},
    cameras:{main:{setBounds(){},setZoom(){},startFollow(){},stopFollow(){}}},
    input:{on(){}}, scale:{resize(){}},
    add:{sprite(){return Object.create(chain)},rectangle(){return Object.create(chain)},text(){return Object.create(chain)},graphics(){return Object.create(chain)}},
  };
}
global.Phaser = {
  CANVAS:'CANVAS', Scale:{RESIZE:'RESIZE'}, Math:{Clamp:value=>value},
  Game:function(config){
    configs.push(config);
    this.events={once(){}};
    this.destroy=(removeCanvas,noReturn)=>{
      destroyArgs.push([removeCanvas,noReturn]);
      if (removeCanvas) { canvas.parentElement=null; host.children=[]; }
    };
    config.scene.create.call(sceneFor(config));
  },
};
const {GAReplayPlayer}=require(process.argv[1]);
const agents=[{agent_key:'resident-001',display_name:'乔治',initial_coord:[1,1],sprite_asset:{status:'READY'}}];
const manifest=runId=>({
  schema_version:2,run_id:runId,revision_id:'revision-same',available_step:0,partial:runId!=='completed',
  world:{render_asset:{status:'READY',base_url:'/tilemap',tilemap_url:'/tilemap.json'}},agents,
});
const fetchImpl=async url=>({ok:true,json:async()=>manifest(decodeURIComponent(url.split('/')[4]))});
const errors=[];
async function open(runId,selectedKey,selectedRevision) {
  const instance=new GAReplayPlayer({canvas,fetchImpl,onError:error=>errors.push(error)});
  await instance.loadRun(runId);
  const restored=GAReplayPlayer.resolveAgentSelection(selectedKey,selectedRevision,'revision-same',instance.manifest.agents);
  instance.selectAgent(restored);
  if (instance.selectedAgentKey!=='resident-001') throw new Error('same-revision selection was not restored');
  return instance;
}
(async()=>{
  let instance=await open('running-a','resident-001','revision-same');
  instance.destroy();
  instance=await open('completed','resident-001','revision-same');
  instance.destroy();
  instance=await open('running-b','resident-001','revision-same');
  if (canvas.parentElement!==host || host.children.length!==1 || host.children[0]!==canvas) throw new Error('owned canvas was detached or duplicated');
  if (configs.length!==3 || configs.some(config=>config.canvas!==canvas || config.parent!==host)) throw new Error('Run switch did not reuse the owned canvas');
  if (destroyArgs.some(args=>args[0]!==false)) throw new Error('Phaser was allowed to remove the shell canvas');
  if (errors.length) throw new Error(`unexpected console errors: ${JSON.stringify(errors)}`);
})().catch(error=>{console.error(error);process.exit(1)});
"""
    subprocess.run(
        [node, "-e", program, str(player)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def test_replay_uses_a_packaged_tile_aligned_texture_without_changing_legacy_source(
    database_url,
):
    root = Path(__file__).parents[2]
    legacy_path = (
        root
        / "generative_agents"
        / "frontend"
        / "static"
        / "assets"
        / "village"
        / "tilemap"
        / "interiors_pt3.png"
    )
    normalized_path = (
        root
        / "generative_agents"
        / "web"
        / "static"
        / "replay-assets"
        / "interiors_pt3.png"
    )
    legacy = legacy_path.read_bytes()
    normalized = normalized_path.read_bytes()

    assert _png_size(legacy) == (512, 10032)
    assert hashlib.sha256(legacy).hexdigest() == (
        "93d523ee6297d54cedba5cec4a2518855c06a68f7084dd259c8eec2769294c0d"
    )
    assert _png_size(normalized) == (512, 10016)
    assert _png_size(normalized)[1] % 32 == 0
    assert hashlib.sha256(normalized).hexdigest() == (
        "2d7eab019f428df91dfe8a5861575b7fe15196c1832f2921872de0cd7cc17952"
    )

    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        response = client.get("/static/console/replay-assets/interiors_pt3.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == normalized

    replay_contract = (root / "generative_agents" / "runtime" / "replay_v2.py").read_text(
        encoding="utf-8"
    )
    player = (root / "generative_agents" / "web" / "static" / "replay-player.js").read_text(
        encoding="utf-8"
    )
    assert '"interiors_pt3": {' in replay_contract
    assert '"texture_overrides"' in replay_contract
    assert "assets.texture_overrides?.[name]" in player
    assert "textureUrl || `${tileRoot}/${name}.png`" in player


def test_replay_uses_a_packaged_tilemap_with_only_the_invalid_imageheight_corrected(
    database_url,
):
    root = Path(__file__).parents[2]
    legacy_path = (
        root
        / "generative_agents"
        / "frontend"
        / "static"
        / "assets"
        / "village"
        / "tilemap"
        / "tilemap.json"
    )
    normalized_path = (
        root
        / "generative_agents"
        / "web"
        / "static"
        / "replay-assets"
        / "tilemap.json"
    )
    legacy_bytes = legacy_path.read_bytes()
    normalized_bytes = normalized_path.read_bytes()
    legacy = json.loads(legacy_bytes)
    normalized = json.loads(normalized_bytes)

    assert hashlib.sha256(legacy_bytes).hexdigest() == (
        "8c15aa6f46ebaf43aec6cf3244860e8161e9d8f7541d1765f907a496686a9bfc"
    )
    assert hashlib.sha256(normalized_bytes).hexdigest() == (
        "53477dc3e5eed02798967fbe032774bf73abe96316a7aeb93397b932e1d3259b"
    )
    legacy_tileset = legacy["tilesets"][12]
    normalized_tileset = normalized["tilesets"][12]
    assert legacy_tileset["name"] == normalized_tileset["name"] == "interiors_pt3"
    assert legacy_tileset["imageheight"] == 10032
    assert normalized_tileset["imageheight"] == 10016
    assert normalized_tileset["tilecount"] == 5008
    assert normalized_tileset["columns"] == 16
    assert normalized_tileset["tilecount"] // normalized_tileset["columns"] == 313

    restored = json.loads(normalized_bytes)
    restored["tilesets"][12]["imageheight"] = 10032
    assert restored == legacy, "Replay tilemap changed outside the controlled imageheight fix"

    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        response = client.get("/static/console/replay-assets/tilemap.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.content == normalized_bytes

    replay_contract = (root / "generative_agents" / "runtime" / "replay_v2.py").read_text(
        encoding="utf-8"
    )
    assert '"tilemap_url": _NORMALIZED_TILEMAP_URL' in replay_contract
    assert '"tilemap_asset": {' in replay_contract
    assert '"normalization": "INTERIORS_PT3_IMAGEHEIGHT_10016"' in replay_contract
