from __future__ import annotations

import inspect
import json
import logging
import os
import random
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from generative_agents.config import ExperimentDefinition, make_builtin_definition
from generative_agents.config.schema import REQUIRED_PROMPT_KEYS, make_blank_definition
from generative_agents.modules import memory as memory_module
from generative_agents.modules.config_adapter import ConfigAdapter
from generative_agents.modules.game import Game
from generative_agents.persistence import create_database, upgrade_database
from generative_agents.persistence.models import (
    RunAgentSummary,
    RunArtifact,
    RunAttempt,
    RunResultSummary,
    RunStep,
)
from generative_agents.runtime import worker
from generative_agents.runtime.algorithm import get_algorithm_profile
from generative_agents.runtime.artifact_builder import ArtifactBuilder
from generative_agents.runtime.artifact_scheduler import ArtifactSchedulerRepository
from generative_agents.runtime.context import RunControl, RunPaths, SimulationClock
from generative_agents.runtime.frame_store import FrameStore
from generative_agents.runtime.results import (
    ActionSnapshot,
    ActivityKind,
    AgentStepResult,
    StepResultBuilder,
)
from generative_agents.runtime.scheduler import LocalRunSchedulerRepository
from generative_agents.runtime.sqlite_result_projector import SqliteResultProjector
from generative_agents.services import ExperimentService
from generative_agents.services.artifacts import ArtifactService
from generative_agents.services.legacy_import import LegacyImportService
from generative_agents.services.results import ResultQueryService
from generative_agents.services.runs import RunService
from generative_agents.web import create_app
from generative_agents.web import app as web_app_module


ROOT = Path(__file__).resolve().parents[2]
PROTOTYPE = ROOT / "docs" / "experiment-console.html"
CONSOLE_JS = ROOT / "generative_agents" / "web" / "static" / "console-api.js"


@pytest.fixture
def database(tmp_path: Path):
    url = "sqlite:///" + (tmp_path / "final-e2e.db").as_posix()
    upgrade_database(url)
    value = create_database(url)
    yield value
    value.close()


def _definition(key: str) -> ExperimentDefinition:
    definition = make_blank_definition(key=key, name=f"Experiment {key}")
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["models"]["chat"]["resolved_model"] = "Qwen/test-chat"
    payload["models"]["embedding"]["resolved_model"] = "test-embedding"
    payload["world"]["definition"] = {
        "world": "test",
        "tile_size": 16,
        "size": [1, 2],
        "map": [[0, 0]],
        "camera": [0, 0],
        "tile_address_keys": ["world", "sector", "arena", "game_object"],
        "tiles": [
            {"coord": [0, 0], "collision": False, "address": ["home", "bedroom", "bed"]},
            {"coord": [1, 0], "collision": False, "address": ["home", "bedroom", "bed"]},
        ],
    }
    payload["agents"] = [
        {
            "agent_key": "test-agent",
            "enabled": True,
            "name": "Test Agent",
            "portrait_asset": None,
            "coord": [0, 0],
            "currently": "testing",
            "scratch": {
                "age": 30,
                "innate": "careful",
                "learned": "tests systems",
                "lifestyle": "repeatable",
                "daily_plan": "",
            },
            "spatial": {
                "address": {
                    "living_area": ["test", "home", "bedroom"],
                    "sleeping": ["test", "home", "bedroom", "bed"],
                },
                "tree": {"test": {"home": {"bedroom": ["bed"]}}},
            },
        }
    ]
    payload["prompts"] = {
        key: {"content": f"Prompt {key}"} for key in REQUIRED_PROMPT_KEYS
    }
    return ExperimentDefinition.model_validate(payload)


def _publish(database, definition: ExperimentDefinition):
    service = ExperimentService(database)
    experiment = service.create_experiment(
        name=definition.experiment.name,
        goal=definition.experiment.goal,
        source_type="BLANK",
    )
    draft = service.get_draft(experiment["id"])
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["experiment"]["key"] = experiment["experiment_key"]
    draft = service.update_draft(
        experiment_id=experiment["id"],
        expected_lock_version=draft["lock_version"],
        definition=ExperimentDefinition.model_validate(payload),
    )
    revision = service.publish_draft(
        experiment_id=experiment["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )
    return experiment, revision


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_def_030_worker_renews_ownership_before_heavy_engine_import():
    """A slow Windows import must not make a healthy attempt look abandoned."""

    source = inspect.getsource(worker.main)
    heartbeat = source.index("repository.heartbeat")
    monitor = source.index("monitor.start()")
    engine_import = source.index("from generative_agents.start import build_runner")
    assert heartbeat < monitor < engine_import
    assert "llm_model import create_llm_model" not in inspect.getsource(worker.main)
    assert "llm_model import create_llm_model" in inspect.getsource(
        worker.ModelFactoryRegistry.get
    )


def test_def_031_runtime_thread_lock_is_not_deepcopied_into_agent(monkeypatch, tmp_path):
    """RunControl contains locks; construction must retain, not deepcopy, it."""

    captured = []

    class FakeAssociate:
        def __init__(self, _path, *_args, **kwargs):
            captured.append(kwargs)
            self.last_evicted = ()

        def to_dict(self):
            return {"memory": {"event": [], "thought": [], "chat": []}}

    class Logger:
        def info(self, *_args, **_kwargs):
            pass

        debug = info
        warning = info

    monkeypatch.setattr(memory_module, "Associate", FakeAssociate)
    definition = _definition("thread-lock")
    config = ConfigAdapter().game_config(definition)
    config["storage_root"] = str(tmp_path / "attempt-storage")
    control = RunControl()
    run_id, attempt_id = uuid4(), uuid4()
    context = SimpleNamespace(
        run_id=run_id,
        attempt_id=attempt_id,
        clock=SimulationClock(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        random=random.Random(7),
        paths=RunPaths.under(tmp_path, run_id),
        prompts={},
        models=None,
        metadata={},
        logger=Logger(),
        control=control,
        algorithm=get_algorithm_profile("ga-cn-v1"),
    )

    game = Game(config, {}, context=context)

    assert game.get_agent("test-agent").associate is not None
    assert captured[0]["embedding"]["_control"] is control


def test_def_032_worker_projects_trace_after_each_committed_step(monkeypatch, tmp_path):
    calls = []

    class FakeCheckpoint:
        def __init__(self, *_args, **_kwargs):
            pass

    class FakeResultProjector:
        def __init__(self, *_args, **_kwargs):
            pass

        def commit_step(self, result, *, frame, checkpoint_path):
            calls.append(("result", result.step_no, frame, checkpoint_path))
            return 19

    class FakeTraceProjector:
        def __init__(self, *_args, **_kwargs):
            pass

        def project(self, **kwargs):
            calls.append(("trace", kwargs))

    class FakeCommitter:
        def __init__(self, _frames, projection, _checkpoint):
            self.projection = projection

    monkeypatch.setattr(worker, "CheckpointBundleWriter", FakeCheckpoint)
    monkeypatch.setattr(worker, "SqliteResultProjector", FakeResultProjector)
    monkeypatch.setattr(worker, "ModelTraceProjector", FakeTraceProjector)
    monkeypatch.setattr(worker, "FileStepCommitter", FakeCommitter)

    run_id, attempt_id = uuid4(), uuid4()
    paths = RunPaths.under(tmp_path / "var", run_id)
    paths.ensure()
    trace_path = paths.traces / "attempt.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text("", encoding="utf-8")
    runner = SimpleNamespace(
        context=SimpleNamespace(paths=paths),
        game=SimpleNamespace(
            snapshot_state=lambda: {}, conversation={}, storage_exporters=lambda: {}
        ),
        committer=None,
    )
    worker._install_sqlite_committer(
        runner,
        object(),
        tmp_path / "var",
        checkpoint_retention=2,
        trace_writer=SimpleNamespace(path=trace_path),
    )
    result = SimpleNamespace(run_id=run_id, attempt_id=attempt_id, step_no=1)

    version = runner.committer.projection.commit_step(
        result, frame="durable-frame", checkpoint_path="checkpoint"
    )

    assert version == 19
    assert calls[0] == ("result", 1, "durable-frame", "checkpoint")
    assert calls[1][0] == "trace"
    assert calls[1][1]["run_id"] == str(run_id)
    assert calls[1][1]["attempt_id"] == str(attempt_id)


def test_def_032_zero_step_failure_still_projects_complete_model_traces():
    """A model failure before commit has trace facts but no StepResult callback."""

    source = inspect.getsource(worker.main)
    finally_block = source[source.index("finally:") :]
    assert "ModelTraceProjector" in finally_block and ".project(" in finally_block, (
        "worker exit has no trace flush, so a zero-step failure leaves model_usage/summary empty"
    )


def test_def_033_artifact_replay_closes_over_observed_frame_path(database, tmp_path):
    var_dir = tmp_path / "var"
    experiment, revision = _publish(database, _definition("artifact-frame"))
    runs = RunService(database, var_dir=var_dir)
    run = runs.create_from_published(experiment["id"], revision["id"])
    scheduler = LocalRunSchedulerRepository(database)
    attempt = scheduler.claim_next()
    assert scheduler.register_worker(attempt, pid=os.getpid(), pid_create_time=1.0)
    builder = StepResultBuilder(
        run_id=UUID(run["run_id"]),
        attempt_id=UUID(attempt.attempt_id),
        step_no=1,
        virtual_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    builder.add_agent(
        AgentStepResult(
            agent_key="test-agent",
            from_coord=(0, 0),
            to_coord=(1, 0),
            path=((0, 0), (1, 0)),
            path_source="OBSERVED",
            action=ActionSnapshot("walk to the lab", "go"),
            activity_kind=ActivityKind.MOVING,
            location=("test", "lab"),
        )
    )
    result = builder.freeze()
    frame = FrameStore(RunPaths.under(var_dir, UUID(run["run_id"]))).write(result)
    SqliteResultProjector(database, var_dir=var_dir).commit_step(
        result, frame=frame, checkpoint_path=None
    )
    artifacts = ArtifactService(database, var_dir=var_dir)
    job = artifacts.create_job(run["run_id"], job_type="BUILD_REPLAY")
    claim = ArtifactSchedulerRepository(database).claim_next()
    assert ArtifactSchedulerRepository(database).register(
        claim, pid=os.getpid(), create_time=1.0
    )

    artifact_id = ArtifactBuilder(database, var_dir=var_dir).build(job["job_id"])
    _artifact, path = artifacts.content(run["run_id"], artifact_id)
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["available_step"] == 1
    assert document["steps"][0]["agents"][0]["path"] == [[0, 0], [1, 0]]
    assert document["steps"][0]["agents"][0]["path_source"] == "OBSERVED"


def test_def_034_zero_step_timeline_has_the_same_collection_schema(database, tmp_path):
    experiment, revision = _publish(database, _definition("zero-step"))
    run = RunService(database, var_dir=tmp_path / "var").create_from_published(
        experiment["id"], revision["id"]
    )

    timeline = ResultQueryService(database).timeline(run["run_id"])

    assert timeline == {
        "run_id": run["run_id"],
        "available_step": 0,
        "requested_steps": run["requested_steps"],
        "steps": [],
        "events": [],
        "agent_steps": [],
    }


def test_def_035_sse_opens_after_current_state_and_history_cursor():
    source = CONSOLE_JS.read_text(encoding="utf-8")
    function = source[source.index("async function loadResults"):source.index("function scheduleResultRefresh")]
    refresh = function.index("await refreshResultData")
    history = function.index("const eventPage = await api")
    stream = function.index("new EventSource")
    assert refresh < history < stream
    assert "after_id=${eventPage.next_after_id || 0}" in function


def test_def_035_sse_cursor_skips_an_event_backlog_larger_than_one_page():
    source = CONSOLE_JS.read_text(encoding="utf-8")
    function = source[
        source.index("async function loadResults") : source.index(
            "function scheduleResultRefresh"
        )
    ]
    safe_tail_cursor = any(
        marker in function
        for marker in (
            "tail=true",
            "order=-id",
            "/events/latest",
            "while (eventPage.next_after_id",
            "while(eventPage.next_after_id",
        )
    )
    assert safe_tail_cursor, (
        "one ascending limit=500 page is not the latest cursor; later historical RUNNING "
        "events can still overwrite a current terminal status"
    )


def test_def_036_agent_modal_has_scrollable_body_and_reachable_footer():
    html = PROTOTYPE.read_text(encoding="utf-8")
    assert re.search(r"\.modal\s*\{[^}]*max-height:\s*calc\(100vh\s*-\s*40px\)", html)
    assert re.search(r"\.modal-body\s*\{[^}]*overflow-y:\s*auto", html)
    modal = html[html.index('id="agentEditorModal"'):]
    assert modal.index('class="modal-body"') < modal.index('class="modal-foot"')


def test_def_036_agent_modal_traps_focus_and_restores_the_trigger():
    html = (
        ROOT / "generative_agents" / "web" / "static" / "experiment-console.html"
    ).read_text(encoding="utf-8")
    source = CONSOLE_JS.read_text(encoding="utf-8")
    modal_start = html.index('id="agentEditorModal"')
    modal_opening = html[modal_start : html.index(">", modal_start) + 1]
    assert 'role="dialog"' in modal_opening and 'aria-modal="true"' in modal_opening

    open_editor = source[
        source.index("function openAgentEditor") : source.index(
            "async function saveAgentEditor"
        )
    ]
    assert "openModal('agentEditorModal'" in open_editor
    open_modal = source[
        source.index("function openModal") : source.index("function closeModal")
    ]
    close_modal = source[
        source.index("function closeModal") : source.index("function handleModalKeydown")
    ]
    keydown = source[
        source.index("function handleModalKeydown") : source.index(
            "function openPublishModal"
        )
    ]
    assert "document.activeElement" in open_modal
    assert "state.modalReturnFocus" in open_modal
    assert "setBackgroundInert(true)" in open_modal and ".focus(" in open_modal
    assert "setBackgroundInert(false)" in close_modal
    assert "returnFocus.focus(" in close_modal
    assert ".inert" in source, "background remains keyboard-reachable while modal is open"
    assert "$('agentEditorModal').addEventListener('keydown'" in source
    assert "event.key === 'Tab'" in keydown and "event.shiftKey" in keydown
    assert "event.key === 'Escape'" in keydown


def test_def_036_modal_focus_explicitly_advances_middle_items():
    """Focus movement must not depend on a browser's native Tab default action."""

    node = shutil.which("node")
    assert node, "Node.js is required for the executable modal focus contract"
    focus_module = (
        ROOT / "generative_agents" / "web" / "static" / "modal-focus.js"
    )
    program = r"""
const { tabTarget } = require(process.argv[1]);
const first = { id: 'first' };
const middle = { id: 'middle' };
const last = { id: 'last' };
const focusables = [first, middle, last];
const actual = {
  firstForward: tabTarget(focusables, first, false)?.id,
  middleForward: tabTarget(focusables, middle, false)?.id,
  lastForward: tabTarget(focusables, last, false)?.id,
  lastBackward: tabTarget(focusables, last, true)?.id,
  middleBackward: tabTarget(focusables, middle, true)?.id,
  firstBackward: tabTarget(focusables, first, true)?.id,
  outsideForward: tabTarget(focusables, {}, false)?.id,
  outsideBackward: tabTarget(focusables, {}, true)?.id,
};
const expected = {
  firstForward: 'middle', middleForward: 'last', lastForward: 'first',
  lastBackward: 'middle', middleBackward: 'first', firstBackward: 'last',
  outsideForward: 'first', outsideBackward: 'last',
};
if (JSON.stringify(actual) !== JSON.stringify(expected)) {
  console.error(JSON.stringify({ actual, expected }));
  process.exit(1);
}
"""
    result = subprocess.run(
        [node, "-e", program, str(focus_module)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_def_037_invalid_asset_upload_is_a_422_error_envelope(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "asset-http.db").as_posix()
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assets",
            files={"file": ("malformed.json", b"not-json-or-image", "application/json")},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_ASSET"
    assert response.headers["X-Request-ID"]


def test_def_038_production_result_shell_contains_no_real_looking_demo_facts(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "shell.db").as_posix()
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        html = client.get("/").text
    forbidden = (
        "run_0109",
        "cfg_8f3a2c1",
        "共 3,842 条",
        "情人节社会传播实验",
        "Isabella Rodriguez",
    )
    assert not [value for value in forbidden if value in html]


def test_def_039_production_experiment_badges_are_not_hardcoded(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "badges.db").as_posix()
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        html = client.get("/").text
    assert '<span class="nav-count" id="navRunCount">3</span>' not in html
    assert 'class="experiment-card" data-status=' not in html


def test_def_040_production_shell_does_not_ship_prototype_event_listeners(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "listeners.db").as_posix()
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        html = client.get("/").text
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, flags=re.DOTALL)
    assert not [script for script in scripts if script.strip()], (
        "inline prototype state/listeners execute beside console-api.js"
    )
    assert html.count('/static/console/console-api.js') == 1


def test_def_045_console_api_owns_every_required_ui_global():
    """The production bundle must not depend on globals from the removed prototype."""

    source = CONSOLE_JS.read_text(encoding="utf-8")
    state_replacements = {
        "currentExperiment": "state.currentExperimentName",
        "currentExperimentStatus": "state.currentExperimentStatus",
        "currentWorkspaceReadonly": "state.workspaceReadonly",
    }
    required_functions = {
        "showToast",
        "markDirty",
        "clearDirty",
        "applyStatusPill",
        "setWorkspaceMode",
        "goToPage",
    }
    missing = []
    for legacy_name, replacement in sorted(state_replacements.items()):
        if replacement not in source:
            missing.append(f"{legacy_name} -> {replacement}")
        bare_lines = [
            line
            for line in source.splitlines()
            if re.search(rf"(?<!state\.)\b{re.escape(legacy_name)}\b", line)
            and not re.search(rf"^\s*{re.escape(legacy_name)}\s*:", line)
        ]
        if bare_lines:
            missing.append(legacy_name)
    for name in sorted(required_functions):
        declared = re.search(
            rf"\b(?:async\s+function|function|const|let|var)\s+{re.escape(name)}\b",
            source,
        )
        if not declared:
            missing.append(name)
    assert missing == [], (
        "console-api.js still relies on globals supplied only by the removed inline "
        f"prototype: {missing}"
    )


def test_def_045_console_api_owns_foundational_ui_interactions():
    """Removing the prototype must not silently remove the only event listeners."""

    source = CONSOLE_JS.read_text(encoding="utf-8")
    required_wiring = {
        "new experiment modal": "$('createExperimentBtn').addEventListener",
        "wizard back": "$('wizardBack').addEventListener",
        "close create modal": "$('closeCreateModal').addEventListener",
        "close publish modal": "$('closeModal').addEventListener",
        "back to experiment list": "$('backToHub').addEventListener",
    }
    missing = [label for label, marker in required_wiring.items() if marker not in source]
    nav_owned = source.count("document.querySelectorAll('.nav-item[data-page]')") >= 2 or (
        "closest('.nav-item[data-page]')" in source
    )
    result_tabs_owned = source.count("document.querySelectorAll('[data-result-tab]')") >= 2 or (
        "closest('[data-result-tab]')" in source
    )
    if not nav_owned:
        missing.append("left navigation")
    if not result_tabs_owned:
        missing.append("result tabs")
    assert missing == [], (
        "foundational interactions still existed only in the removed prototype: "
        f"{missing}"
    )


def test_def_045_every_static_id_selector_exists_in_the_neutral_shell():
    """A renderer cannot dereference children erased by shell neutralization."""

    shell = (
        ROOT / "generative_agents" / "web" / "static" / "experiment-console.html"
    ).read_text(encoding="utf-8")
    source = CONSOLE_JS.read_text(encoding="utf-8")
    shell_ids = set(re.findall(r'\bid=["\']([^"\']+)["\']', shell))
    selected_ids = set(re.findall(r"\$\([\"']([^\"']+)[\"']\)", source))
    missing = sorted(selected_ids - shell_ids)
    assert missing == [], (
        "console-api.js dereferences IDs removed from the neutral shell before a "
        f"renderer can recreate them: {missing}"
    )


def test_def_044_homepage_shell_and_images_are_packaged_runtime_assets(tmp_path):
    source = inspect.getsource(web_app_module.create_app)
    homepage = source[
        source.index("def experiment_console") : source.index(
            '@app.get("/api/v1/health",', source.index("def experiment_console")
        )
    ]
    assert ' / "docs" / ' not in homepage
    assert "experiment-console.html" not in homepage or ' / "static" / ' in homepage
    shell = ROOT / "generative_agents" / "web" / "static" / "experiment-console.html"
    assert shell.is_file()
    shell_document = shell.read_text(encoding="utf-8")
    assert 'src="resources/snapshot.png"' not in shell_document
    assert 'src="/static/console/snapshot.png"' in shell_document

    database_url = "sqlite:///" + (tmp_path / "packaged-shell.db").as_posix()
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        image = client.get("/static/console/snapshot.png")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_def_041_to_043_legacy_artifacts_log_and_counts_are_consistent(
    database, tmp_path
):
    source = tmp_path / "legacy"
    checkpoint = source / "checkpoints" / "sample"
    compressed = source / "compressed" / "sample"
    definition = make_builtin_definition(key="legacy", name="legacy")
    agent = definition.agents[0]
    document = {
        "stride": 10,
        "time": "20250213-09:30",
        "step": 1,
        "agent_base": {},
        "agents": {
            agent.name: {
                "coord": [7, 9],
                "currently": "imported",
                "action": {"event": {"address": ["the Ville", "lab"], "describe": "inspect"}},
            }
        },
    }
    checkpoint_path = checkpoint / "simulate-20250213-0930.json"
    _write_json(checkpoint_path, document)
    checkpoint_conversation = checkpoint / "conversation.json"
    _write_json(
        checkpoint_conversation,
        {
            "20250213-09:30": [
                {
                    f"{agent.name} -> {definition.agents[1].name} @ the Ville, lab": [
                        [agent.name, "hello"],
                        [definition.agents[1].name, "hi"],
                    ]
                }
            ]
        },
    )
    compressed_conversation = compressed / "conversation.json"
    _write_json(compressed_conversation, {"compressed": True})
    report_path = compressed / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("immutable legacy report", encoding="utf-8")
    before_checkpoint = checkpoint_path.read_bytes()
    before_checkpoint_conversation = checkpoint_conversation.read_bytes()
    before_compressed_conversation = compressed_conversation.read_bytes()
    before_report = report_path.read_bytes()
    var_dir = tmp_path / "var"

    applied = LegacyImportService(
        database, project_root=tmp_path, var_dir=var_dir
    ).import_runs(apply=True, source_root=source)

    assert applied["created"] == 1 and applied["failed"] == 0, applied
    assert applied["items"][0]["artifact_count"] == 4
    run_id = applied["items"][0]["run_id"]
    with database.session_factory() as session:
        rows = list(
            session.scalars(
                select(RunArtifact)
                .where(RunArtifact.run_id == run_id)
                .order_by(RunArtifact.logical_name)
            )
        )
        summary = session.get(RunResultSummary, run_id)
        step = session.scalar(select(RunStep).where(RunStep.run_id == run_id))
        agent_summary = session.get(RunAgentSummary, (run_id, agent.agent_key))
        attempt = session.scalar(select(RunAttempt).where(RunAttempt.run_id == run_id))
        assert summary.projection_version == "legacy-v1"
        assert summary.capabilities_json == {
            "summary": {"state": "AVAILABLE", "reason": "LEGACY_PARTIAL"},
            "timeline": {"state": "PARTIAL", "reason": "RECONSTRUCTED_FROM_LEGACY"},
            "agents": {"state": "PARTIAL", "reason": "RECONSTRUCTED_FROM_LEGACY"},
            "conversations": {"state": "AVAILABLE", "reason": None},
            "memories": {"state": "UNAVAILABLE", "reason": "LEGACY_MEMORY_HISTORY_INCOMPLETE"},
            "operations": {"state": "PARTIAL", "reason": "LEGACY_MODEL_TRACE_MISSING"},
        }
        assert step.conversation_count == 1 and step.message_count == 2
        assert agent_summary.conversation_count == 1
        assert agent_summary.message_count == 2
        log_path = var_dir / attempt.log_path
        assert log_path.is_file()
        log_document = json.loads(log_path.read_text(encoding="utf-8"))
        assert log_document["event"] == "legacy_import"
        assert log_document["snapshot_complete"] is False
        assert log_document["source_fingerprint"]
    assert {row.logical_name for row in rows} == {
        "checkpoints/simulate-20250213-0930.json",
        "checkpoints/conversation.json",
        "compressed/conversation.json",
        "compressed/report.md",
    }
    for row in rows:
        target = (var_dir / row.relative_path).resolve()
        assert target.is_relative_to((var_dir / "runs" / run_id).resolve())
        assert target.read_bytes() in {
            before_checkpoint,
            before_checkpoint_conversation,
            before_compressed_conversation,
            before_report,
        }
    assert checkpoint_path.read_bytes() == before_checkpoint
    assert checkpoint_conversation.read_bytes() == before_checkpoint_conversation
    assert compressed_conversation.read_bytes() == before_compressed_conversation
    assert report_path.read_bytes() == before_report

    results = ResultQueryService(database)
    views = (
        results.summary(run_id),
        results.timeline(run_id),
        results.agents(run_id),
        results.conversations(run_id),
        results.memories(run_id),
        results.operations(run_id),
    )
    assert all(view["run_id"] == run_id for view in views)


def test_agent_crud_world_asset_and_published_revision_rerun_http(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "crud-rerun.db").as_posix()
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        experiment = client.post(
            "/api/v1/experiments",
            json={"name": "CRUD", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        draft = client.get(f"/api/v1/experiments/{experiment['id']}/draft").json()
        agent = dict(draft["definition"]["agents"][0])
        agent.update({"agent_key": "added-agent", "name": "Added Agent"})
        added = client.put(
            f"/api/v1/experiments/{experiment['id']}/draft/agents/added-agent",
            json={"lock_version": draft["lock_version"], "data": agent},
        )
        assert added.status_code == 200, added.text
        changed = client.patch(
            f"/api/v1/experiments/{experiment['id']}/draft/agents/added-agent",
            json={"lock_version": added.json()["lock_version"], "data": {"currently": "patched"}},
        )
        assert changed.json()["definition"]["agents"][-1]["currently"] == "patched"

        asset = client.post(
            "/api/v1/assets",
            files={"file": ("world.json", b'{"world":"owned"}', "application/json")},
        ).json()
        world = changed.json()["definition"]["world"]
        world["assets"].append(
            {
                "logical_path": "assets/world.json",
                "asset_hash": f"sha256:{asset['sha256']}",
                "media_type": asset["media_type"],
                "size": asset["size_bytes"],
            }
        )
        saved_world = client.put(
            f"/api/v1/experiments/{experiment['id']}/draft/world",
            json={"lock_version": changed.json()["lock_version"], "data": world},
        )
        assert saved_world.status_code == 200, saved_world.text
        deleted = client.request(
            "DELETE",
            f"/api/v1/experiments/{experiment['id']}/draft/agents/added-agent",
            json={"lock_version": saved_world.json()["lock_version"], "data": {}},
        )
        assert deleted.status_code == 200, deleted.text

        # A second experiment supplies a compact publishable definition and
        # proves the public rerun route without relying on a mutable draft.
        database = create_database(database_url)
        try:
            runnable, revision = _publish(database, _definition("rerun-http"))
        finally:
            database.close()
        first = client.post(
            f"/api/v1/experiments/{runnable['id']}/revisions/{revision['id']}/runs"
        )
        assert first.status_code == 202, first.text
        cancelled = client.post(
            f"/api/v1/runs/{first.json()['run_id']}/cancel", json={"force": False}
        )
        assert cancelled.status_code == 200, cancelled.text
        second = client.post(
            f"/api/v1/experiments/{runnable['id']}/revisions/{revision['id']}/runs"
        )
        assert second.status_code == 202, second.text
        assert second.json()["run_id"] != first.json()["run_id"]
        assert second.json()["revision_id"] == first.json()["revision_id"]
