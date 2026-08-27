"""基础能力回归测试：覆盖 ``test_console_runtime`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from generative_agents.web import create_app


def _png_size(payload: bytes) -> tuple[int, int]:
    """为本测试模块封装 ``_png_size`` 辅助步骤，减少重复的场景搭建代码。"""
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
        skill_script_response = client.get("/static/console/skill-workspace.js")
        skill_style_response = client.get("/static/console/skill-workspace.css")
        focus_script_response = client.get("/static/console/modal-focus.js")
        ux_style_response = client.get("/static/console/console-ux.css")
        listing = client.get("/api/v1/experiments").json()

    assert script_response.status_code == 200
    assert skill_script_response.status_code == 200
    assert skill_script_response.headers["cache-control"] == (
        "no-cache, must-revalidate"
    )
    assert skill_style_response.status_code == 200
    assert focus_script_response.status_code == 200
    assert ux_style_response.status_code == 200
    script = script_response.text
    ux_style = ux_style_response.text
    assert listing["items"][0]["id"] == created.json()["id"]
    assert shell.count('/static/console/console-api.js') == 1
    assert '/static/console/skill-workspace.js?v=' in shell
    assert '/static/console/skill-workspace.css?v=' in shell
    assert shell.count('/static/console/modal-focus.js') == 1
    assert shell.count('/static/console/console-ux.css') == 1
    assert "--sidebar-width: 216px" in ux_style
    assert "--text-control: 13px" in ux_style
    assert "max-width: none" in ux_style
    assert "grid-template-columns: 200px minmax(480px, 1fr) 300px" in ux_style
    assert 'id="sidebarToggle"' in shell
    assert '<span class="nav-text">实验</span>' in shell
    assert "pageSize: 5" in script
    assert "sidebar-collapsed" in ux_style
    assert not [body for body in re.findall(r"<script[^>]*>(.*?)</script>", shell, re.S) if body.strip()]

    shell_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', shell))
    eager_lookups = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", script))
    assert eager_lookups <= shell_ids, (
        "console-api.js binds an element removed from the neutral production shell: "
        f"{sorted(eager_lookups - shell_ids)}"
    )


def test_map_editor_identity_and_publish_actions_share_the_global_topbar():
    """回归验证 ``test_map_editor_identity_and_publish_actions_share_the_global_topbar`` 所描述的业务结果、故障边界和隔离约束。"""
    root = Path(__file__).resolve().parents[2]
    static = root / "generative_agents" / "web" / "static"
    shell = (static / "experiment-console.html").read_text(encoding="utf-8")
    script = (static / "console-api.js").read_text(encoding="utf-8")

    topbar = shell[shell.index('<header class="topbar">') : shell.index("</header>")]
    editor_shell_start = shell.index(
        '<div class="map-editor-shell" id="mapEditorShell"'
    )
    editor_tabs_start = shell.index('<nav class="map-editor-tabs"', editor_shell_start)
    editor_shell_lead = shell[editor_shell_start:editor_tabs_start]

    for element_id in (
        "mapEditorTopbarContext",
        "backToMapsBtn",
        "mapEditorTitle",
        "mapEditorMeta",
        "mapEditorState",
        "mapAutosaveStatus",
        "mapEditorActions",
        "saveMapBtn",
        "publishMapBtn",
    ):
        assert f'id="{element_id}"' in topbar
        assert f'id="{element_id}"' not in editor_shell_lead
    assert "syncMapEditorTopbar" in script
    assert "document.body.classList.toggle('map-editor-mode', active)" in script


def test_crowd_editor_identity_and_publish_actions_share_the_global_topbar():
    """回归验证 ``test_crowd_editor_identity_and_publish_actions_share_the_global_topbar`` 所描述的业务结果、故障边界和隔离约束。"""
    root = Path(__file__).resolve().parents[2]
    static = root / "generative_agents" / "web" / "static"
    shell = (static / "experiment-console.html").read_text(encoding="utf-8")
    script = (static / "console-api.js").read_text(encoding="utf-8")

    topbar = shell[shell.index('<header class="topbar">') : shell.index("</header>")]
    editor_shell_start = shell.index(
        '<div class="crowd-editor-shell" id="crowdEditorShell"'
    )
    editor_body_start = shell.index(
        '<div class="crowd-editor-body">', editor_shell_start
    )
    editor_shell_lead = shell[editor_shell_start:editor_body_start]

    for element_id in (
        "crowdEditorTopbarContext",
        "backToCrowdsBtn",
        "crowdEditorTitle",
        "crowdEditorMeta",
        "crowdEditorState",
        "crowdEditorTopbarActions",
        "manageCrowdAgentsBtn",
        "saveCrowdBtn",
        "publishCrowdBtn",
    ):
        assert f'id="{element_id}"' in topbar
        assert f'id="{element_id}"' not in editor_shell_lead
    assert "const crowdActive =" in script
    assert "document.body.classList.toggle('crowd-editor-mode', crowdActive)" in script


def test_console_ui_font_uses_sidebar_typography_as_the_global_baseline():
    """回归验证 ``test_console_ui_font_uses_sidebar_typography_as_the_global_baseline`` 所描述的业务结果、故障边界和隔离约束。"""
    root = Path(__file__).resolve().parents[2]
    static = root / "generative_agents" / "web" / "static"
    ux_style = (static / "console-ux.css").read_text(encoding="utf-8")
    map_style = (static / "map-workspace.css").read_text(encoding="utf-8")
    crowd_style = (static / "crowd-workspace.css").read_text(encoding="utf-8")

    assert "--sans: var(--font)" in ux_style
    assert "body {\n  font-family: var(--font);" in ux_style
    assert "button,\ninput,\nselect,\ntextarea {\n  font-family: var(--font);" in ux_style
    assert "#mapEditorMeta { color: var(--muted); font: 10px/1.3 var(--font);" in map_style
    assert "font-family: var(--font);\n  font-size: 12px;" in map_style
    assert "system-ui" not in map_style
    assert ".me2-node-copy strong { font-size: 12px;" in map_style
    assert ".me2-form-section .control { font-size: 12px;" in map_style
    assert ".crowd-editor-shell { font-size:12px; }" in crowd_style
    assert ".crowd-editor-shell .control { font-size:12px; }" in crowd_style


@pytest.mark.skip(reason="the graph workflow editor was intentionally removed")
def test_removed_prompt_workspace_was_a_self_contained_workflow_editor(
    database_url,
):
    """回归验证 ``test_removed_prompt_workspace_was_a_self_contained_workflow_editor`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        shell = client.get("/").text
        editor_response = client.get("/static/console/workflow-editor.js")
        style_response = client.get("/static/console/workflow-editor.css")
        console_response = client.get("/static/console/console-api.js")

    assert editor_response.status_code == 200
    assert style_response.status_code == 200
    assert console_response.status_code == 200
    editor = editor_response.text
    style = style_response.text
    console = console_response.text
    shell_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', shell))
    lookups = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", editor))
    dynamic_ids = {
        "workflowFailurePolicy",
        "workflowNodeBody",
        "workflowNodeOperation",
        "workflowNodeSubflow",
        "workflowNodeTitle",
        "workflowResponseSchema",
        "workflowRetryAttempts",
        "workflowRetrySchema",
        "workflowSelectorMode",
        "workflowTimeout",
    }
    assert lookups - dynamic_ids <= shell_ids, sorted(lookups - dynamic_ids - shell_ids)
    assert all(f'id="{item}"' in editor for item in dynamic_ids)
    assert 'id="workflowTabs" role="tablist"' in shell
    for kind in (
        "llm",
        "code",
        "selector",
        "variable_assigner",
        "variable_aggregator",
        "subflow",
    ):
        assert f'data-workflow-add="{kind}"' in shell
    assert 'data-workflow-add="script"' not in shell
    assert 'id="workflowVersionPopover"' in shell
    assert 'id="workflowFunctionPage"' in shell
    assert 'id="workflowFunctionManagerBtn"' in shell
    assert 'id="workflowExecutionMode"' in shell
    assert 'id="workflowMigrateRouterBtn"' in shell
    assert 'id="workflowConnectHint"' in shell
    assert 'id="workflowCanvasScroller"' in shell
    assert 'id="workflowHorizontalLayoutBtn"' in shell
    assert 'id="promptList"' not in shell
    assert 'id="promptEditor"' not in shell
    assert "新建流程" not in shell
    assert "Prompt 套件说明" not in shell
    assert "function enableDrag" in editor
    assert "data-node-drag-handle" in editor
    assert "if (drag.moved && !editorState.readonly) setDirty(true)" in editor
    assert "function enableCanvasPan" in editor
    assert "function enableMinimapPan" in editor
    assert "async function openBrain" in editor
    assert "async function openExperiment" in editor
    assert "function openCapability" in editor
    assert "function refreshViewport" in editor
    assert "minimap.classList.add('dragging')" in editor
    assert "cursor:grab" in style
    assert ".workflow-minimap.dragging { cursor:grabbing; }" in style
    assert "cursor:crosshair" not in next(line for line in style.splitlines() if line.startswith(".workflow-minimap {"))
    assert "scroller.scrollLeft = pan.scrollLeft - dx" in editor
    assert "autoLayout('horizontal')" in editor
    assert "function handleConnectionClick" in editor
    assert "真实执行 · Prompt 路由" in editor
    assert "/migrate-router" in editor
    assert "data-remove-edge" in editor
    assert "结构化输出 JSON Schema" in editor
    assert "{step_context.agent.name}" in editor
    assert "dataTypeOptions" in editor
    assert "api('/workflow-functions')" in editor
    assert "async function restoreVersion" in editor
    assert "async function save" in editor
    assert "一键恢复" in editor
    assert "restored.restored_as_version_no" in editor
    assert "flow.dirty = false" in editor
    assert "默认流程" in editor
    assert "function renderDirtyState()" in console
    assert "state.dirty = Boolean(state.formDirty || state.workflowDirty)" in console
    assert "state.formDirty = true" in console
    assert "function discard()" in editor
    assert "window.WorkflowEditor?.discard()" in console

    node = shutil.which("node")
    assert node, "Node.js is required for production JavaScript syntax checks"
    subprocess.run(
        [node, "--check", str(Path(__file__).parents[2] / "generative_agents" / "web" / "static" / "workflow-editor.js")],
        check=True,
        capture_output=True,
        text=True,
    )


def test_skill_workspace_is_file_backed_and_self_contained(database_url):
    """回归验证 ``test_skill_workspace_is_file_backed_and_self_contained`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        shell = client.get("/").text
        script_response = client.get("/static/console/skill-workspace.js")
        style_response = client.get("/static/console/skill-workspace.css")

    assert script_response.status_code == 200
    assert style_response.status_code == 200
    script = script_response.text
    assert 'id="page-skills"' in shell
    assert 'id="page-brains"' in shell
    assert 'id="page-experiment-brain"' in shell
    assert "/api/v1/skills" in script
    assert "SKILL.md" in script
    assert "Scripts 与 MCP" in script
    assert "使用 Qwen3.8 27B 运行" in script
    assert "/dependencies" in script
    assert "/history" in script
    assert "workflow-editor.js" not in shell
    assert "brain-workspace.js" not in shell
    assert "capability-workspace.js" not in shell

    node = shutil.which("node")
    assert node, "Node.js is required for production JavaScript syntax checks"
    subprocess.run(
        [
            node,
            "--check",
            str(
                Path(__file__).parents[2]
                / "generative_agents"
                / "web"
                / "static"
                / "skill-workspace.js"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_agent_result_page_is_agent_owned_and_switches_structured_outputs_by_tab():
    """回归验证 ``test_agent_result_page_is_agent_owned_and_switches_structured_outputs_by_tab`` 所描述的业务结果、故障边界和隔离约束。"""
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
    assert 'data-tooltip="每个 Agent 是一个独立结果单元；切换上方 Tab' not in shell
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
    """回归验证 ``test_dynamic_card_and_error_paths_are_owned_by_the_production_script`` 所描述的业务结果、故障边界和隔离约束。"""
    script = (
        Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "console-api.js"
    ).read_text(encoding="utf-8")

    assert "function showToast(message, title" in script
    assert "function reportError(error)" in script
    assert "发生了什么：${error.message || String(error)}" in script
    assert "service_error_code: error.code || 'CLIENT_ERROR'" in script
    assert "openExperiment(card.dataset.id).catch(reportError)" in script
    assert "state.currentExperimentName = experiment.name" in script
    assert "state.currentExperimentStatus = statusLabels[experiment.status]" in script


def test_modal_focus_runtime_traps_both_tab_directions_and_restores_focus():
    """回归验证 ``test_modal_focus_runtime_traps_both_tab_directions_and_restores_focus`` 所描述的业务结果、故障边界和隔离约束。"""
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
    """回归验证 ``test_console_owns_global_activity_reconciliation_and_resume_hooks`` 所描述的业务结果、故障边界和隔离约束。"""
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
    """回归验证 ``test_selected_result_run_is_not_confused_with_the_experiment_latest_run`` 所描述的业务结果、故障边界和隔离约束。"""
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
    """回归验证 ``test_console_url_tracks_the_selected_experiment_workspace_and_run`` 所描述的业务结果、故障边界和隔离约束。"""
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


def test_overview_is_a_single_definition_workspace_while_other_workspaces_keep_tabs():
    """回归验证 ``test_overview_is_a_single_definition_workspace_while_other_workspaces_keep_tabs`` 所描述的业务结果、故障边界和隔离约束。"""
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

    for group in ("models", "world", "advanced", "agent-editor"):
        assert f'data-content-tabs="{group}"' in shell
    overview = shell[shell.index('id="page-overview"') : shell.index('id="page-results"')]
    agents = shell[shell.index('id="page-agents"') : shell.index('id="page-models"')]
    topbar = shell[shell.index('<header class="topbar">') : shell.index('</header>')]
    assert 'data-content-tabs="overview"' not in overview
    assert 'role="tablist"' not in overview
    assert '<h1>实验概览</h1>' not in overview
    assert 'id="overviewRevisionState"' not in overview
    assert 'id="overviewRevisionCode"' not in overview
    assert 'id="overviewRevisionTime"' not in overview
    assert 'class="stats overview-stats"' in overview
    assert 'id="overviewReleaseDetails" hidden' in overview
    assert 'id="publishBtn">发布版本并启动实验</button>' in overview
    assert 'Agent 编组' not in overview
    assert 'for="expName"' not in overview
    assert 'for="expKey"' not in overview
    assert 'for="logLevel"' not in overview
    assert 'id="experimentHeaderMeta" hidden' in topbar
    assert 'id="experimentOwnerMeta"' in topbar
    assert 'id="experimentTagsMeta"' in topbar
    assert 'id="expOwner"' not in shell
    assert 'id="expTags"' not in shell
    assert topbar.index('id="topbarTitle"') < topbar.index('id="experimentHeaderMeta"')
    assert '控制 Agent 状态记录点的密度' in overview
    assert 'id="overviewLatestRunCode"' in overview
    assert 'class="result-metrics"' not in shell
    assert '<h1>Agent 配置</h1>' not in agents
    assert 'Agent 配置说明' not in agents
    assert 'id="selectedAgentCount"' not in agents
    assert "$('selectedAgentCount')" not in script
    assert "function setContentTab" in script
    assert "definition.simulation.log_level = 'INFO';" in script
    latest_summary = script[
        script.index("async function fillLatestRunSummary") : script.index(
            "function behaviorControlKey"
        )
    ]
    assert "/results/summary" not in latest_summary
    assert "/results/operations" not in latest_summary
    assert "url.searchParams.set('tab'," in script
    assert "window.addEventListener('popstate'" in script
    assert "Apply deep-link state before loading the experiment" in script
    assert script.index("state.selectedAgentContent = requestedTab") < script.index(
        "await openExperiment(experimentId"
    )


def test_running_duration_uses_utc_instants_and_a_live_execution_label():
    """回归验证 ``test_running_duration_uses_utc_instants_and_a_live_execution_label`` 所描述的业务结果、故障边界和隔离约束。"""
    root = Path(__file__).parents[2]
    shell = (
        root / "generative_agents" / "web" / "static" / "experiment-console.html"
    ).read_text(encoding="utf-8")
    script_path = root / "generative_agents" / "web" / "static" / "console-api.js"
    script = script_path.read_text(encoding="utf-8")

    topbar = shell[shell.index('<header class="topbar">') : shell.index('</header>')]
    assert 'id="resultDurationMeta" hidden' in topbar
    assert 'id="resultDurationLabel">实际耗时' in topbar
    assert topbar.index('id="topbarTitle"') < topbar.index('id="resultDurationMeta"') < topbar.index('id="statusPill"')
    assert 'id="resultStepMetric"' not in shell
    assert 'id="resultConversationMetric"' not in shell
    assert 'id="resultMemoryMetric"' not in shell
    assert 'id="resultLlmMetric"' not in shell
    assert "/results/summary" not in script
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
    """回归验证 ``test_agent_results_use_content_tabs_instead_of_an_all_sections_waterfall`` 所描述的业务结果、故障边界和隔离约束。"""
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


def test_recoverable_run_action_uses_resume_without_a_rerun_action():
    """回归验证 ``test_recoverable_run_action_uses_resume_without_a_rerun_action`` 所描述的业务结果、故障边界和隔离约束。"""
    node = shutil.which("node")
    assert node, "Node.js is required for the executable Run action contract"
    root = Path(__file__).parents[2]
    shell_path = root / "generative_agents" / "web" / "static" / "experiment-console.html"
    script_path = root / "generative_agents" / "web" / "static" / "console-api.js"
    shell = shell_path.read_text(encoding="utf-8")
    source = script_path.read_text(encoding="utf-8")

    topbar = shell[shell.index('<header class="topbar">') : shell.index('</header>')]
    results = shell[shell.index('id="page-results"') : shell.index('id="page-agents"')]
    assert 'id="runContinueBtn"' in topbar
    assert 'id="exportResultsBtn"' in topbar
    assert 'id="resultRunSelect"' in topbar
    assert 'id="resultRunControls"' in topbar
    assert 'id="runPauseResumeBtn"' in topbar
    assert 'id="runCancelBtn"' in topbar
    assert 'id="resultRunControls"' not in results
    assert topbar.index('id="resultRunSelect"') < topbar.index('id="resultRunControls"') < topbar.index('id="cloneBtn"')
    assert ".top-actions .btn { flex: 0 0 auto; white-space: nowrap; }" in shell
    assert '<h1>实验结果</h1>' not in results
    assert 'id="resultRunTabs"' not in results
    assert 'data-content-tab="events"' not in results
    assert 'id="summaryKeyEvents"' not in results
    assert "$('summaryKeyEvents')" not in source
    assert 'data-result-tab="summary"' not in results
    assert 'data-result-panel="summary"' not in results
    assert 'data-content-tabs="summary"' not in results
    assert 'data-result-tab="timeline">仿真回放</button>' in results
    assert 'class="result-panel active" data-result-panel="timeline"' in results
    assert ".result-tabs, .content-tabs, .operations-subtabs, .filter-tabs { overflow-y: hidden; scrollbar-width: none; -ms-overflow-style: none; }" in shell
    assert ".result-tabs::-webkit-scrollbar, .content-tabs::-webkit-scrollbar, .operations-subtabs::-webkit-scrollbar, .filter-tabs::-webkit-scrollbar { display: none; width: 0; height: 0; }" in shell
    ux_style = (root / "generative_agents" / "web" / "static" / "console-ux.css").read_text(encoding="utf-8")
    assert '[role="tablist"] {' in ux_style
    assert "overflow-y: hidden" in ux_style
    assert '[role="tablist"]::-webkit-scrollbar {' in ux_style
    assert results.index('id="resultMap"') < results.index('id="timelineRange"') < results.index('id="replayInspector"') < results.index('id="timelineStreamItems"') < results.index('id="replayAgentRoster"')
    assert 'class="timeline-toolbar replay-sidebar-controls"' in results
    assert 'class="replay-sidebar-events"' in results
    assert ".replay-sidebar-controls .replay-options select { width: 46px; height: 24px; min-height: 24px;" in shell
    assert 'id="replayLayerAgentNames"' not in results
    assert 'id="replayLayerActionBubbles"' not in results
    assert 'id="replayLayerConversations"' not in results
    assert "const conversations = step.conversations;" in source
    assert 'id="replayCameraMode" aria-label="回放镜头模式" hidden' in results
    assert 'id="replayAgentSelect" aria-label="回放 Agent" hidden' in results
    assert 'id="replayCameraState">自由镜头' in results
    assert 'function applyReplayAgentSelection(agentKey)' in source
    assert 'state.replayPlayer?.followAgent(key);' in source
    assert "applyReplayAgentSelection(state.selectedReplayAgentKey === key ? null : key)" in source
    assert "resultTab: 'timeline'" in source
    assert "requestedResultTabParam === 'summary' ? 'timeline'" in source
    assert "function renderSummary" not in source
    assert "function renderActivityChart" not in source
    for removed_id in (
        "openRunHistory",
        "resultStatusChip",
        "resultRevision",
        "resultWindow",
        "resultSync",
    ):
        assert f'id="{removed_id}"' not in shell
    assert "function renderRunSelect" in source
    assert 'id="agentResultCount"' not in results
    assert "Agent 结果说明" not in results
    assert "在 Agent 之间快速切换" not in results
    assert "内容随运行自动更新" not in results
    assert "$('agentResultCount')" not in source
    assert "refreshResultData(runId, generation, { silent: true })" in source
    assert "showAgentDetail(state.selectedAgentKey, { silent })" in source
    assert "if (!silent) panel.innerHTML" in source
    assert "state.agentDetailSignatures.get(agentKey) === signature" in source
    assert "strip.scrollLeft = previousScrollLeft" in source
    assert "if (silent) window.scrollTo(scrollX, scrollY)" in source
    assert "const AGENT_CONTENT_PAGE_SIZE = 5" in source
    assert source.count("agentContentPager('") == 6
    assert 'class="agent-content-pagination"' in source
    assert 'data-agent-page-kind="${kind}"' in source
    assert "state.agentContentPages.set(pageKey, targetPage)" in source
    assert "state.agentDetailCache.get(`${state.selectedRunId}:${state.selectedAgentKey}`)" in source
    agent_sections = source[source.index("function renderAgentPlanSection") : source.index("function agentPlanText")]
    assert "slice(0," not in agent_sections
    assert agent_sections.count("slice(pagination.itemsFrom, pagination.itemsTo)") == 6
    assert "const OPERATION_LIST_PAGE_SIZE = 5" in source
    assert 'id="modelUsagePagination"' in results
    assert 'id="modelTracePagination"' in results
    assert 'id="systemEventPagination"' in results
    assert 'id="checkpointPagination"' in results
    assert 'class="diagnostic-lists-grid"' in results
    assert ".diagnostic-lists-grid { display: grid; grid-template-columns: minmax(390px,.82fr) minmax(520px,1.18fr);" in shell
    assert "state.modelUsageItems.slice(pagination.itemsFrom, pagination.itemsTo)" in source
    assert "state.traceItems.slice(pagination.itemsFrom, pagination.itemsTo)" in source
    assert "filtered.slice(pagination.itemsFrom, pagination.itemsTo)" in source
    assert "state.checkpointItems.slice(pagination.itemsFrom, pagination.itemsTo)" in source
    assert "state.eventPage = 1" in source
    assert "state.checkpointPage = page" in source
    assert 'data-operation-list="${kind}"' in source
    assert ".trace-row[data-trace-id] { cursor: pointer; }" in shell
    assert "document.querySelectorAll('.filter-tab[data-filter]')" in source
    assert "while (cursor)" in source
    assert 'id="runAgainBtn"' not in shell
    assert 'id="openReplayBtn"' not in shell
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
const state = {workspacePage:'results'};
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
  };
};
const failed = snapshot({status:'FAILED', recoverable:true, recoverable_step:30});
const unavailable = snapshot({status:'FAILED', recoverable:false, recoverable_step:0});
const paused = snapshot({status:'PAUSED', recoverable:true, recoverable_step:7});
const running = snapshot({status:'RUNNING', recoverable:false, recoverable_step:7});
if (JSON.stringify(failed) !== JSON.stringify({
  pauseHidden:true, cancelHidden:true, continueHidden:false,
  continueText:'继续执行 · Step 30',
})) process.exit(1);
if (!unavailable.continueHidden) process.exit(2);
if (paused.continueHidden || paused.continueText !== '继续执行 · Step 7' || paused.cancelHidden) process.exit(3);
if (running.pauseHidden || running.cancelHidden || !running.continueHidden) process.exit(4);
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
    """回归验证 ``test_console_reconciles_publish_actions_and_renders_artifact_job_states`` 所描述的业务结果、故障边界和隔离约束。"""
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
    assert "function modelAutoProbeMarkup" in source
    assert "report.auto_model_probe" in source
    assert "上次自动检测失败" in source
    assert "const report = await refreshValidation();" in source
    assert "operations.artifact_jobs" in source
    assert "artifact_queued" in source
    assert "artifact_running" in source
    assert "result_rewound" in source


def test_chat_output_limit_is_not_presented_as_the_model_context_window():
    """回归验证 ``test_chat_output_limit_is_not_presented_as_the_model_context_window`` 所描述的业务结果、故障边界和隔离约束。"""
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
    assert 'id="chatServiceStatus"' in shell
    assert "result.service?.context_window" in script
    assert "chat.context_window" in script


def test_replay_player_uses_an_explicit_canvas_renderer_for_custom_browsers():
    """回归验证 ``test_replay_player_uses_an_explicit_canvas_renderer_for_custom_browsers`` 所描述的业务结果、故障边界和隔离约束。"""
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
    """回归验证 ``test_replay_agent_selection_is_revision_owned_and_executable`` 所描述的业务结果、故障边界和隔离约束。"""
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
const cameraEvents = [];
const sprite = {setTint(){return this},clearTint(){return this}};
const circleEvents = [];
const circle = {setStrokeStyle(...args){circleEvents.push(args);return this}};
const instance = new GAReplayPlayer({onAgent(){}});
instance.scene = {cameras:{main:{startFollow(){cameraEvents.push('follow')},stopFollow(){cameraEvents.push('free')}}}};
instance.agentObjects.set('resident-001', {sprite});
instance.agentObjects.set('resident-002', {sprite:circle});
instance.agentDefinitions.set('resident-001', agents[0]);
instance.agentDefinitions.set('resident-002', agents[1]);
if (instance.toggleAgentFollow('resident-001') !== 'resident-001') process.exit(2);
if (instance.selectedAgentKey !== 'resident-001' || instance.followedAgentKey !== 'resident-001') process.exit(3);
if (instance.toggleAgentFollow('resident-001') !== null) process.exit(4);
if (instance.selectedAgentKey !== null || instance.followedAgentKey !== null) process.exit(5);
if (JSON.stringify(cameraEvents) !== JSON.stringify(['follow','free'])) process.exit(6);
instance.selectAgent('resident-002');
instance.selectAgent(null);
if (JSON.stringify(circleEvents) !== JSON.stringify([[0,0xffd166,0],[0,0xffd166,0],[3,0xffd166,1],[0,0xffd166,0]])) process.exit(7);
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
    assert "applyReplayAgentSelection(restoredAgentKey)" in console
    assert "sprite.on('pointerdown', () => this.toggleAgentFollow(definition.agent_key));" in player.read_text(encoding="utf-8")
    assert "if (!payload.selectedAgentKey)" in console


def test_replay_playback_starts_at_step_one_and_restarts_after_the_end():
    """回归验证 ``test_replay_playback_starts_at_step_one_and_restarts_after_the_end`` 所描述的业务结果、故障边界和隔离约束。"""
    node = shutil.which("node")
    assert node, "Node.js is required for the replay transport contract"
    root = Path(__file__).parents[2]
    player = root / "generative_agents" / "web" / "static" / "replay-player.js"
    program = r"""
global.window = global;
global.Phaser = {};
const { GAReplayPlayer } = require(process.argv[1]);
const statuses = [];
const manifest = {
  schema_version:2,run_id:'run-1',revision_id:'revision-1',available_step:3,partial:false,
  world:{render_asset:{status:'READY'}},agents:[],
};
const steps = [1,2,3].map(step_no => ({step_no}));
const fetchImpl = async url => ({
  ok:true,
  json:async() => url.includes('/manifest')
    ? manifest
    : {run_id:'run-1',available_step:3,result_version:1,steps},
});
(async()=>{
  const instance = new GAReplayPlayer({fetchImpl,onStatus:status=>statuses.push(status.state)});
  instance._createGame = async()=>{};
  instance._renderStep = ()=>{};
  await instance.loadRun('run-1');
  if (!instance.ready || instance.currentStep !== 1) throw new Error(`replay did not start at Step 1: ${instance.currentStep}`);
  await Promise.all([instance.stepBy(1), instance.stepBy(1)]);
  if (instance.currentStep !== 3) throw new Error(`queued steps were lost: ${instance.currentStep}`);
  await instance.play();
  if (instance.currentStep !== 1 || !instance.timer) throw new Error('play at the end did not restart from Step 1');
  if (statuses.at(-1) !== 'PLAYING') throw new Error(`unexpected playback state: ${statuses.at(-1)}`);
  instance.pause();
  instance.ready = false;
  await instance.play();
  if (instance.timer) throw new Error('loading replay accepted a play request');
})().catch(error=>{console.error(error);process.exit(1)});
"""
    subprocess.run(
        [node, "-e", program, str(player)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    shell = (root / "generative_agents" / "web" / "static" / "experiment-console.html").read_text(encoding="utf-8")
    console = (root / "generative_agents" / "web" / "static" / "console-api.js").read_text(encoding="utf-8")
    assert 'id="timelinePlay" aria-label="播放" disabled' in shell
    assert "replayReady: false" in console
    assert "atEnd ? '↻' : '▶'" in console
    assert "updateTimelineStep(Number(slider.value), { seekReplay: false });" in console
    assert "state.replayPlayer.play().catch(reportError);" in console


def test_running_replay_refetches_an_incomplete_cached_tail_after_growth():
    """回归验证 ``test_running_replay_refetches_an_incomplete_cached_tail_after_growth`` 所描述的业务结果、故障边界和隔离约束。"""
    node = shutil.which("node")
    assert node, "Node.js is required for the replay cache growth contract"
    root = Path(__file__).parents[2]
    player = root / "generative_agents" / "web" / "static" / "replay-player.js"
    program = r"""
global.window = global;
global.Phaser = {};
const { GAReplayPlayer } = require(process.argv[1]);
let availableStep = 63;
let windowRequests = 0;
const manifest = () => ({
  schema_version:2,run_id:'run-growing',revision_id:'revision-1',available_step:availableStep,partial:true,
  world:{render_asset:{status:'READY'}},agents:[],
});
const fetchImpl = async url => ({
  ok:true,
  json:async() => {
    if (url.includes('/manifest')) return manifest();
    windowRequests += 1;
    const from = Number(new URL(url, 'http://localhost').searchParams.get('from_step'));
    const steps = Array.from(
      {length: Math.max(0, availableStep - from + 1)},
      (_, index) => ({step_no: from + index}),
    ).slice(0, 100);
    return {run_id:'run-growing',available_step:availableStep,result_version:availableStep,steps};
  },
});
(async()=>{
  const instance = new GAReplayPlayer({fetchImpl});
  instance._createGame = async()=>{};
  instance._renderStep = ()=>{};
  await instance.loadRun('run-growing');
  await instance.seek(63);
  if (windowRequests !== 1) throw new Error(`initial tail was fetched ${windowRequests} times`);
  availableStep = 72;
  await instance.refreshAvailable();
  const step = await instance.seek(72);
  if (!step || instance.currentStep !== 72) throw new Error(`replay remained at Step ${instance.currentStep}`);
  if (windowRequests !== 2) throw new Error(`growing tail was not refetched: ${windowRequests}`);
})().catch(error=>{console.error(error);process.exit(1)});
"""
    subprocess.run(
        [node, "-e", program, str(player)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def test_replay_phaser_canvas_stays_owned_by_the_result_map_container():
    """回归验证 ``test_replay_phaser_canvas_stays_owned_by_the_result_map_container`` 所描述的业务结果、故障边界和隔离约束。"""
    node = shutil.which("node")
    assert node, "Node.js is required for the replay canvas ownership contract"
    root = Path(__file__).parents[2]
    player = root / "generative_agents" / "web" / "static" / "replay-player.js"
    program = r"""
global.window = global;
global.devicePixelRatio = 1.5;
global.ResizeObserver = undefined;
let captured;
let initialZoom;
const layer = {setDepth(){return this},setVisible(){return this}};
const scene = {
  make:{tilemap(){return {widthInPixels:3200,heightInPixels:2400,addTilesetImage(name){return name},createLayer(){return layer}}}},
  cameras:{main:{setBounds(){},setZoom(value){initialZoom=value}}},
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
  if (captured.resolution !== 1.5) throw new Error(`unexpected display resolution: ${captured.resolution}`);
  if (initialZoom !== 0.7) throw new Error(`unexpected initial zoom: ${initialZoom}`);
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
    assert "label.setResolution(TEXT_RENDER_RESOLUTION)" in source
    assert "bubble.setResolution(TEXT_RENDER_RESOLUTION)" in source
    assert source.count("fontSize: '11px'") == 2
    shell = (root / "generative_agents" / "web" / "static" / "experiment-console.html").read_text(encoding="utf-8")
    result_canvas_css = re.search(r"\.result-map\s*>\s*canvas\s*\{([^}]*)\}", shell)
    assert result_canvas_css and "transform:" not in result_canvas_css.group(1)


def test_replay_agent_name_and_action_emoji_use_separate_offsets():
    """回归验证 ``test_replay_agent_name_and_action_emoji_use_separate_offsets`` 所描述的业务结果、故障边界和隔离约束。"""
    node = shutil.which("node")
    assert node, "Node.js is required for the replay overlay layout contract"
    root = Path(__file__).parents[2]
    player = root / "generative_agents" / "web" / "static" / "replay-player.js"
    program = r"""
global.window = global;
global.Phaser = {};
const {GAReplayPlayer}=require(process.argv[1]);
const positions={};
const instance=new GAReplayPlayer({});
instance.scene={
  tweens:{killTweensOf(){},add(){}},
};
instance.agentObjects.set('agent-1',{
  sprite:{},
  label:{setPosition(x,y){positions.label=[x,y]}},
  bubble:{setText(){return this},setPosition(x,y){positions.bubble=[x,y]}},
});
instance._renderStep({
  step_no:1,
  agents:[{agent_key:'agent-1',coord:[2,3],path:[],action:{emoji:'🙂'}}],
});
const expected={label:[62,68],bubble:[118,88]};
if(JSON.stringify(positions)!==JSON.stringify(expected)){
  throw new Error(`unexpected replay overlay positions: ${JSON.stringify(positions)}`);
}
"""
    subprocess.run(
        [node, "-e", program, str(player)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def test_replay_canvas_survives_running_completed_running_switches():
    """回归验证 ``test_replay_canvas_survives_running_completed_running_switches`` 所描述的业务结果、故障边界和隔离约束。"""
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
    """回归验证 ``test_replay_uses_a_packaged_tile_aligned_texture_without_changing_legacy_source`` 所描述的业务结果、故障边界和隔离约束。"""
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
    """回归验证 ``test_replay_uses_a_packaged_tilemap_with_only_the_invalid_imageheight_corrected`` 所描述的业务结果、故障边界和隔离约束。"""
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
