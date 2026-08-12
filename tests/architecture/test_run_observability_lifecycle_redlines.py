"""Adversarial acceptance for Run observability and result lifecycle.

These tests intentionally describe product contracts rather than mirror the
current implementation.  A missing route is only the first failure: once a
route exists the same tests continue into ownership, cursor, versioning and
state-machine assertions.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import importlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from generative_agents import compress
from generative_agents.config import ExperimentDefinition
from generative_agents.config.schema import REQUIRED_PROMPT_KEYS, make_blank_definition
from generative_agents.modules.storage.index import LlamaIndex
from generative_agents.persistence import create_database, upgrade_database
from generative_agents.persistence.models import (
    ArtifactJob,
    ExperimentRevision,
    Run,
    RunAgentStep,
    RunArtifact,
    RunAttempt,
    RunConversation,
    RunMessage,
    RunQueue,
    RunResultSummary,
    RunStep,
)
from generative_agents.runtime.artifact_builder import (
    ArtifactBuilder,
    GENERATOR_VERSION,
)
from generative_agents.runtime.artifact_scheduler import ArtifactSchedulerRepository
from generative_agents.runtime.checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from generative_agents.runtime import worker
from generative_agents.runtime.context import RunPaths, SimulationClock
from generative_agents.runtime.frame_store import FrameStore
from generative_agents.runtime.model_trace import (
    ModelTraceEvent,
    ModelTraceEventType,
    ModelTraceStatus,
    ModelTraceWriter,
)
from generative_agents.runtime.manifest import (
    ManifestConflictError,
    RunManifestStore,
    build_manifest_document,
)
from generative_agents.runtime.results import (
    ActionSnapshot,
    ActivityKind,
    AgentStepResult,
    ConversationMessage,
    ConversationRecord,
    DomainEventRecord,
    MemoryDelta,
    MemoryDeltaKind,
    ScheduleRevisionRecord,
    StepResultBuilder,
)
from generative_agents.runtime.scheduler import LocalRunSchedulerRepository
from generative_agents.runtime.sqlite_result_projector import SqliteResultProjector
from generative_agents.runtime.supervisor import LocalProcessSupervisor
from generative_agents.runtime.trace_projector import ModelTraceProjector
from generative_agents.services import ExperimentService
from generative_agents.services.artifacts import ArtifactService
from generative_agents.services.byte_windows import file_identity, read_utf8_window
from generative_agents.services.checkpoints import CheckpointService
from generative_agents.services.errors import ServiceError
from generative_agents.services.logs import LogService
from generative_agents.services.legacy_import import LegacyImportService
from generative_agents.services.runs import RunService
from generative_agents.web import create_app


ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "generative_agents" / "web" / "static" / "experiment-console.html"
CONSOLE = ROOT / "generative_agents" / "web" / "static" / "console-api.js"
PLAYER = ROOT / "generative_agents" / "web" / "static" / "replay-player.js"
PHASER = ROOT / "generative_agents" / "web" / "static" / "vendor" / "phaser.min.js"


def _create_native_symlink_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    """Use a real OS symlink; release-gate mode turns capability skips into failures."""

    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        message = (
            f"host cannot create {'directory' if target_is_directory else 'file'} "
            f"symlinks: {exc}"
        )
        if os.environ.get("GA_REQUIRE_NATIVE_SYMLINK_TESTS") == "1":
            pytest.fail(message)
        pytest.skip(message)
    assert link.is_symlink(), "release gate requires a native symlink"
    is_junction = getattr(link, "is_junction", None)
    assert not (callable(is_junction) and is_junction()), (
        "a Windows Junction cannot satisfy the native symlink release gate"
    )


@pytest.fixture
def database(tmp_path: Path):
    database_url = "sqlite:///" + (tmp_path / "observability.db").as_posix()
    upgrade_database(database_url)
    value = create_database(database_url)
    yield value
    value.close()


@pytest.fixture
def web_runtime(tmp_path: Path):
    database_url = "sqlite:///" + (tmp_path / "web-observability.db").as_posix()
    var_dir = tmp_path / "var"
    app = create_app(
        database_url=database_url,
        var_dir=str(var_dir),
        supervisor_enabled=False,
    )
    with TestClient(app) as client:
        yield client, app.state.database, var_dir, app


def _definition(key: str) -> ExperimentDefinition:
    definition = make_blank_definition(key=key, name=f"Experiment {key}")
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["models"]["chat"]["resolved_model"] = "Qwen/test-chat"
    payload["models"]["embedding"]["resolved_model"] = "test-embedding"
    payload["world"]["definition"] = {
        "world": "test",
        "tile_size": 16,
        "size": [2, 1],
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


def _publish_run(database, var_dir: Path, key: str):
    experiments = ExperimentService(database)
    definition = _definition(key)
    experiment = experiments.create_experiment(
        name=definition.experiment.name,
        goal=definition.experiment.goal,
        source_type="BLANK",
    )
    draft = experiments.get_draft(experiment["id"])
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["experiment"]["key"] = experiment["experiment_key"]
    draft = experiments.update_draft(
        experiment_id=experiment["id"],
        expected_lock_version=draft["lock_version"],
        definition=ExperimentDefinition.model_validate(payload),
    )
    revision = experiments.publish_draft(
        experiment_id=experiment["id"],
        draft_revision_id=draft["id"],
        expected_lock_version=draft["lock_version"],
    )
    run = RunService(database, var_dir=var_dir).create_from_published(
        experiment["id"], revision["id"]
    )
    return experiment, revision, run


def _claimed_run(database, var_dir: Path, key: str):
    experiment, revision, run = _publish_run(database, var_dir, key)
    claimed = LocalRunSchedulerRepository(database, max_concurrent_runs=8).claim_next()
    assert claimed is not None and claimed.run_id == run["run_id"]
    return experiment, revision, run, claimed


def _step(run_id: str, attempt_id: str, step_no: int):
    builder = StepResultBuilder(
        run_id=UUID(run_id),
        attempt_id=UUID(attempt_id),
        step_no=step_no,
        virtual_time=datetime(2026, 8, 9, tzinfo=timezone.utc)
        + timedelta(minutes=step_no),
    )
    builder.add_agent(
        AgentStepResult(
            agent_key="test-agent",
            from_coord=(0, 0),
            to_coord=(step_no % 2, 0),
            path=((0, 0), (step_no % 2, 0)),
            action=ActionSnapshot(description=f"step {step_no}", emoji="🧭"),
            activity_kind=ActivityKind.MOVING,
            location=("test",),
            currently=f"step {step_no}",
        )
    )
    return builder.freeze()


def _rich_step(run_id: str, attempt_id: str, step_no: int):
    builder = StepResultBuilder(
        run_id=UUID(run_id),
        attempt_id=UUID(attempt_id),
        step_no=step_no,
        virtual_time=datetime(2026, 8, 9, tzinfo=timezone.utc)
        + timedelta(minutes=step_no * 10),
    )
    builder.add_agent(
        AgentStepResult(
            agent_key="test-agent",
            from_coord=(0, 0),
            to_coord=(1, 0),
            path=((0, 0), (1, 0)),
            action=ActionSnapshot(
                description="inspect the laboratory",
                emoji="🔎",
                object_description="old terminal",
            ),
            activity_kind=ActivityKind.MOVING,
            location=("test", "laboratory"),
            currently="checking replay truth",
            schedule_item_id="schedule-item-1",
            path_source="OBSERVED",
        )
    )
    conversation_id = uuid4()
    builder.add_conversation(
        ConversationRecord(
            conversation_id=conversation_id,
            participant_agent_keys=("test-agent", "other-agent"),
            location=("test", "laboratory"),
            messages=(
                ConversationMessage(
                    message_id=uuid4(),
                    sequence=1,
                    speaker_agent_key="test-agent",
                    content="the replay must preserve this conversation",
                ),
            ),
            summary="replay contract discussion",
            ended_reason="COMPLETE",
            duration_minutes=5,
            duration_source="OBSERVED",
        )
    )
    builder.add_memory_delta(
        MemoryDelta(
            event_id=uuid4(),
            sequence=1,
            agent_key="test-agent",
            memory_id="memory-replay-1",
            kind=MemoryDeltaKind.CREATED,
            memory_type="THOUGHT",
            description="remember the verified replay observation",
            poignancy=7.0,
        )
    )
    builder.add_schedule_revision(
        ScheduleRevisionRecord(
            revision_id=uuid4(),
            sequence=1,
            agent_key="test-agent",
            reason="inspection changed the plan",
            source_event_id=None,
            content_hash="a" * 64,
            schedule=(
                {
                    "item_id": "schedule-item-1",
                    "start_minute": 10,
                    "duration_minutes": 20,
                    "description": "inspect the laboratory",
                },
            ),
        )
    )
    builder.add_domain_event(
        DomainEventRecord(
            event_id=uuid4(),
            sequence=1,
            event_type="inspection_completed",
            agent_keys=("test-agent",),
            payload={"object": "old terminal", "result": "safe"},
        )
    )
    return builder.freeze()


def _assert_replay_v2(document: dict, *, run_id: str, source_step: int) -> None:
    """One strict semantic validator used for artifacts and live windows."""

    required = {
        "schema_version",
        "generator_version",
        "source_kind",
        "run_id",
        "revision_id",
        "definition_hash",
        "world",
        "source_step",
        "available_step",
        "stride_minutes",
        "start_time",
        "agents",
        "partial",
        "steps",
    }
    assert required <= document.keys()
    assert document["schema_version"] == 2
    assert document["generator_version"] == "ga-replay-v2"
    assert document["source_kind"] in {"RUN_FRAMES", "RUN_PROJECTION", "LEGACY_ADAPTER"}
    assert document["run_id"] == run_id
    assert document["revision_id"]
    assert len(document["definition_hash"]) == 64
    assert document["source_step"] == source_step
    assert document["available_step"] >= document["source_step"]
    assert isinstance(document["stride_minutes"], int) and document["stride_minutes"] > 0
    assert datetime.fromisoformat(document["start_time"]).utcoffset() is not None
    assert isinstance(document["partial"], bool)
    assert {"world_key", "world_name", "definition", "assets"} <= document["world"].keys()

    agents = {item["agent_key"]: item for item in document["agents"]}
    assert "test-agent" in agents
    assert agents["test-agent"]["display_name"] == "Test Agent"
    assert "sprite_asset" in agents["test-agent"]
    assert agents["test-agent"]["initial_coord"] == [0, 0]

    assert document["steps"]
    for step in document["steps"]:
        assert {
            "step_no",
            "attempt_id",
            "attempt_boundary",
            "virtual_time",
            "checkpoint",
            "agents",
            "conversations",
            "memory_deltas",
            "schedule_revisions",
            "domain_events",
        } <= step.keys()
        assert datetime.fromisoformat(step["virtual_time"]).utcoffset() is not None
    rich = next(item for item in document["steps"] if item["step_no"] == 1)
    assert rich["attempt_id"]
    assert rich["attempt_boundary"] is True
    assert rich["checkpoint"] is True
    agent = next(item for item in rich["agents"] if item["agent_key"] == "test-agent")
    assert agent["path_source"] == "OBSERVED"
    assert agent["path"] == [[0, 0], [1, 0]]
    assert agent["coord"] == [1, 0]
    assert agent["action"]["description"] == "inspect the laboratory"
    assert agent["action"]["emoji"] == "🔎"
    assert agent["address"] == ["test", "laboratory"]
    assert rich["conversations"][0]["messages"][0]["content"].startswith(
        "the replay must preserve"
    )
    assert rich["memory_deltas"][0]["memory_id"] == "memory-replay-1"
    assert rich["memory_deltas"][0]["kind"] == "CREATED"
    assert rich["schedule_revisions"][0]["content_hash"] == "a" * 64
    assert rich["schedule_revisions"][0]["schedule"][0]["item_id"] == "schedule-item-1"
    assert rich["domain_events"][0]["event_type"] == "inspection_completed"


def _write_checkpoint(paths: RunPaths, result, *, retention: int = 3):
    frame = FrameStore(paths).write(result)
    writer = CheckpointBundleWriter(
        paths,
        lambda current: CheckpointSnapshot(
            state={
                "virtual_time": current.virtual_time.isoformat(),
                "rng_state": [3, [1, 2, 3], None],
                "agents": {"test-agent": {"coord": [current.step_no % 2, 0]}},
            },
            conversation={"items": []},
        ),
        retention=retention,
    )
    return writer, writer.write(result, frame)


def _project_replay_step(
    database,
    var_dir: Path,
    run_id: str,
    result,
    *,
    checkpoint: bool = False,
):
    paths = RunPaths.under(var_dir, UUID(run_id))
    frame = FrameStore(paths).write(result)
    with database.session_factory.begin() as session:
        existing = session.get(RunStep, (run_id, result.step_no))
        if existing is None:
            session.add(
                RunStep(
                    run_id=run_id,
                    step_no=result.step_no,
                    attempt_id=str(result.attempt_id),
                    virtual_time=result.virtual_time,
                    frame_path=frame.path.relative_to(var_dir).as_posix(),
                    frame_sha256=frame.sha256,
                    action_count=len(result.agents),
                    movement_count=sum(
                        item.from_coord != item.to_coord for item in result.agents
                    ),
                    conversation_count=len(result.conversations),
                    message_count=sum(
                        len(item.messages) for item in result.conversations
                    ),
                    memory_created_count=0,
                    memory_accessed_count=0,
                    model_logical_calls=len(result.committed_model_usage),
                    model_retry_count=sum(
                        max(0, item.physical_attempts - 1)
                        for item in result.committed_model_usage
                    ),
                    active_agent_count=len(result.agents),
                    checkpoint=checkpoint,
                )
            )
    return frame


def test_def_047_observability_api_exposes_owned_log_protocols(web_runtime):
    """ROL-LOG-001/002, ROL-TRACE-001, ROL-SYNC-001 route floor."""

    _client, _database, _var_dir, app = web_runtime
    paths = app.openapi()["paths"]
    required = {
        "/api/v1/runs/{run_id}/attempts",
        "/api/v1/runs/{run_id}/attempts/{attempt_id}/log",
        "/api/v1/runs/{run_id}/attempts/{attempt_id}/log/stream",
        "/api/v1/runs/{run_id}/attempts/{attempt_id}/log/download",
        "/api/v1/runs/{run_id}/artifact-jobs/{job_id}/log",
        "/api/v1/runs/{run_id}/artifact-jobs/{job_id}/log/stream",
        "/api/v1/runs/{run_id}/artifact-jobs/{job_id}/log/download",
        "/api/v1/runs/{run_id}/model-traces",
        "/api/v1/runs/{run_id}/model-traces/{trace_id}",
    }
    assert required <= set(paths), f"missing observability routes: {sorted(required - set(paths))}"


def test_def_047_log_byte_cursor_round_trips_split_utf8_without_loss(web_runtime):
    """ROL-LOG-001 byte cursors must never persist replacement characters."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(database, var_dir, "log-utf8")
    content = "INFO alpha\nERROR 中🙂界\nINFO omega\n"
    log_path = var_dir / claimed.log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_bytes(content.encode("utf-8"))

    cursor = 0
    chunks: list[str] = []
    observed_cursors = [cursor]
    consumed = 0
    for _ in range(50):
        response = client.get(
            f"/api/v1/runs/{run['run_id']}/attempts/{claimed.attempt_id}/log",
            params={"cursor": cursor, "limit_bytes": 17},
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["run_id"] == run["run_id"]
        assert page["attempt_id"] == claimed.attempt_id
        assert page["cursor"] == cursor
        assert "�" not in page["content"]
        chunks.append(page["content"])
        consumed += len(page["content"].encode("utf-8"))
        next_cursor = page["next_cursor"]
        if page.get("eof"):
            assert next_cursor == consumed
            assert consumed == len(content.encode("utf-8"))
            break
        assert next_cursor > cursor
        assert next_cursor == consumed, "cursor must count consumed source bytes"
        cursor = next_cursor
        observed_cursors.append(cursor)
    else:
        pytest.fail("log cursor did not terminate")

    assert "".join(chunks) == content
    assert observed_cursors == sorted(set(observed_cursors))


def test_def_047_log_ownership_rejects_cross_run_traversal_and_symlink(web_runtime):
    """ROL-LOG-002 must bind the DB attempt, Run and physical log root."""

    client, database, var_dir, _app = web_runtime
    _e1, _r1, run_a, attempt_a = _claimed_run(database, var_dir, "log-owner-a")
    _e2, _r2, run_b, attempt_b = _claimed_run(database, var_dir, "log-owner-b")
    for claimed, sentinel in ((attempt_a, "ONLY-A"), (attempt_b, "ONLY-B")):
        target = var_dir / claimed.log_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(sentinel, encoding="utf-8")

    crossed = client.get(
        f"/api/v1/runs/{run_a['run_id']}/attempts/{attempt_b.attempt_id}/log"
    )
    assert crossed.status_code == 404
    assert crossed.json().get("error", {}).get("code") == "ATTEMPT_NOT_FOUND"
    assert "ONLY-B" not in crossed.text

    secret = var_dir / "outside-secret.log"
    secret.write_text("DO-NOT-LEAK", encoding="utf-8")
    with database.session_factory.begin() as session:
        row = session.get(RunAttempt, attempt_a.attempt_id)
        row.log_path = "../outside-secret.log"
    traversal = client.get(
        f"/api/v1/runs/{run_a['run_id']}/attempts/{attempt_a.attempt_id}/log"
    )
    assert traversal.status_code == 500
    assert traversal.json()["error"]["code"] == "RUN_STORAGE_INTEGRITY_ERROR"
    assert str(secret) not in traversal.text and "DO-NOT-LEAK" not in traversal.text


def test_def_047_log_service_reports_terminal_truncated_rotated_and_missing(
    database, tmp_path: Path
):
    """ROL-LOG-001/002 stable file lifecycle is independent of HTTP wiring."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "log-file-lifecycle"
    )
    target = var_dir / claimed.log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("INFO one\nERROR two\n", encoding="utf-8")
    service = LogService(database, var_dir=var_dir)
    first = service.read_attempt_log(
        run["run_id"], claimed.attempt_id, cursor=0, limit_bytes=10
    )
    assert first["terminal"] is False
    assert first["file_id"] and first["next_cursor"] == len(first["content"].encode("utf-8"))

    with database.session_factory.begin() as session:
        attempt = session.get(RunAttempt, claimed.attempt_id)
        attempt.status = "ENDED"
        attempt.ended_at = datetime.now(timezone.utc)
        attempt.end_step = 0
    terminal = service.read_attempt_log(
        run["run_id"], claimed.attempt_id, cursor=0, limit_bytes=10
    )
    assert terminal["terminal"] is True

    with pytest.raises(ServiceError) as truncated:
        service.read_attempt_log(
            run["run_id"], claimed.attempt_id, cursor=target.stat().st_size + 1
        )
    assert (truncated.value.status_code, truncated.value.code) == (
        409,
        "ATTEMPT_LOG_TRUNCATED",
    )
    assert truncated.value.details["reset_cursor"] == 0

    original_file_id = first["file_id"]
    target.unlink()
    target.write_text("INFO rotated\n", encoding="utf-8")
    with pytest.raises(ServiceError) as rotated:
        service.read_attempt_log(
            run["run_id"],
            claimed.attempt_id,
            cursor=0,
            file_id=original_file_id,
        )
    assert (rotated.value.status_code, rotated.value.code) == (
        409,
        "ATTEMPT_LOG_ROTATED",
    )
    target.unlink()
    with pytest.raises(ServiceError) as missing:
        service.read_attempt_log(run["run_id"], claimed.attempt_id)
    assert (missing.value.status_code, missing.value.code) == (
        410,
        "ATTEMPT_LOG_MISSING",
    )


def test_def_047_file_id_survives_append_but_detects_replacement(
    database, tmp_path: Path
):
    """ROL-LOG-001/SYNC-001 old SSE cursors must survive normal append."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "log-append-identity"
    )
    target = var_dir / claimed.log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    original = "INFO first\n"
    appended = "ERROR 新增🙂\n"
    target.write_bytes(original.encode("utf-8"))
    service = LogService(database, var_dir=var_dir)
    first = service.read_attempt_log(run["run_id"], claimed.attempt_id)
    assert first["eof"] is True

    with target.open("ab") as handle:
        handle.write(appended.encode("utf-8"))
    continuation = service.read_attempt_log(
        run["run_id"],
        claimed.attempt_id,
        cursor=first["next_cursor"],
        file_id=first["file_id"],
    )
    assert continuation["file_id"] == first["file_id"]
    assert continuation["content"] == appended
    assert continuation["next_cursor"] == len((original + appended).encode("utf-8"))

    previous_id = continuation["file_id"]
    target.unlink()
    target.write_bytes(b"INFO replacement\n")
    with pytest.raises(ServiceError) as rotated:
        service.read_attempt_log(
            run["run_id"], claimed.attempt_id, cursor=0, file_id=previous_id
        )
    assert (rotated.value.status_code, rotated.value.code) == (
        409,
        "ATTEMPT_LOG_ROTATED",
    )
    assert rotated.value.details["reset_cursor"] == 0


def test_def_047_file_identity_does_not_depend_on_append_mutable_timestamps(
    monkeypatch, tmp_path: Path
):
    """Linux ctime/mtime changes on append and therefore cannot define rotation."""

    path = tmp_path / "identity.log"
    path.write_text("x", encoding="utf-8")
    stats = iter(
        [
            type("Stat", (), {"st_dev": 7, "st_ino": 11, "st_ctime_ns": 100, "st_size": 1, "st_mtime_ns": 100})(),
            type("Stat", (), {"st_dev": 7, "st_ino": 11, "st_ctime_ns": 200, "st_size": 2, "st_mtime_ns": 200})(),
        ]
    )
    real_stat = Path.stat

    def fake_stat(target, *args, **kwargs):
        return next(stats) if target == path else real_stat(target, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    before = file_identity(path)[0]
    after = file_identity(path)[0]
    assert before == after, "append-mutable ctime/mtime must not rotate file_id"


def test_def_047_attempt_sse_reconnects_by_file_identity_and_byte_offset(
    web_runtime,
):
    """ROL-LOG-001/SYNC-001 EventSource continuation cannot be offset-only."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "attempt-sse-reconnect"
    )
    target = var_dir / claimed.log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    original = "INFO before reconnect\n"
    appended = "ERROR after reconnect 中🙂\n"
    target.write_bytes(original.encode("utf-8"))
    first = LogService(database, var_dir=var_dir).read_attempt_log(
        run["run_id"], claimed.attempt_id
    )
    assert first["eof"] is True
    assert first["next_cursor"] == len(original.encode("utf-8"))

    with target.open("ab") as handle:
        handle.write(appended.encode("utf-8"))
    with database.session_factory.begin() as session:
        attempt = session.get(RunAttempt, claimed.attempt_id)
        attempt.status = "ENDED"
        attempt.end_step = 0
        attempt.ended_at = datetime.now(timezone.utc)

    event_id = f'{first["file_id"]}:{first["next_cursor"]}'
    response = client.get(
        f"/api/v1/runs/{run['run_id']}/attempts/{claimed.attempt_id}/log/stream",
        headers={"Last-Event-ID": event_id},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert original not in response.text
    assert appended.strip() in response.text
    final_cursor = len((original + appended).encode("utf-8"))
    assert f"id: {first['file_id']}:{final_cursor}" in response.text
    assert "event: log" in response.text
    assert "event: eof" in response.text


def test_def_047_attempt_sse_reconnect_rejects_replaced_file(web_runtime):
    """ROL-LOG-001/SYNC-001 reconnect after rotation must explicitly reset."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "attempt-sse-rotation"
    )
    target = var_dir / claimed.log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("INFO old stream\n", encoding="utf-8")
    first = LogService(database, var_dir=var_dir).read_attempt_log(
        run["run_id"], claimed.attempt_id
    )
    old_event_id = f'{first["file_id"]}:{first["next_cursor"]}'

    target.unlink()
    target.write_text("INFO replacement stream\n", encoding="utf-8")
    response = client.get(
        f"/api/v1/runs/{run['run_id']}/attempts/{claimed.attempt_id}/log/stream",
        headers={"Last-Event-ID": old_event_id},
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "ATTEMPT_LOG_ROTATED"
    assert error["details"]["reset_cursor"] == 0
    assert error["details"]["file_id"] != first["file_id"]
    assert "replacement stream" not in response.text


def test_def_047_attempt_sse_emits_heartbeat_then_closes_on_terminal(
    web_runtime, monkeypatch
):
    """ROL-LOG-001 streams stay observable while idle and close at terminal EOF."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "attempt-sse-heartbeat"
    )
    target = var_dir / claimed.log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")

    app_module = importlib.import_module("generative_agents.web.app")
    calls = 0

    async def controlled_sleep(_seconds: float):
        nonlocal calls
        calls += 1
        if calls == 20:
            with database.session_factory.begin() as session:
                attempt = session.get(RunAttempt, claimed.attempt_id)
                attempt.status = "ENDED"
                attempt.end_step = 0
                attempt.ended_at = datetime.now(timezone.utc)
        await asyncio.sleep(0)

    monkeypatch.setattr(
        app_module,
        "asyncio",
        SimpleNamespace(to_thread=asyncio.to_thread, sleep=controlled_sleep),
    )
    response = client.get(
        f"/api/v1/runs/{run['run_id']}/attempts/{claimed.attempt_id}/log/stream"
    )
    assert response.status_code == 200, response.text
    assert calls >= 20
    assert ": keepalive" in response.text
    assert "event: eof" in response.text
    assert response.text.index(": keepalive") < response.text.index("event: eof")


def test_def_047_artifact_log_stream_is_run_owned_and_terminal(web_runtime):
    """ROL-LOG-002 applies the same owned SSE/download protocol to job logs."""

    client, database, var_dir, _app = web_runtime
    _e1, _r1, run_a = _publish_run(database, var_dir, "artifact-log-a")
    _e2, _r2, run_b = _publish_run(database, var_dir, "artifact-log-b")
    job = ArtifactService(database, var_dir=var_dir).create_job(
        run_a["run_id"], job_type="BUILD_REPLAY"
    )
    claimed_job = ArtifactSchedulerRepository(database).claim_next()
    assert claimed_job is not None and claimed_job.job_id == job["job_id"]
    with database.session_factory.begin() as session:
        row = session.get(ArtifactJob, job["job_id"])
        assert row.log_path
        row.status = "SUCCEEDED"
        row.finished_at = datetime.now(timezone.utc)
        log_path = row.log_path
    target = var_dir / log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("INFO artifact complete 中\n", encoding="utf-8")

    crossed = client.get(
        f"/api/v1/runs/{run_b['run_id']}/artifact-jobs/{job['job_id']}/log/stream"
    )
    assert crossed.status_code == 404
    assert crossed.json()["error"]["code"] == "ARTIFACT_JOB_NOT_FOUND"
    assert "artifact complete" not in crossed.text

    streamed = client.get(
        f"/api/v1/runs/{run_a['run_id']}/artifact-jobs/{job['job_id']}/log/stream"
    )
    assert streamed.status_code == 200, streamed.text
    assert "artifact complete 中" in streamed.text
    assert "event: log" in streamed.text and "event: eof" in streamed.text
    downloaded = client.get(
        f"/api/v1/runs/{run_a['run_id']}/artifact-jobs/{job['job_id']}/log/download"
    )
    assert downloaded.status_code == 200
    assert downloaded.content == target.read_bytes()


def test_def_056_one_utf8_log_line_spanning_pages_is_one_record(
    database, tmp_path: Path
):
    """ROL-LOG-001 raw byte pages must not fragment a logical record."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "log-record-long-line"
    )
    target = var_dir / claimed.log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    line = "ERROR " + "中🙂" * 40 + " terminal message"
    target.write_bytes((line + "\n").encode("utf-8"))
    with database.session_factory.begin() as session:
        attempt = session.get(RunAttempt, claimed.attempt_id)
        attempt.status = "ENDED"
        attempt.end_step = 0
        attempt.ended_at = datetime.now(timezone.utc)

    service = LogService(database, var_dir=var_dir)
    cursor = 0
    records = []
    contents = []
    for _ in range(100):
        page = service.read_attempt_log(
            run["run_id"], claimed.attempt_id, cursor=cursor, limit_bytes=17
        )
        records.extend(page["records"])
        contents.append(page["content"])
        cursor = page["next_cursor"]
        if page["eof"]:
            break
    assert "".join(contents) == line + "\n", "raw byte cursor remains lossless"
    assert records == [{"level": "ERROR", "message": line}], (
        "a line split by transport windows must be emitted once, not as fragments"
    )


def test_def_056_tail_starting_mid_line_discards_only_the_leading_fragment(
    database, tmp_path: Path
):
    """ROL-LOG-001 initial tail has an explicit mid-line policy."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "log-record-tail-midline"
    )
    target = var_dir / claimed.log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "INFO ignored " + "x" * 300 + "\nERROR keep this record\n",
        encoding="utf-8",
    )
    page = LogService(database, var_dir=var_dir).read_attempt_log(
        run["run_id"], claimed.attempt_id, tail=True, limit_bytes=40
    )
    assert page["cursor"] > 0
    assert page["records"] == [
        {"level": "ERROR", "message": "ERROR keep this record"}
    ]


def test_def_056_terminal_line_without_newline_is_emitted_exactly_once(
    database, tmp_path: Path
):
    """ROL-LOG-001 terminal EOF flushes one pending line, once."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "log-record-terminal-no-newline"
    )
    target = var_dir / claimed.log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    line = "WARNING final " + "界" * 40
    target.write_text(line, encoding="utf-8")
    with database.session_factory.begin() as session:
        attempt = session.get(RunAttempt, claimed.attempt_id)
        attempt.status = "ENDED"
        attempt.end_step = 0
        attempt.ended_at = datetime.now(timezone.utc)

    service = LogService(database, var_dir=var_dir)
    cursor = 0
    records = []
    for _ in range(100):
        page = service.read_attempt_log(
            run["run_id"], claimed.attempt_id, cursor=cursor, limit_bytes=13
        )
        records.extend(page["records"])
        cursor = page["next_cursor"]
        if page["eof"]:
            assert page["terminal"] is True
            break
    assert records == [{"level": "WARNING", "message": line}]


def test_def_059_sse_drains_non_eof_backlog_without_per_page_sleep(
    web_runtime, monkeypatch
):
    """ROL-LOG-001/SYNC-001 existing backlog is drained before tail polling."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "log-sse-backlog-drain"
    )
    target = var_dir / claimed.log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("INFO backlog row\n" * 12_000, encoding="utf-8")
    with database.session_factory.begin() as session:
        attempt = session.get(RunAttempt, claimed.attempt_id)
        attempt.status = "ENDED"
        attempt.end_step = 0
        attempt.ended_at = datetime.now(timezone.utc)

    sleep_calls: list[float] = []

    async def forbidden_backlog_sleep(seconds: float):
        sleep_calls.append(seconds)
        await asyncio.sleep(0)

    app_module = importlib.import_module("generative_agents.web.app")
    monkeypatch.setattr(
        app_module,
        "asyncio",
        SimpleNamespace(to_thread=asyncio.to_thread, sleep=forbidden_backlog_sleep),
    )
    response = client.get(
        f"/api/v1/runs/{run['run_id']}/attempts/{claimed.attempt_id}/log/stream"
    )
    assert response.status_code == 200, response.text
    assert response.text.count("event: log") >= 3
    assert response.text.count("event: eof") == 1
    assert sleep_calls == [], (
        "SSE must immediately request the next byte page while eof=false; "
        "poll sleep is only for a caught-up appendable log"
    )


@pytest.mark.parametrize(
    "corrupt_path",
    ["../outside.log", "/absolute/outside.log", "C:/outside.log", "runs\\bad.log"],
)
def test_def_047_db_owned_log_path_integrity_error_is_stable(
    database, tmp_path: Path, corrupt_path: str
):
    """ROL-LOG-002 product decision: persisted path corruption is a 500 invariant."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "log-integrity-" + str(abs(hash(corrupt_path)))
    )
    with database.session_factory.begin() as session:
        session.get(RunAttempt, claimed.attempt_id).log_path = corrupt_path

    with pytest.raises(ServiceError) as rejected:
        LogService(database, var_dir=var_dir).read_attempt_log(
            run["run_id"], claimed.attempt_id
        )
    assert (rejected.value.status_code, rejected.value.code) == (
        500,
        "RUN_STORAGE_INTEGRITY_ERROR",
    )
    assert corrupt_path not in rejected.value.message


def test_def_047_log_service_rejects_a_real_symlink_chain(database, tmp_path: Path):
    """ROL-LOG-002 resolves every path component, not only the final string."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "log-real-symlink"
    )
    outside = tmp_path / "outside.log"
    outside.write_text("SECRET-OUTSIDE", encoding="utf-8")
    target = var_dir / claimed.log_path
    target.parent.mkdir(parents=True, exist_ok=True)
    _create_native_symlink_or_skip(target, outside)

    with pytest.raises(ServiceError) as rejected:
        LogService(database, var_dir=var_dir).read_attempt_log(
            run["run_id"], claimed.attempt_id
        )
    assert (rejected.value.status_code, rejected.value.code) == (
        500,
        "RUN_STORAGE_INTEGRITY_ERROR",
    )
    assert str(outside) not in rejected.value.message


def test_def_047_model_trace_detail_filters_and_redacts_payloads(database, tmp_path: Path):
    """ROL-TRACE-001 detail remains useful without leaking credentials."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "trace-detail"
    )
    run_id = UUID(run["run_id"])
    attempt_id = UUID(claimed.attempt_id)
    writer = ModelTraceWriter(
        RunPaths.under(var_dir, run_id),
        run_id=run_id,
        attempt_id=attempt_id,
        attempt_no=claimed.attempt_no,
        capture_payloads=True,
    )
    at = datetime(2026, 8, 9, tzinfo=timezone.utc)
    writer.append(
        ModelTraceEvent(
            event_type=ModelTraceEventType.PHYSICAL_ATTEMPT,
            run_id=run_id,
            attempt_id=attempt_id,
            call_id=uuid4(),
            step_no=1,
            agent_key="test-agent",
            purpose="chat",
            prompt_key="generate_chat",
            provider="vllm",
            resolved_model="test-model",
            started_at=at,
            ended_at=at + timedelta(milliseconds=125),
            latency_ms=125,
            attempt_no=2,
            status=ModelTraceStatus.FAILED,
            error_code="UPSTREAM_TIMEOUT",
            error_summary="Authorization: Bearer super-secret-token",
            payload={
                "authorization": "Bearer super-secret-token",
                "nested": {"api_key": "sk-never-return-this"},
                "prompt": "safe prompt body",
            },
        )
    )
    relative = writer.path.relative_to(var_dir).as_posix()
    ModelTraceProjector(database, var_dir=var_dir).project(
        run_id=run["run_id"],
        attempt_id=claimed.attempt_id,
        relative_path=relative,
    )
    service = LogService(database, var_dir=var_dir)
    page = service.model_traces(
        run["run_id"],
        claimed.attempt_id,
        purpose="chat",
        status="FAILED",
    )
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["resolved_model"] == "test-model"
    assert item["attempt_no"] == 2 and item["retry"] is True
    assert item["latency_ms"] == 125 and item["payload_available"] is True
    assert "payload" not in item
    assert "super-secret-token" not in json.dumps(item)

    payload = service.model_trace_payload(
        run["run_id"], claimed.attempt_id, item["event_seq"], limit_bytes=64
    )
    combined = payload["content"]
    cursor = payload["next_cursor"]
    while cursor is not None:
        part = service.model_trace_payload(
            run["run_id"],
            claimed.attempt_id,
            item["event_seq"],
            cursor=cursor,
            limit_bytes=64,
        )
        combined += part["content"]
        cursor = part["next_cursor"]
    assert "[REDACTED]" in combined
    assert "super-secret-token" not in combined
    assert "sk-never-return-this" not in combined
    assert "safe prompt body" in combined


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


def test_def_048_log_stream_error_cannot_enter_an_automatic_reconnect_loop():
    """ROL-LOG-002/SYNC-001 rotation/reset must stop the stale EventSource."""

    script = CONSOLE.read_text(encoding="utf-8")
    start = script.index("source.addEventListener('error'", script.index("function startLogStream"))
    end = script.index("\n  }", start)
    handler = script[start:end]
    assert "ATTEMPT_LOG_ROTATED" in handler or "reset_cursor" in handler
    assert "closeLogStream()" in handler
    assert "state.logFileId = null" in handler and "state.logCursor = 0" in handler


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


def test_def_058_trace_payload_over_16k_pages_losslessly_and_stays_run_owned(
    web_runtime,
):
    """ROL-TRACE-001 backend detail is a byte-window fact the UI can continue."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "trace-payload-large"
    )
    run_id = UUID(run["run_id"])
    attempt_id = UUID(claimed.attempt_id)
    large_value = "中🙂" * 10_000
    writer = ModelTraceWriter(
        RunPaths.under(var_dir, run_id),
        run_id=run_id,
        attempt_id=attempt_id,
        attempt_no=claimed.attempt_no,
        capture_payloads=True,
    )
    at = datetime(2026, 8, 9, tzinfo=timezone.utc)
    writer.append(
        ModelTraceEvent(
            event_type=ModelTraceEventType.PHYSICAL_ATTEMPT,
            run_id=run_id,
            attempt_id=attempt_id,
            call_id=uuid4(),
            step_no=1,
            agent_key="test-agent",
            purpose="chat",
            prompt_key="generate_chat",
            provider="vllm",
            resolved_model="test-model",
            started_at=at,
            ended_at=at + timedelta(milliseconds=20),
            latency_ms=20,
            attempt_no=1,
            status=ModelTraceStatus.SUCCEEDED,
            payload={
                "text": large_value,
                "authorization": "Bearer must-never-leak",
            },
        )
    )
    ModelTraceProjector(database, var_dir=var_dir).project(
        run_id=run["run_id"],
        attempt_id=claimed.attempt_id,
        relative_path=writer.path.relative_to(var_dir).as_posix(),
    )
    listed = client.get(
        f"/api/v1/runs/{run['run_id']}/model-traces",
        params={"attempt_id": claimed.attempt_id},
    )
    assert listed.status_code == 200, listed.text
    trace_id = listed.json()["items"][0]["trace_id"]

    cursor = 0
    chunks = []
    file_id = None
    for _ in range(20):
        detail = client.get(
            f"/api/v1/runs/{run['run_id']}/model-traces/{trace_id}",
            params={"cursor": cursor, "limit_bytes": 16_384},
        )
        assert detail.status_code == 200, detail.text
        page = detail.json()
        assert page["trace_id"] == trace_id
        assert page["run_id"] == run["run_id"]
        file_id = file_id or page["file_id"]
        assert page["file_id"] == file_id
        chunks.append(page["content"])
        if page["next_cursor"] is None:
            assert page["eof"] is True
            break
        assert page["next_cursor"] > cursor
        cursor = page["next_cursor"]
    else:
        pytest.fail("trace detail byte cursor did not terminate")
    payload = json.loads("".join(chunks))
    assert payload["text"] == large_value
    assert payload["authorization"] == "[REDACTED]"
    assert "must-never-leak" not in "".join(chunks)

    _e2, _r2, other = _publish_run(database, var_dir, "trace-payload-other")
    crossed = client.get(
        f"/api/v1/runs/{other['run_id']}/model-traces/{trace_id}"
    )
    assert crossed.status_code == 404
    assert crossed.json()["error"]["code"] == "ATTEMPT_NOT_FOUND"


def test_def_058_trace_append_after_eof_uses_the_previous_byte_cursor(web_runtime):
    """ROL-TRACE-001/SYNC-001 append refresh reads only facts after durable EOF."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "trace-append-after-eof"
    )
    run_id = UUID(run["run_id"])
    attempt_id = UUID(claimed.attempt_id)
    writer = ModelTraceWriter(
        RunPaths.under(var_dir, run_id),
        run_id=run_id,
        attempt_id=attempt_id,
        attempt_no=claimed.attempt_no,
        capture_payloads=True,
    )
    at = datetime(2026, 8, 9, tzinfo=timezone.utc)

    def append_trace(step_no: int) -> None:
        writer.append(
            ModelTraceEvent(
                event_type=ModelTraceEventType.PHYSICAL_ATTEMPT,
                run_id=run_id,
                attempt_id=attempt_id,
                call_id=uuid4(),
                step_no=step_no,
                agent_key="test-agent",
                purpose="chat",
                prompt_key="generate_chat",
                provider="vllm",
                resolved_model="test-model",
                started_at=at + timedelta(minutes=step_no),
                ended_at=at + timedelta(minutes=step_no, milliseconds=5),
                latency_ms=5,
                attempt_no=1,
                status=ModelTraceStatus.SUCCEEDED,
                payload={"step": step_no},
            )
        )
        ModelTraceProjector(database, var_dir=var_dir).project(
            run_id=run["run_id"],
            attempt_id=claimed.attempt_id,
            relative_path=writer.path.relative_to(var_dir).as_posix(),
        )

    append_trace(1)
    first = client.get(
        f"/api/v1/runs/{run['run_id']}/model-traces",
        params={"attempt_id": claimed.attempt_id, "cursor": 0, "limit": 200},
    )
    assert first.status_code == 200, first.text
    first_page = first.json()
    assert [item["event_seq"] for item in first_page["items"]] == [1]
    assert first_page["eof"] is True
    assert isinstance(first_page["next_cursor"], int) and first_page["next_cursor"] > 0
    old_eof = first_page["next_cursor"]

    append_trace(2)
    continuation = client.get(
        f"/api/v1/runs/{run['run_id']}/model-traces",
        params={
            "attempt_id": claimed.attempt_id,
            "cursor": old_eof,
            "limit": 200,
        },
    )
    assert continuation.status_code == 200, continuation.text
    next_page = continuation.json()
    assert [item["event_seq"] for item in next_page["items"]] == [2]
    assert next_page["next_cursor"] > old_eof and next_page["eof"] is True


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


@pytest.mark.parametrize("status", ["PAUSED", "FAILED", "INTERRUPTED"])
def test_def_049_resume_requires_a_verified_authorized_checkpoint(
    database, tmp_path: Path, status: str
):
    """ROL-REC-001 forbids queueing a fresh run disguised as recovery."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run = _publish_run(database, var_dir, f"resume-{status.lower()}")
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.status = status
        row.recoverable_step = 0
        row.completed_steps = 4
        session.query(RunQueue).filter_by(run_id=row.id).delete()

    with pytest.raises(ServiceError) as exc:
        RunService(database, var_dir=var_dir).resume_paused(run["run_id"])
    assert exc.value.code == "RUN_NOT_RECOVERABLE"
    with database.session_factory() as session:
        assert session.get(Run, run["run_id"]).status == status


@pytest.mark.parametrize("materialize_invalid", [False, True])
def test_def_049_positive_recoverable_projection_is_not_enough(
    database, tmp_path: Path, materialize_invalid: bool
):
    """ROL-REC-001 validates the exact DB-authorized physical bundle before rewind."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run = _publish_run(
        database, var_dir, f"resume-invalid-{int(materialize_invalid)}"
    )
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.status = "INTERRUPTED"
        row.recoverable_step = 1
        row.completed_steps = 4
        session.query(RunQueue).filter_by(run_id=row.id).delete()
    if materialize_invalid:
        invalid = var_dir / "runs" / run["run_id"] / "checkpoints" / "step-000001"
        invalid.mkdir(parents=True)
        (invalid / "bundle.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ServiceError) as exc:
        RunService(database, var_dir=var_dir).resume_paused(run["run_id"])
    assert (exc.value.status_code, exc.value.code) == (409, "RUN_NOT_RECOVERABLE")
    with database.session_factory() as session:
        row = session.get(Run, run["run_id"])
        assert row.status == "INTERRUPTED"
        assert row.completed_steps == 4, "validation must happen before projection rewind"
        assert session.scalar(select(RunQueue).where(RunQueue.run_id == row.id)) is None


def test_def_050_checkpoint_api_distinguishes_verified_invalid_and_recoverable(web_runtime):
    """ROL-CHK-001 only promotes a fully validated DB-authorized bundle."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(database, var_dir, "checkpoint-list")
    paths = RunPaths.under(var_dir, UUID(run["run_id"]))
    _writer, valid = _write_checkpoint(paths, _step(run["run_id"], claimed.attempt_id, 1))
    invalid = paths.checkpoints / "step-000002"
    invalid.mkdir()
    (invalid / "bundle.json").write_text("{}", encoding="utf-8")
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.completed_steps = 2
        row.recoverable_step = 1

    response = client.get(f"/api/v1/runs/{run['run_id']}/checkpoints")
    assert response.status_code == 200, response.text
    document = response.json()
    by_step = {item["step_no"]: item for item in document["items"]}
    assert by_step[1]["status"] == "RECOVERABLE"
    assert by_step[1]["validated"] is True
    assert by_step[1]["bundle_sha256"]
    assert by_step[2]["status"] == "INVALID"
    assert by_step[2]["validated"] is False
    assert str(valid) not in json.dumps(document), "API must not expose server paths"


def test_def_050_checkpoint_list_separates_pruned_retained_and_recoverable(
    database, tmp_path: Path
):
    """ROL-CHK-001 historical retention is distinct from the active boundary."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "checkpoint-retention-states"
    )
    paths = RunPaths.under(var_dir, UUID(run["run_id"]))
    results = [_step(run["run_id"], claimed.attempt_id, number) for number in (1, 2, 3)]
    for result in results:
        _write_checkpoint(paths, result, retention=2)
    assert not (paths.checkpoints / "step-000001").exists()
    frame = FrameStore(paths).write(results[0])
    with database.session_factory.begin() as session:
        session.add(
            RunStep(
                run_id=run["run_id"],
                step_no=1,
                attempt_id=claimed.attempt_id,
                virtual_time=results[0].virtual_time,
                frame_path=frame.path.relative_to(var_dir).as_posix(),
                frame_sha256=frame.sha256,
                action_count=1,
                movement_count=1,
                conversation_count=0,
                message_count=0,
                memory_created_count=0,
                memory_accessed_count=0,
                model_logical_calls=0,
                model_retry_count=0,
                active_agent_count=1,
                checkpoint=True,
            )
        )
        row = session.get(Run, run["run_id"])
        row.status = "PAUSED"
        row.completed_steps = 3
        row.recoverable_step = 3
        row.slot_no = None
        row.current_attempt_id = None
        row.pid = None
        row.pid_create_time = None

    document = CheckpointService(database, var_dir=var_dir).list_checkpoints(run["run_id"])
    by_step = {item["step_no"]: item for item in document["items"]}
    assert by_step[1]["status"] == "PRUNED"
    assert by_step[1]["database_marker"] is True
    assert by_step[1]["retained"] is False and by_step[1]["validated"] is False
    assert by_step[2]["status"] == "RETAINED"
    assert by_step[2]["retained"] is True and by_step[2]["validated"] is True
    assert by_step[3]["status"] == "RECOVERABLE"
    assert by_step[3]["resumable"] is True and document["can_resume"] is True


def test_def_050_missing_db_authorized_checkpoint_is_invalid_not_pruned(
    database, tmp_path: Path
):
    """ROL-CHK-001 PRUNED is historical retention, never the active recovery boundary."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run = _publish_run(
        database, var_dir, "checkpoint-missing-authority"
    )
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.status = "PAUSED"
        row.recoverable_step = 1
        row.completed_steps = 1
        row.slot_no = None
        row.current_attempt_id = None
        row.pid = None
        row.pid_create_time = None
        session.query(RunQueue).filter_by(run_id=row.id).delete()
    document = CheckpointService(database, var_dir=var_dir).list_checkpoints(run["run_id"])
    assert document["can_resume"] is False
    assert len(document["items"]) == 1
    item = document["items"][0]
    assert item["step_no"] == 1
    assert item["status"] == "INVALID"
    assert item["validated"] is False and item["resumable"] is False
    assert item["validation"]["code"] == "CHECKPOINT_AUTHORIZED_BUNDLE_MISSING"


def test_def_050_checkpoint_detail_is_bounded_structured_and_run_owned(
    database, tmp_path: Path
):
    """ROL-CHK-002 exposes summaries and whitelisted raw sections, not arbitrary files."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "checkpoint-detail"
    )
    paths = RunPaths.under(var_dir, UUID(run["run_id"]))
    result = _step(run["run_id"], claimed.attempt_id, 1)
    frame = FrameStore(paths).write(result)

    def export_storage(destination: Path):
        (destination / "docstore.json").write_text(
            json.dumps({"embedding": [0.1] * 200, "safe": "metadata"}),
            encoding="utf-8",
        )
        (destination / "index_store.json").write_text("{}", encoding="utf-8")

    writer = CheckpointBundleWriter(
        paths,
        lambda current: CheckpointSnapshot(
            state={
                "virtual_time": current.virtual_time.isoformat(),
                "rng_state": [3, [1, 2, 3], None],
                "agents": {
                    "test-agent": {
                        "coord": [1, 0],
                        "currently": "checking continuity",
                        "action": {
                            "event": "inspect",
                            "address": "test",
                            "emoji": "🔎",
                        },
                        "schedule": [{"start": 0, "duration": 10}],
                        "embedding": [0.1] * 200,
                    }
                },
            },
            conversation={
                "items": [
                    {
                        "participants": ["test-agent", "other-agent"],
                        "messages": [{"speaker": "test-agent", "content": "你好"}],
                    }
                ]
            },
            storage_exporters={"test-agent": export_storage},
        ),
        retention=2,
    )
    writer.write(result, frame)
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.status = "PAUSED"
        row.recoverable_step = 1
        row.completed_steps = 1
        row.slot_no = None
        row.current_attempt_id = None
        row.pid = None
        row.pid_create_time = None

    service = CheckpointService(database, var_dir=var_dir)
    detail = service.detail(run["run_id"], 1)
    assert detail["status"] == "RECOVERABLE"
    assert detail["validated"] is True and detail["resumable"] is True
    assert detail["bundle"]["run_id"] == run["run_id"]
    assert detail["bundle"]["attempt_id"] == claimed.attempt_id
    assert detail["bundle"]["step_no"] == 1
    agent = detail["agent_state"]["items"][0]
    assert agent["coord"] == [1, 0]
    assert agent["currently"] == "checking continuity"
    assert agent["action"]["event"] == "inspect"
    assert agent["schedule_item_count"] == 1
    assert "embedding" not in json.dumps(agent)
    assert detail["conversations"]["items"][0]["messages"][0]["content"] == "你好"
    assert detail["storage"]["group_count"] == 1
    storage = detail["storage"]["groups"][0]
    assert storage["agent_key"] == "test-agent"
    assert storage["file_count"] == 2 and storage["size_bytes"] > 0
    assert all(not Path(item["path"]).is_absolute() and ".." not in Path(item["path"]).parts for item in detail["files"])
    assert str(var_dir) not in json.dumps(detail)

    cursor = 0
    chunks = []
    for _ in range(100):
        page = service.preview(
            run["run_id"], 1, "state", cursor=cursor, limit_bytes=23
        )
        chunks.append(page["content"])
        if page["next_cursor"] is None:
            break
        assert page["next_cursor"] > cursor
        cursor = page["next_cursor"]
    assert json.loads("".join(chunks))["agents"]["test-agent"]["coord"] == [1, 0]
    with pytest.raises(ServiceError) as forbidden:
        service.preview(run["run_id"], 1, "storage/test-agent/associate/docstore.json")
    assert (forbidden.value.status_code, forbidden.value.code) == (
        422,
        "CHECKPOINT_PREVIEW_SECTION_INVALID",
    )

    _e2, _r2, other = _publish_run(database, var_dir, "checkpoint-detail-other")
    with pytest.raises(ServiceError) as cross_run:
        service.detail(other["run_id"], 1)
    assert cross_run.value.status_code in {404, 410}


def test_def_050_checkpoint_http_detail_preview_and_tamper_envelopes(web_runtime):
    """ROL-CHK-002 HTTP only exposes validated, bounded, enumerated sections."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "checkpoint-http-detail"
    )
    paths = RunPaths.under(var_dir, UUID(run["run_id"]))
    _write_checkpoint(paths, _step(run["run_id"], claimed.attempt_id, 1))
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.status = "PAUSED"
        row.completed_steps = 1
        row.recoverable_step = 1
        row.slot_no = None
        row.current_attempt_id = None
        row.pid = None
        row.pid_create_time = None

    detail = client.get(f"/api/v1/runs/{run['run_id']}/checkpoints/1")
    assert detail.status_code == 200, detail.text
    document = detail.json()
    assert document["run_id"] == run["run_id"]
    assert document["status"] == "RECOVERABLE" and document["validated"] is True
    assert document["preview_sections"] == ["bundle", "conversation", "state"]
    assert str(var_dir) not in detail.text

    cursor = 0
    chunks: list[str] = []
    observed_file_id = None
    for _ in range(100):
        preview = client.get(
            f"/api/v1/runs/{run['run_id']}/checkpoints/1/preview",
            params={
                "section": "state",
                "cursor": cursor,
                "limit_bytes": 19,
                **({"file_id": observed_file_id} if observed_file_id else {}),
            },
        )
        assert preview.status_code == 200, preview.text
        page = preview.json()
        observed_file_id = observed_file_id or page["file_id"]
        assert page["file_id"] == observed_file_id
        chunks.append(page["content"])
        if page["next_cursor"] is None:
            assert page["eof"] is True
            break
        cursor = page["next_cursor"]
    else:
        pytest.fail("checkpoint preview cursor did not terminate")
    assert json.loads("".join(chunks))["agents"]["test-agent"]["coord"] == [1, 0]

    arbitrary = client.get(
        f"/api/v1/runs/{run['run_id']}/checkpoints/1/preview",
        params={"section": "storage/test-agent/associate/docstore.json"},
    )
    assert arbitrary.status_code == 422
    assert arbitrary.json()["error"]["code"] == "CHECKPOINT_PREVIEW_SECTION_INVALID"

    _e2, _r2, other = _publish_run(database, var_dir, "checkpoint-http-other")
    crossed = client.get(f"/api/v1/runs/{other['run_id']}/checkpoints/1")
    assert crossed.status_code == 404
    assert crossed.json()["error"]["code"] == "CHECKPOINT_NOT_FOUND"

    state_path = paths.checkpoints / "step-000001" / "state.json"
    state_path.write_text('{"tampered":true}', encoding="utf-8")
    tampered = client.get(
        f"/api/v1/runs/{run['run_id']}/checkpoints/1/preview",
        params={"section": "state"},
    )
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "CHECKPOINT_INVALID"


def test_def_050_checkpoint_zip_uses_the_selected_verified_step(database, tmp_path: Path):
    """ROL-CHK-002/ART-002 ZIP selection cannot silently follow LATEST."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "checkpoint-export"
    )
    paths = RunPaths.under(var_dir, UUID(run["run_id"]))
    _write_checkpoint(paths, _step(run["run_id"], claimed.attempt_id, 1))
    _write_checkpoint(paths, _step(run["run_id"], claimed.attempt_id, 2))
    artifacts = ArtifactService(database, var_dir=var_dir)
    job = artifacts.create_job(
        run["run_id"],
        job_type="CHECKPOINT_BUNDLE",
        parameters={"checkpoint_step": 1},
    )
    claimed_job = ArtifactSchedulerRepository(database).claim_next()
    assert claimed_job is not None and claimed_job.job_id == job["job_id"]
    artifact_id = ArtifactBuilder(database, var_dir=var_dir).build(job["job_id"])
    _artifact, archive_path = artifacts.content(run["run_id"], artifact_id)

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert names
        assert all(not name.startswith(("/", "\\")) and ".." not in Path(name).parts for name in names)
        roots = {Path(name).parts[0] for name in names}
        assert roots == {"step-000001"}


def test_def_051_replay_has_one_v2_schema_and_source_locked_identity(database, tmp_path: Path):
    """ROL-ART-001/RPL-001 rejects two incompatible documents called v1."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run = _publish_run(database, var_dir, "replay-v2")
    document = ArtifactBuilder(database, var_dir=var_dir)._replay_document(run["run_id"])
    required = {
        "schema_version",
        "generator_version",
        "run_id",
        "revision_id",
        "definition_hash",
        "world",
        "source_step",
        "stride_minutes",
        "start_time",
        "agents",
        "partial",
        "steps",
    }
    assert document["schema_version"] == 2
    assert required <= document.keys()
    assert document["generator_version"] == GENERATOR_VERSION == "ga-replay-v2"
    assert document["source_step"] == document["available_step"]
    assert '"schema_version": 1' not in inspect.getsource(compress.build_replay)
    assert "ArtifactBuilder" not in inspect.getsource(compress.build_replay), (
        "compress.py must call the common Replay V2 builder, not the Web job wrapper"
    )


def test_def_051_replay_artifact_satisfies_the_full_v2_semantic_validator(
    database, tmp_path: Path
):
    """ROL-ART-001/RPL-001 validates facts, not only a version integer."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "replay-v2-artifact-full"
    )
    result = _rich_step(run["run_id"], claimed.attempt_id, 1)
    paths = RunPaths.under(var_dir, UUID(run["run_id"]))
    _write_checkpoint(paths, result)
    _project_replay_step(
        database, var_dir, run["run_id"], result, checkpoint=True
    )
    with database.session_factory.begin() as session:
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=1,
                result_state="PARTIAL",
                projection_version="test",
                result_version=1,
            )
        )
    document = ArtifactBuilder(database, var_dir=var_dir)._replay_document(run["run_id"])
    _assert_replay_v2(document, run_id=run["run_id"], source_step=1)


def test_def_051_live_replay_window_uses_the_same_full_v2_validator(web_runtime):
    """ROL-RPL-001 live DTO and final Artifact must not drift into two schemas."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "replay-v2-window-full"
    )
    result = _rich_step(run["run_id"], claimed.attempt_id, 1)
    paths = RunPaths.under(var_dir, UUID(run["run_id"]))
    _write_checkpoint(paths, result)
    _project_replay_step(
        database, var_dir, run["run_id"], result, checkpoint=True
    )
    with database.session_factory.begin() as session:
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=1,
                result_state="PARTIAL",
                projection_version="test",
                result_version=7,
            )
        )

    manifest_response = client.get(f"/api/v1/runs/{run['run_id']}/replay/manifest")
    assert manifest_response.status_code == 200, manifest_response.text
    steps_response = client.get(
        f"/api/v1/runs/{run['run_id']}/replay/steps",
        params={"from_step": 1, "limit": 100},
    )
    assert steps_response.status_code == 200, steps_response.text
    manifest = manifest_response.json()
    window = steps_response.json()
    assert window["result_version"] == 7
    assert window["available_step"] == 1
    document = {**manifest, "steps": window["steps"]}
    _assert_replay_v2(document, run_id=run["run_id"], source_step=1)


def test_def_051_artifact_dedup_identity_changes_with_source_step(database, tmp_path: Path):
    """ROL-ART-002 step 10 partial must not suppress step 100 final output."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run = _publish_run(database, var_dir, "artifact-source-step")
    with database.session_factory.begin() as session:
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=10,
                result_state="PARTIAL",
                projection_version="test",
            )
        )
    service = ArtifactService(database, var_dir=var_dir)
    first = service.create_job(run["run_id"], job_type="BUILD_REPLAY")
    with database.session_factory.begin() as session:
        job = session.get(ArtifactJob, first["job_id"])
        job.status = "SUCCEEDED"
        job.finished_at = datetime.now(timezone.utc)
        summary = session.get(RunResultSummary, run["run_id"])
        summary.available_step = 100
        summary.result_state = "COMPLETE"

    second = service.create_job(run["run_id"], job_type="BUILD_REPLAY")
    assert second["job_id"] != first["job_id"]
    assert first["parameters"]["source_step"] == 10
    assert second["parameters"]["source_step"] == 100
    assert first["parameters"]["generator_version"] == "ga-replay-v2"
    assert second["parameters"]["generator_version"] == "ga-replay-v2"


def test_def_051_build_job_freezes_source_and_preserves_partial_and_final_artifacts(
    database, tmp_path: Path
):
    """ROL-ART-002 creation-time source lock survives delayed build and finalization."""

    # Keep this independent of pytest's long node-id directory on Windows. The
    # product identity under test is source immutability, not MAX_PATH support.
    var_dir = tmp_path.parent / f"artifact-freeze-{uuid4().hex[:8]}"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "artifact-source-freeze"
    )
    for step_no in range(1, 11):
        _project_replay_step(
            database,
            var_dir,
            run["run_id"],
            _step(run["run_id"], claimed.attempt_id, step_no),
        )
    with database.session_factory.begin() as session:
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=10,
                result_state="PARTIAL",
                projection_version="test",
                result_version=10,
            )
        )
    service = ArtifactService(database, var_dir=var_dir)
    partial_job = service.create_job(run["run_id"], job_type="BUILD_REPLAY")
    assert partial_job["parameters"]["source_step"] == 10
    assert partial_job["parameters"]["partial"] is True
    assert partial_job["parameters"]["generator_version"] == "ga-replay-v2"

    for step_no in range(11, 101):
        _project_replay_step(
            database,
            var_dir,
            run["run_id"],
            _step(run["run_id"], claimed.attempt_id, step_no),
        )
    with database.session_factory.begin() as session:
        summary = session.get(RunResultSummary, run["run_id"])
        summary.available_step = 100
        summary.result_state = "COMPLETE"
        summary.result_version = 100
        row = session.get(Run, run["run_id"])
        row.requested_steps = 100
        row.completed_steps = 100
        row.recoverable_step = 100
        row.status = "COMPLETED"
        row.slot_no = None
        row.current_attempt_id = None
        row.pid = None
        row.pid_create_time = None

    claimed_partial = ArtifactSchedulerRepository(database).claim_next()
    assert claimed_partial is not None and claimed_partial.job_id == partial_job["job_id"]
    partial_artifact_id = ArtifactBuilder(database, var_dir=var_dir).build(
        partial_job["job_id"]
    )
    partial_artifact, partial_path = service.content(
        run["run_id"], partial_artifact_id
    )
    partial_bytes = partial_path.read_bytes()
    partial_document = json.loads(partial_bytes)
    assert partial_document["source_step"] == 10
    assert partial_document["partial"] is True
    assert max(item["step_no"] for item in partial_document["steps"]) == 10
    partial_hash = hashlib.sha256(partial_bytes).hexdigest()
    assert partial_artifact.sha256 == partial_hash

    final_job = service.create_job(run["run_id"], job_type="BUILD_REPLAY")
    assert final_job["job_id"] != partial_job["job_id"]
    assert final_job["parameters"]["source_step"] == 100
    assert final_job["parameters"]["partial"] is False
    claimed_final = ArtifactSchedulerRepository(database).claim_next()
    assert claimed_final is not None and claimed_final.job_id == final_job["job_id"]
    final_artifact_id = ArtifactBuilder(database, var_dir=var_dir).build(
        final_job["job_id"]
    )
    final_artifact, final_path = service.content(run["run_id"], final_artifact_id)
    final_document = json.loads(final_path.read_bytes())
    assert final_document["source_step"] == 100
    assert final_document["partial"] is False
    assert max(item["step_no"] for item in final_document["steps"]) == 100

    assert final_artifact_id != partial_artifact_id
    assert final_path != partial_path
    assert partial_path.read_bytes() == partial_bytes
    with database.session_factory() as session:
        persisted_partial = session.get(RunArtifact, partial_artifact_id)
        persisted_final = session.get(RunArtifact, final_artifact_id)
        assert persisted_partial.relative_path != persisted_final.relative_path
        assert persisted_partial.sha256 == partial_hash
        assert persisted_partial.state == persisted_final.state == "READY"


def test_def_052_completed_run_automatically_queues_replay_and_report(database, tmp_path: Path):
    """ROL-ART-002 completion owns automatic idempotent derived facts."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(database, var_dir, "auto-artifacts")
    repository = LocalRunSchedulerRepository(database)
    assert repository.register_worker(claimed, pid=4242, pid_create_time=1.0)
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.completed_steps = row.requested_steps
        row.recoverable_step = row.requested_steps
    assert repository.finish_worker(run["run_id"], claimed.attempt_id, exit_code=0)

    with database.session_factory() as session:
        job_types = set(
            session.scalars(select(ArtifactJob.job_type).where(ArtifactJob.run_id == run["run_id"]))
        )
    assert {"BUILD_REPLAY", "BUILD_REPORT"} <= job_types


def test_def_052_concurrent_finish_is_idempotent_for_automatic_artifacts(
    database, tmp_path: Path
):
    """ROL-ART-002 concurrent terminal reconciliation creates one final pair."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "auto-artifacts-concurrent"
    )
    repository = LocalRunSchedulerRepository(database)
    assert repository.register_worker(claimed, pid=4343, pid_create_time=2.0)
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.completed_steps = row.requested_steps
        row.recoverable_step = row.requested_steps
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=row.requested_steps,
                result_state="COMPLETE",
                projection_version="test",
                result_version=1,
            )
        )

    def finish_once(_index: int):
        return LocalRunSchedulerRepository(database).finish_worker(
            run["run_id"], claimed.attempt_id, exit_code=0
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(finish_once, range(8)))
    assert outcomes.count(True) == 1
    with database.session_factory() as session:
        jobs = list(
            session.scalars(
                select(ArtifactJob).where(ArtifactJob.run_id == run["run_id"])
            )
        )
    assert [item.job_type for item in jobs].count("BUILD_REPLAY") == 1
    assert [item.job_type for item in jobs].count("BUILD_REPORT") == 1
    for item in jobs:
        assert item.parameters_json["source_step"] > 0
        assert item.parameters_json["partial"] is False
        assert item.parameters_json["generator_version"]


def test_def_053_artifact_preview_preserves_utf8_at_byte_boundaries(web_runtime):
    """ROL-LOG-001 byte semantics also apply to controlled textual artifacts."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run = _publish_run(database, var_dir, "artifact-preview")
    payload = '{"message":"中🙂界","tail":"done"}'
    target = var_dir / "runs" / run["run_id"] / "artifacts" / "unicode.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload.encode("utf-8"))
    with database.session_factory.begin() as session:
        artifact = RunArtifact(
            id=str(uuid4()),
            run_id=run["run_id"],
            artifact_type="REPORT",
            logical_name="unicode.json",
            media_type="application/json",
            relative_path=target.relative_to(var_dir).as_posix(),
            size_bytes=target.stat().st_size,
            sha256="0" * 64,
            source_kind="DERIVED",
            generator_version="test",
            state="READY",
        )
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id

    cursor = 0
    chunks = []
    for _ in range(30):
        response = client.get(
            f"/api/v1/runs/{run['run_id']}/artifacts/{artifact_id}/preview",
            params={"cursor": cursor, "limit_bytes": 14},
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert "�" not in page["content"]
        chunks.append(page["content"])
        if page["next_cursor"] is None:
            break
        assert page["next_cursor"] > cursor
        cursor = page["next_cursor"]
    assert "".join(chunks) == payload


@pytest.mark.parametrize(
    "mutation",
    ["absolute", "parent_segment", "final_symlink", "intermediate_symlink", "size", "sha256"],
)
def test_def_061_artifact_preview_and_download_enforce_persisted_storage_integrity(
    web_runtime, mutation: str
):
    """ROL-ART-002 owned metadata cannot authorize a changed or aliased file."""

    client, database, var_dir, _app = web_runtime
    key_suffix = mutation.replace("_", "-")
    _experiment, _revision, run = _publish_run(
        database, var_dir, f"artifact-integrity-{key_suffix}"
    )
    run_root = var_dir / "runs" / run["run_id"]
    artifact_root = run_root / "artifacts"
    actual_root = artifact_root / "actual"
    actual_root.mkdir(parents=True, exist_ok=True)
    target = actual_root / "result.json"
    original = b'{"result":"immutable"}'
    target.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    relative_path = target.relative_to(var_dir).as_posix()

    if mutation == "absolute":
        relative_path = str(target.resolve())
    elif mutation == "parent_segment":
        relative_path = (
            f"runs/{run['run_id']}/artifacts/actual/../actual/result.json"
        )
    elif mutation == "final_symlink":
        link = artifact_root / "linked-result.json"
        _create_native_symlink_or_skip(link, target)
        relative_path = link.relative_to(var_dir).as_posix()
    elif mutation == "intermediate_symlink":
        link = artifact_root / "linked-directory"
        _create_native_symlink_or_skip(
            link, actual_root, target_is_directory=True
        )
        relative_path = (link / target.name).relative_to(var_dir).as_posix()
    elif mutation == "size":
        target.write_bytes(original + b"!")
    elif mutation == "sha256":
        target.write_bytes(b"X" * len(original))

    with database.session_factory.begin() as session:
        artifact = RunArtifact(
            id=str(uuid4()),
            run_id=run["run_id"],
            artifact_type="REPORT",
            logical_name="result.json",
            media_type="application/json",
            relative_path=relative_path,
            size_bytes=len(original),
            sha256=digest,
            source_kind="DERIVED",
            generator_version="integrity-test",
            source_step=1,
            partial=True,
            state="READY",
        )
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id

    _other_experiment, _other_revision, other = _publish_run(
        database, var_dir, f"artifact-integrity-other-{key_suffix}"
    )
    crossed = client.get(
        f"/api/v1/runs/{other['run_id']}/artifacts/{artifact_id}/download"
    )
    assert crossed.status_code == 404
    assert crossed.json()["error"]["code"] == "ARTIFACT_NOT_FOUND"

    for suffix in ("preview", "download"):
        response = client.get(
            f"/api/v1/runs/{run['run_id']}/artifacts/{artifact_id}/{suffix}"
        )
        assert response.status_code in {409, 500}, response.text
        assert response.json()["error"]["code"] in {
            "ARTIFACT_CONTENT_INTEGRITY_ERROR",
            "RUN_STORAGE_INTEGRITY_ERROR",
        }
        assert str(var_dir) not in response.text and str(target) not in response.text


def test_def_061_artifact_cross_run_native_directory_symlink_is_rejected(
    web_runtime,
):
    """ROL-ART-002 a real directory symlink cannot import another Run's file."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run = _publish_run(
        database, var_dir, "artifact-native-cross-run-owner"
    )
    _other_experiment, _other_revision, other = _publish_run(
        database, var_dir, "artifact-native-cross-run-target"
    )
    target_root = var_dir / "runs" / other["run_id"] / "artifacts"
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / "cross-run.json"
    original = b'{"owner":"other-run","immutable":true}'
    target.write_bytes(original)

    owner_root = var_dir / "runs" / run["run_id"] / "artifacts"
    owner_root.mkdir(parents=True, exist_ok=True)
    link = owner_root / "cross-run-directory"
    _create_native_symlink_or_skip(link, target_root, target_is_directory=True)
    relative_path = (link / target.name).relative_to(var_dir).as_posix()
    with database.session_factory.begin() as session:
        artifact = RunArtifact(
            id=str(uuid4()),
            run_id=run["run_id"],
            artifact_type="REPORT",
            logical_name="cross-run.json",
            media_type="application/json",
            relative_path=relative_path,
            size_bytes=len(original),
            sha256=hashlib.sha256(original).hexdigest(),
            source_kind="DERIVED",
            generator_version="native-symlink-release-gate",
            source_step=1,
            partial=True,
            state="READY",
        )
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id

    responses = []
    try:
        crossed = client.get(
            f"/api/v1/runs/{other['run_id']}/artifacts/{artifact_id}/download"
        )
        assert crossed.status_code == 404, crossed.text
        for suffix in ("preview", "download"):
            responses.append(
                client.get(
                    f"/api/v1/runs/{run['run_id']}/artifacts/{artifact_id}/{suffix}"
                )
            )
    finally:
        link.unlink()

    assert target.read_bytes() == original
    for response in responses:
        assert response.status_code in {409, 500}, response.text
        assert response.json()["error"]["code"] in {
            "ARTIFACT_CONTENT_INTEGRITY_ERROR",
            "RUN_STORAGE_INTEGRITY_ERROR",
        }
        assert str(var_dir) not in response.text
        assert str(target) not in response.text


def test_def_062_generic_checkpoint_job_rejects_source_step_mismatch(web_runtime):
    """ROL-CHK-002/ART-002 one checkpoint job has one authoritative source step."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "checkpoint-job-source-mismatch"
    )
    paths = RunPaths.under(var_dir, UUID(run["run_id"]))
    _write_checkpoint(paths, _step(run["run_id"], claimed.attempt_id, 1))
    response = client.post(
        f"/api/v1/runs/{run['run_id']}/artifact-jobs",
        json={
            "job_type": "CHECKPOINT_BUNDLE",
            "parameters": {"checkpoint_step": 1, "source_step": 2},
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "CHECKPOINT_SOURCE_STEP_MISMATCH"


def test_def_062_generic_checkpoint_job_validates_before_it_is_queued(web_runtime):
    """ROL-CHK-002/ART-002 the generic endpoint cannot bypass full validation."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, _claimed = _claimed_run(
        database, var_dir, "checkpoint-job-invalid"
    )
    invalid = (
        var_dir
        / "runs"
        / run["run_id"]
        / "checkpoints"
        / "step-000001"
    )
    invalid.mkdir(parents=True)
    (invalid / "bundle.json").write_text("{}", encoding="utf-8")
    response = client.post(
        f"/api/v1/runs/{run['run_id']}/artifact-jobs",
        json={
            "job_type": "CHECKPOINT_BUNDLE",
            "parameters": {"checkpoint_step": 1},
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CHECKPOINT_INVALID"


def test_def_054_replay_player_is_packaged_external_and_not_dom_dot_fallback():
    """ROL-RPL-002 formal replay cannot keep the prototype dot renderer alive."""

    shell = SHELL.read_text(encoding="utf-8")
    console = CONSOLE.read_text(encoding="utf-8")
    assert PLAYER.is_file(), "formal replay-player.js is not packaged"
    assert PHASER.is_file(), "the replay runtime cannot depend on a CDN Phaser build"
    assert shell.count('/static/console/vendor/phaser.min.js') == 1
    assert shell.count('/static/console/replay-player.js') == 1
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
  selectedReplayRevisionId:'revision-1', currentRun:{revision_id:'revision-1'},
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
  state.selectedReplayRevisionId='revision-1';
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


def test_def_066_phaser_canvas_and_normalized_tiles_are_package_owned(web_runtime):
    """ROL-RPL-002: the renderer may not escape its high-fidelity card."""

    shell = SHELL.read_text(encoding="utf-8")
    player = PLAYER.read_text(encoding="utf-8")
    create_game = player[
        player.index("async _createGame") : player.index("_createAgent(")
    ]
    assert "const host = player.canvas?.parentElement" in create_game
    assert "parent: host" in create_game
    assert "canvas: player.canvas" in create_game
    assert "scale: { mode: PhaserRuntime.Scale.RESIZE }" in create_game
    assert "scale: { mode: PhaserRuntime.Scale.RESIZE, parent:" not in create_game
    assert "'Interior Furniture L2 '" in player, (
        "the shipped Tiled layer name has a significant trailing space"
    )
    assert __import__("re").search(
        r'id="resultMap"[^>]*>\s*<canvas id="resultMapCanvas"', shell
    ), "the controlled canvas must be a direct result-map descendant"
    timeline_layout_css = __import__("re").search(
        r"\.timeline-layout\s*\{([^}]*)\}", shell
    )
    assert timeline_layout_css and "--replay-stage-height: 520px" in timeline_layout_css.group(1)
    assert "height: var(--replay-stage-height)" in timeline_layout_css.group(1)
    result_map_css = __import__("re").search(r"\.result-map\s*\{([^}]*)\}", shell)
    assert result_map_css and "overflow: hidden" in result_map_css.group(1)
    assert "height: 100%" in result_map_css.group(1)
    assert "min-height: 0" in result_map_css.group(1)
    result_canvas_css = __import__("re").search(
        r"\.result-map\s*>\s*canvas\s*\{([^}]*)\}", shell
    )
    assert result_canvas_css and "height: 100%" in result_canvas_css.group(1)
    assert "min-height: 0" in result_canvas_css.group(1)
    timeline_stream_css = __import__("re").search(
        r"\.timeline-stream\s*\{([^}]*)\}", shell
    )
    assert timeline_stream_css and "height: 100%" in timeline_stream_css.group(1)
    assert "max-height: 100%" in timeline_stream_css.group(1)
    assert "min-height: 0" in timeline_stream_css.group(1)

    # The legacy image has a 16 px non-tile footer. Replay must consume a
    # controlled crop while leaving the legacy source byte-for-byte untouched.
    normalized = (
        ROOT
        / "generative_agents"
        / "web"
        / "static"
        / "replay-assets"
        / "interiors_pt3.png"
    )
    legacy = (
        ROOT
        / "generative_agents"
        / "frontend"
        / "static"
        / "assets"
        / "village"
        / "tilemap"
        / "interiors_pt3.png"
    )

    def png_size(path: Path) -> tuple[int, int]:
        header = path.read_bytes()[:24]
        assert header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR"
        return tuple(__import__("struct").unpack(">II", header[16:24]))

    assert png_size(legacy) == (512, 10032)
    assert png_size(normalized) == (512, 10016)
    assert all(value % 32 == 0 for value in png_size(normalized))
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() != hashlib.sha256(
        normalized.read_bytes()
    ).hexdigest()

    from generative_agents.config.bootstrap import make_builtin_definition
    from generative_agents.runtime.replay_v2 import build_replay_v2

    definition = make_builtin_definition(key="replay-texture-contract", name="Replay")
    manifest = build_replay_v2(
        run_id=str(uuid4()),
        revision_id=str(uuid4()),
        definition_hash="c" * 64,
        definition=definition,
        source_step=0,
        partial=True,
        results=(),
    )
    override = manifest["world"]["render_asset"]["texture_overrides"][
        "interiors_pt3"
    ]
    normalized_bytes = normalized.read_bytes()
    assert override["url"] == "/static/console/replay-assets/interiors_pt3.png"
    assert override["sha256"] == hashlib.sha256(normalized_bytes).hexdigest()
    assert (override["width"], override["height"]) == png_size(normalized)
    assert override["normalization"] == "CROP_BOTTOM_NON_TILE_PIXELS"
    assert "texture_overrides" in player and "textureUrl" in player
    client, _database, _var_dir, _app = web_runtime
    response = client.get(override["url"])
    assert response.status_code == 200
    assert response.content == normalized_bytes
    tilemap_url = manifest["world"]["render_asset"]["tilemap_url"]
    assert tilemap_url.startswith("/static/console/replay-assets/")
    assert tilemap_url != (
        "/generative_agents/frontend/static/assets/village/tilemap/tilemap.json"
    )
    tilemap_response = client.get(tilemap_url)
    assert tilemap_response.status_code == 200
    normalized_map = tilemap_response.json()
    legacy_map = json.loads(
        (
            ROOT
            / "generative_agents"
            / "frontend"
            / "static"
            / "assets"
            / "village"
            / "tilemap"
            / "tilemap.json"
        ).read_text(encoding="utf-8")
    )
    legacy_tileset = next(
        item for item in legacy_map["tilesets"] if item["name"] == "interiors_pt3"
    )
    normalized_tileset = next(
        item
        for item in normalized_map["tilesets"]
        if item["name"] == "interiors_pt3"
    )
    assert legacy_tileset["imageheight"] == 10032
    assert normalized_tileset["imageheight"] == 10016
    assert normalized_tileset["tilecount"] == legacy_tileset["tilecount"] == 5008
    restored = __import__("copy").deepcopy(normalized_map)
    next(
        item for item in restored["tilesets"] if item["name"] == "interiors_pt3"
    )["imageheight"] = 10032
    assert restored == legacy_map, "Replay normalization changed tilemap semantics beyond the footer"
    render_asset = manifest["world"]["render_asset"]
    tilemap_asset = render_asset["tilemap_asset"]
    assert tilemap_asset["url"] == tilemap_url
    assert tilemap_asset["sha256"] == hashlib.sha256(
        tilemap_response.content
    ).hexdigest()
    legacy_tilemap_path = (
        ROOT
        / "generative_agents"
        / "frontend"
        / "static"
        / "assets"
        / "village"
        / "tilemap"
        / "tilemap.json"
    )
    assert tilemap_asset["source_sha256"] == hashlib.sha256(
        legacy_tilemap_path.read_bytes()
    ).hexdigest()
    assert tilemap_asset["normalization"] == "INTERIORS_PT3_IMAGEHEIGHT_10016"


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


def test_def_068_supervisor_child_chinese_stdout_is_explicit_utf8_and_byte_exact(
    database, tmp_path: Path
):
    """ROL-LOG-001: real redirected worker output cannot inherit Windows cp936."""

    var_dir = tmp_path / "var"
    _experiment, _revision, run = _publish_run(database, var_dir, "spawned-utf8-log")
    captured: dict[str, object] = {}
    lines = [
        "INFO 子进程标准输出：两位居民完成对话",
        "ERROR 子进程标准错误：检查点写入完成",
    ]
    child_code = (
        "import sys,time\n"
        f"print({lines[0]!r}, flush=True)\n"
        f"print({lines[1]!r}, file=sys.stderr, flush=True)\n"
        "time.sleep(0.5)\n"
    )

    def process_factory(_worker_command, **kwargs):
        captured["env"] = kwargs.get("env")
        return subprocess.Popen([sys.executable, "-c", child_code], **kwargs)

    supervisor = LocalProcessSupervisor(
        database,
        var_dir=var_dir,
        max_concurrent_runs=1,
        process_factory=process_factory,
        code_build_id="utf8-log-contract",
    )
    try:
        supervisor.tick()
        with database.session_factory() as session:
            row = session.get(Run, run["run_id"])
            assert row.current_attempt_id
            attempt_id = row.current_attempt_id
            attempt = session.get(RunAttempt, attempt_id)
            assert attempt is not None
            log_path = var_dir / attempt.log_path
        child = supervisor._children[attempt_id].process
        assert child.wait(timeout=10) == 0
        supervisor.tick()

        failures = []
        child_env = captured.get("env")
        if not isinstance(child_env, dict):
            failures.append("worker spawn did not pass an explicit environment")
        else:
            if child_env.get("PYTHONUTF8") != "1":
                failures.append("PYTHONUTF8=1 was not forced for the worker")
            if not str(child_env.get("PYTHONIOENCODING", "")).lower().startswith(
                "utf-8"
            ):
                failures.append("PYTHONIOENCODING was not forced to UTF-8")

        raw = log_path.read_bytes()
        try:
            decoded = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            decoded = ""
            failures.append(f"redirected child log is not UTF-8: byte {exc.start}")
        if decoded and decoded.splitlines() != lines:
            failures.append(f"stdout/stderr merge changed records: {decoded!r}")

        service = LogService(database, var_dir=var_dir)
        cursor = 0
        pieces: list[str] = []
        observed_file_id = None
        try:
            for _ in range(len(raw) + 10):
                page = service.read_attempt_log(
                    run["run_id"],
                    attempt_id,
                    cursor=cursor,
                    limit_bytes=7,
                    file_id=observed_file_id,
                )
                observed_file_id = observed_file_id or page["file_id"]
                assert page["file_id"] == observed_file_id
                pieces.append(page["content"])
                assert page["next_cursor"] >= cursor
                cursor = page["next_cursor"]
                if page["eof"]:
                    break
            else:
                failures.append("byte cursor did not terminate")
        except ServiceError as exc:
            failures.append(f"log API rejected spawned bytes: {exc.code}")
        if cursor != len(raw):
            failures.append(f"cursor consumed {cursor} bytes but file has {len(raw)}")
        if decoded and "".join(pieces) != decoded:
            failures.append("byte-window concatenation did not reconstruct the child log")
        assert not failures, "; ".join(failures)
    finally:
        supervisor.stop()


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


def test_def_054_replay_runtime_and_real_map_assets_are_controlled_http_resources(
    web_runtime,
):
    """ROL-RPL-002 formal player dependencies remain available without repository docs."""

    client, _database, _var_dir, _app = web_runtime
    resources = {
        "/static/console/vendor/phaser.min.js": "javascript",
        "/static/console/replay-player.js": "javascript",
        "/generative_agents/frontend/static/assets/village/tilemap/tilemap.json": "json",
        "/generative_agents/frontend/static/assets/village/tilemap/CuteRPG_Village_B.png": "png",
        "/generative_agents/frontend/static/assets/village/agents/%E4%B9%94%E6%B2%BB/texture.png": "png",
    }
    for url, content_type in resources.items():
        response = client.get(url)
        assert response.status_code == 200, f"{url}: {response.text[:200]}"
        assert content_type in response.headers["content-type"]
        assert response.content
    player = PLAYER.read_text(encoding="utf-8")
    assert "currentExperimentName" not in player
    assert "experiment.name" not in player
    assert "map-agent" not in player


def test_def_054_replay_assets_survive_a_real_wheel_install_and_http_boot(
    tmp_path: Path,
):
    """ROL-RPL-002 source-tree presence is not evidence of deployable assets."""

    candidates = [ROOT / "pyproject.toml", ROOT / "setup.cfg", ROOT / "setup.py"]
    metadata = next((path for path in candidates if path.is_file()), None)
    assert metadata is not None, "repository has no build metadata capable of producing a wheel"
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(ROOT),
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert build.returncode == 0, build.stderr or build.stdout
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    exact = {
        "generative_agents/web/static/experiment-console.html",
        "generative_agents/web/static/console-api.js",
        "generative_agents/web/static/replay-player.js",
        "generative_agents/web/static/vendor/phaser.min.js",
        "generative_agents/web/static/replay-assets/interiors_pt3.png",
        "generative_agents/frontend/static/assets/village/tilemap/tilemap.json",
        "generative_agents/frontend/static/assets/village/tilemap/CuteRPG_Village_B.png",
    }
    assert exact <= names, f"wheel misses runtime assets: {sorted(exact - names)}"
    assert any(
        name.startswith("generative_agents/web/static/replay-assets/")
        and name.endswith(".json")
        for name in names
    ), "wheel misses the Replay-normalized tilemap"
    assert any(
        name.startswith("generative_agents/frontend/static/assets/village/agents/")
        and name.endswith("/texture.png")
        for name in names
    ), "wheel contains no Agent sprite texture"

    install_dir = tmp_path / "installed"
    installed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            str(wheel),
            "--target",
            str(install_dir),
            "--no-deps",
            "--no-compile",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert installed.returncode == 0, installed.stderr or installed.stdout
    probe = r"""
import json
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from generative_agents.config.bootstrap import make_builtin_definition
from generative_agents.runtime.replay_v2 import build_replay_v2
from generative_agents.web import create_app
root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
app = create_app(database_url='sqlite:///' + (root / 'wheel.db').as_posix(), var_dir=str(root / 'var'), supervisor_enabled=False)
definition = make_builtin_definition(key='wheel-replay', name='Wheel Replay')
manifest = build_replay_v2(
  run_id='00000000-0000-0000-0000-000000000001',
  revision_id='00000000-0000-0000-0000-000000000002',
  definition_hash='d' * 64,
  definition=definition,
  source_step=0,
  partial=True,
  results=(),
)
render_asset = manifest['world']['render_asset']
urls = [
  '/static/console/replay-player.js',
  '/static/console/vendor/phaser.min.js',
  render_asset['tilemap_url'],
  render_asset['texture_overrides']['interiors_pt3']['url'],
  '/generative_agents/frontend/static/assets/village/tilemap/tilemap.json',
  '/generative_agents/frontend/static/assets/village/tilemap/CuteRPG_Village_B.png',
]
with TestClient(app) as client:
  output = []
  for url in urls:
    response = client.get(url)
    output.append((url, response.status_code, response.headers.get('content-type')))
print(json.dumps(output))
if any(status != 200 for _, status, _ in output): raise SystemExit(2)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(install_dir)
    boot = subprocess.run(
        [sys.executable, "-c", probe, str(tmp_path / "isolated-runtime")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert boot.returncode == 0, boot.stderr or boot.stdout


def test_def_054_replay_steps_window_10k_never_returns_the_whole_run(web_runtime):
    """ROL-RPL-003 validates real frame semantics inside a 10k projection."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "replay-window-10k"
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    paths = RunPaths.under(var_dir, UUID(run["run_id"]))
    actual: dict[int, tuple[object, object]] = {}
    for step_no in range(5001, 5101):
        result = _step(run["run_id"], claimed.attempt_id, step_no)
        frame = FrameStore(paths).write(result)
        actual[step_no] = (result, frame)
    rows = [
        {
            "run_id": run["run_id"],
            "step_no": step_no,
            "attempt_id": claimed.attempt_id,
            "virtual_time": (
                actual[step_no][0].virtual_time
                if step_no in actual
                else start + timedelta(minutes=step_no)
            ),
            "frame_path": (
                actual[step_no][1].path.relative_to(var_dir).as_posix()
                if step_no in actual
                else f"runs/{run['run_id']}/frames/step-{step_no:06d}.json.gz"
            ),
            "frame_sha256": (
                actual[step_no][1].sha256
                if step_no in actual
                else f"{step_no:064x}"[-64:]
            ),
            "action_count": 1,
            "movement_count": 1,
            "conversation_count": 0,
            "message_count": 0,
            "memory_created_count": 0,
            "memory_accessed_count": 0,
            "model_logical_calls": 0,
            "model_retry_count": 0,
            "active_agent_count": 1,
            "checkpoint": step_no % 100 == 0,
            "committed_at": start + timedelta(minutes=step_no),
        }
        for step_no in range(1, 10_001)
    ]
    with database.session_factory.begin() as session:
        run_row = session.get(Run, run["run_id"])
        run_row.requested_steps = 10_000
        run_row.completed_steps = 10_000
        session.execute(insert(RunStep), rows)
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=10_000,
                result_state="COMPLETE",
                projection_version="test",
            )
        )

    response = client.get(
        f"/api/v1/runs/{run['run_id']}/replay/steps",
        params={"from_step": 5001, "limit": 100},
    )
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["run_id"] == run["run_id"]
    assert document["available_step"] == 10_000
    assert document["source_step"] == 10_000
    assert len(document["steps"]) == 100
    assert document["steps"][0]["step_no"] == 5001
    assert document["steps"][-1]["step_no"] == 5100
    assert document["next_from_step"] == 5101
    assert len(response.content) < 1_000_000
    assert all(item["attempt_boundary"] is False for item in document["steps"]), (
        "request-window starts must not be relabelled as Attempt boundaries"
    )
    for item in document["steps"]:
        expected = actual[item["step_no"]][0]
        assert item["virtual_time"] == expected.virtual_time.isoformat()
        agent = next(value for value in item["agents"] if value["agent_key"] == "test-agent")
        expected_agent = expected.agents[0]
        assert agent["path"] == [list(coord) for coord in expected_agent.path]
        assert agent["coord"] == list(expected_agent.to_coord)
        assert agent["action"]["description"] == expected_agent.action.description

    assert client.get(
        f"/api/v1/runs/{run['run_id']}/replay/steps",
        params={"from_step": 1, "limit": 0},
    ).status_code == 422
    assert client.get(
        f"/api/v1/runs/{run['run_id']}/replay/steps",
        params={"from_step": 1, "limit": 101},
    ).status_code == 422
    unknown = client.get(f"/api/v1/runs/{uuid4()}/replay/steps")
    assert unknown.status_code == 404


def test_def_054_replay_window_attempt_boundary_depends_on_committed_attempts(
    web_runtime,
):
    """ROL-RPL-003 marker semantics are independent of request chunk boundaries."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, first_attempt = _claimed_run(
        database, var_dir, "replay-attempt-boundary"
    )
    second_attempt_id = str(uuid4())
    for result in (
        _rich_step(run["run_id"], first_attempt.attempt_id, 1),
        _step(run["run_id"], first_attempt.attempt_id, 2),
        _step(run["run_id"], second_attempt_id, 3),
    ):
        _project_replay_step(database, var_dir, run["run_id"], result)
    with database.session_factory.begin() as session:
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=3,
                result_state="PARTIAL",
                projection_version="test",
                result_version=3,
            )
        )

    response = client.get(
        f"/api/v1/runs/{run['run_id']}/replay/steps",
        params={"from_step": 2, "limit": 2},
    )
    assert response.status_code == 200, response.text
    items = response.json()["steps"]
    assert [item["step_no"] for item in items] == [2, 3]
    assert [item["attempt_boundary"] for item in items] == [False, True]


@pytest.mark.parametrize(
    "mutation",
    [
        "absolute",
        "parent_segment",
        "cross_run",
        "final_symlink",
        "intermediate_symlink",
        "content",
        "database_sha256",
    ],
)
def test_def_063_replay_frame_integrity_blocks_manifest_window_and_artifact(
    web_runtime, mutation: str
):
    """ROL-ART-002/RPL-001/003 every replay producer trusts the same DB frame fact."""

    client, database, var_dir, _app = web_runtime
    suffix = mutation.replace("_", "-")
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, f"replay-frame-{suffix}"
    )
    result = _rich_step(run["run_id"], claimed.attempt_id, 1)
    stored = _project_replay_step(database, var_dir, run["run_id"], result)
    with database.session_factory.begin() as session:
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=1,
                result_state="PARTIAL",
                projection_version="test",
                result_version=1,
            )
        )

    if mutation == "absolute":
        with database.session_factory.begin() as session:
            session.get(RunStep, (run["run_id"], 1)).frame_path = str(stored.path.resolve())
    elif mutation == "parent_segment":
        with database.session_factory.begin() as session:
            session.get(RunStep, (run["run_id"], 1)).frame_path = (
                f"runs/{run['run_id']}/frames/../frames/{stored.path.name}"
            )
    elif mutation == "cross_run":
        _e2, _r2, other, other_attempt = _claimed_run(
            database, var_dir, f"replay-frame-other-{uuid4().hex[:6]}"
        )
        other_frame = _project_replay_step(
            database,
            var_dir,
            other["run_id"],
            _step(other["run_id"], other_attempt.attempt_id, 1),
        )
        with database.session_factory.begin() as session:
            session.get(RunStep, (run["run_id"], 1)).frame_path = (
                other_frame.path.relative_to(var_dir).as_posix()
            )
    elif mutation == "final_symlink":
        backup = stored.path.with_name("verified-frame.json.gz")
        stored.path.replace(backup)
        try:
            _create_native_symlink_or_skip(stored.path, backup)
        except BaseException:
            backup.replace(stored.path)
            raise
    elif mutation == "intermediate_symlink":
        link = stored.path.parent / "linked-frames"
        _create_native_symlink_or_skip(
            link, stored.path.parent, target_is_directory=True
        )
        with database.session_factory.begin() as session:
            session.get(RunStep, (run["run_id"], 1)).frame_path = (
                (link / stored.path.name).relative_to(var_dir).as_posix()
            )
    elif mutation == "content":
        with gzip.open(stored.path, "rt", encoding="utf-8") as handle:
            envelope = json.load(handle)
        envelope["result"]["agents"][0]["action"]["description"] = (
            "tampered but internally valid"
        )
        encoded = json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        stored.path.write_bytes(gzip.compress(encoded, compresslevel=6, mtime=0))
    elif mutation == "database_sha256":
        with database.session_factory.begin() as session:
            session.get(RunStep, (run["run_id"], 1)).frame_sha256 = "f" * 64

    manifest = client.get(f"/api/v1/runs/{run['run_id']}/replay/manifest")
    window = client.get(
        f"/api/v1/runs/{run['run_id']}/replay/steps",
        params={"from_step": 1, "limit": 1},
    )
    artifact_service = ArtifactService(database, var_dir=var_dir)
    job = artifact_service.create_job(run["run_id"], job_type="BUILD_REPLAY")
    owned = ArtifactSchedulerRepository(database).claim_next()
    assert owned is not None and owned.job_id == job["job_id"]
    build_error = None
    try:
        ArtifactBuilder(database, var_dir=var_dir).build(job["job_id"])
    except Exception as exc:  # the contract is "no artifact", envelope is asserted below
        build_error = exc
    with database.session_factory() as session:
        ready_artifacts = list(
            session.scalars(
                select(RunArtifact).where(
                    RunArtifact.run_id == run["run_id"],
                    RunArtifact.artifact_type == "REPLAY",
                    RunArtifact.state == "READY",
                )
            )
        )

    evidence = {
        "manifest": (manifest.status_code, manifest.text[:160]),
        "window": (window.status_code, window.text[:160]),
        "build_error": repr(build_error),
        "ready_artifacts": len(ready_artifacts),
    }
    assert manifest.status_code in {409, 500}, evidence
    assert window.status_code in {409, 500}, evidence
    assert isinstance(build_error, ServiceError), evidence
    assert build_error.code in {
        "RUN_STORAGE_INTEGRITY_ERROR",
        "REPLAY_FRAME_INTEGRITY_ERROR",
        "REPLAY_FRAME_OWNERSHIP_INVALID",
        "REPLAY_FRAME_INVALID",
    }, evidence
    assert not ready_artifacts, evidence
    assert str(var_dir) not in manifest.text and str(var_dir) not in window.text


def test_def_063_replay_cross_run_native_file_symlink_blocks_all_consumers(
    web_runtime,
):
    """ROL-ART/RPL a real file symlink cannot make another Run own a frame."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "replay-native-cross-run-owner"
    )
    stored = _project_replay_step(
        database,
        var_dir,
        run["run_id"],
        _rich_step(run["run_id"], claimed.attempt_id, 1),
    )
    original = stored.path.read_bytes()
    with database.session_factory.begin() as session:
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=1,
                result_state="PARTIAL",
                projection_version="native-symlink-release-gate",
                result_version=1,
            )
        )

    _other_experiment, _other_revision, other = _publish_run(
        database, var_dir, "replay-native-cross-run-target"
    )
    target_root = var_dir / "runs" / other["run_id"] / "frames"
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / "cross-run-frame.json.gz"
    target.write_bytes(original)
    link = stored.path.parent / "cross-run-frame.json.gz"
    _create_native_symlink_or_skip(link, target)
    with database.session_factory.begin() as session:
        session.get(RunStep, (run["run_id"], 1)).frame_path = (
            link.relative_to(var_dir).as_posix()
        )

    manifest = window = None
    build_error = None
    ready_artifacts: list[RunArtifact] = []
    try:
        manifest = client.get(f"/api/v1/runs/{run['run_id']}/replay/manifest")
        window = client.get(
            f"/api/v1/runs/{run['run_id']}/replay/steps",
            params={"from_step": 1, "limit": 1},
        )
        job = ArtifactService(database, var_dir=var_dir).create_job(
            run["run_id"], job_type="BUILD_REPLAY"
        )
        owned = ArtifactSchedulerRepository(database).claim_next()
        assert owned is not None and owned.job_id == job["job_id"]
        try:
            ArtifactBuilder(database, var_dir=var_dir).build(job["job_id"])
        except Exception as exc:
            build_error = exc
        with database.session_factory() as session:
            ready_artifacts = list(
                session.scalars(
                    select(RunArtifact).where(
                        RunArtifact.run_id == run["run_id"],
                        RunArtifact.artifact_type == "REPLAY",
                        RunArtifact.state == "READY",
                    )
                )
            )
    finally:
        link.unlink()

    assert target.read_bytes() == original
    assert manifest is not None and window is not None
    evidence = {
        "manifest": (manifest.status_code, manifest.text[:160]),
        "window": (window.status_code, window.text[:160]),
        "build_error": repr(build_error),
        "ready_artifacts": len(ready_artifacts),
    }
    assert manifest.status_code in {409, 500}, evidence
    assert window.status_code in {409, 500}, evidence
    assert isinstance(build_error, ServiceError), evidence
    assert build_error.code in {
        "RUN_STORAGE_INTEGRITY_ERROR",
        "REPLAY_FRAME_INTEGRITY_ERROR",
        "REPLAY_FRAME_OWNERSHIP_INVALID",
        "REPLAY_FRAME_INVALID",
    }, evidence
    assert str(var_dir) not in str(build_error)
    assert str(target) not in str(build_error)
    assert not ready_artifacts, evidence
    for response in (manifest, window):
        assert str(var_dir) not in response.text
        assert str(target) not in response.text


def test_rol_system_two_active_runs_isolate_log_checkpoint_artifact_and_replay(
    tmp_path: Path,
):
    """ROL-SYNC-001 system proof: two active Runs never share observable facts."""

    runtime_root = tmp_path.parent / f"rol-system-{uuid4().hex[:8]}"
    runtime_root.mkdir()
    var_dir = runtime_root / "var"
    app = create_app(
        database_url="sqlite:///" + (runtime_root / "rol.db").as_posix(),
        var_dir=str(var_dir),
        supervisor_enabled=False,
    )
    with TestClient(app) as client:
        database = app.state.database
        _ea, _ra, run_a = _publish_run(database, var_dir, "rol-system-a")
        _eb, _rb, run_b = _publish_run(database, var_dir, "rol-system-b")
        scheduler = LocalRunSchedulerRepository(database, max_concurrent_runs=2)
        attempt_a = scheduler.claim_next()
        attempt_b = scheduler.claim_next()
        assert attempt_a is not None and attempt_b is not None
        assert {attempt_a.run_id, attempt_b.run_id} == {
            run_a["run_id"],
            run_b["run_id"],
        }
        by_run = {attempt_a.run_id: attempt_a, attempt_b.run_id: attempt_b}
        attempt_a = by_run[run_a["run_id"]]
        attempt_b = by_run[run_b["run_id"]]
        assert attempt_a.slot_no != attempt_b.slot_no

        for run, attempt, marker, result in (
            (
                run_a,
                attempt_a,
                "ONLY-RUN-A",
                _rich_step(run_a["run_id"], attempt_a.attempt_id, 1),
            ),
            (
                run_b,
                attempt_b,
                "ONLY-RUN-B",
                _step(run_b["run_id"], attempt_b.attempt_id, 1),
            ),
        ):
            log_path = var_dir / attempt.log_path
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"INFO {marker}\n", encoding="utf-8")
            paths = RunPaths.under(var_dir, UUID(run["run_id"]))
            _write_checkpoint(paths, result)
            _project_replay_step(database, var_dir, run["run_id"], result, checkpoint=True)
            with database.session_factory.begin() as session:
                session.add(
                    RunResultSummary(
                        run_id=run["run_id"],
                        available_step=1,
                        result_state="PARTIAL",
                        projection_version="system-test",
                        result_version=1,
                    )
                )

        for own, other, attempt, marker, absent in (
            (run_a, run_b, attempt_a, "ONLY-RUN-A", "ONLY-RUN-B"),
            (run_b, run_a, attempt_b, "ONLY-RUN-B", "ONLY-RUN-A"),
        ):
            log = client.get(
                f"/api/v1/runs/{own['run_id']}/attempts/{attempt.attempt_id}/log"
            )
            assert log.status_code == 200, log.text
            assert marker in log.json()["content"] and absent not in log.json()["content"]
            crossed_log = client.get(
                f"/api/v1/runs/{other['run_id']}/attempts/{attempt.attempt_id}/log"
            )
            assert crossed_log.status_code == 404

            checkpoint = client.get(
                f"/api/v1/runs/{own['run_id']}/checkpoints/1"
            )
            assert checkpoint.status_code == 200, checkpoint.text
            assert checkpoint.json()["run_id"] == own["run_id"]
            assert checkpoint.json()["attempt_id"] == attempt.attempt_id

            replay = client.get(
                f"/api/v1/runs/{own['run_id']}/replay/steps",
                params={"from_step": 1, "limit": 1},
            )
            assert replay.status_code == 200, replay.text
            assert replay.json()["run_id"] == own["run_id"]
            assert replay.json()["steps"][0]["attempt_id"] == attempt.attempt_id

        artifacts = ArtifactService(database, var_dir=var_dir)
        jobs = {
            run["run_id"]: artifacts.create_job(
                run["run_id"], job_type="BUILD_REPLAY"
            )
            for run in (run_a, run_b)
        }
        artifact_ids = {}
        repository = ArtifactSchedulerRepository(database)
        for _ in range(2):
            claimed = repository.claim_next()
            assert claimed is not None
            artifact_ids[claimed.run_id] = ArtifactBuilder(
                database, var_dir=var_dir
            ).build(claimed.job_id)
        assert set(artifact_ids) == {run_a["run_id"], run_b["run_id"]}
        for own, other in ((run_a, run_b), (run_b, run_a)):
            artifact_id = artifact_ids[own["run_id"]]
            _row, path = artifacts.content(own["run_id"], artifact_id)
            document = json.loads(path.read_bytes())
            assert document["run_id"] == own["run_id"]
            assert other["run_id"] not in path.read_text(encoding="utf-8")
            crossed = client.get(
                f"/api/v1/runs/{other['run_id']}/artifacts/{artifact_id}/download"
            )
            assert crossed.status_code == 404
            assert path.is_relative_to(
                var_dir / "runs" / own["run_id"] / "artifacts"
            )


def test_def_054_running_replay_available_step_expands_without_stale_window(
    web_runtime,
):
    """ROL-RPL-003 a RUNNING player observes newly committed result versions."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, "replay-running-expansion"
    )
    first = _step(run["run_id"], claimed.attempt_id, 1)
    _project_replay_step(database, var_dir, run["run_id"], first)
    with database.session_factory.begin() as session:
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=1,
                result_state="PARTIAL",
                projection_version="test",
                result_version=1,
            )
        )
    before = client.get(
        f"/api/v1/runs/{run['run_id']}/replay/steps",
        params={"from_step": 1, "limit": 100},
    )
    assert before.status_code == 200, before.text
    assert before.json()["available_step"] == 1
    assert [item["step_no"] for item in before.json()["steps"]] == [1]

    second = _step(run["run_id"], claimed.attempt_id, 2)
    _project_replay_step(database, var_dir, run["run_id"], second)
    with database.session_factory.begin() as session:
        summary = session.get(RunResultSummary, run["run_id"])
        summary.available_step = 2
        summary.result_version = 2
    after = client.get(
        f"/api/v1/runs/{run['run_id']}/replay/steps",
        params={"from_step": 2, "limit": 100},
    )
    assert after.status_code == 200, after.text
    assert after.json()["available_step"] == 2
    assert after.json()["result_version"] == 2
    assert [item["step_no"] for item in after.json()["steps"]] == [2]


def test_def_060_real_legacy_movement_adapter_preserves_state_time_and_conversation(
    database, tmp_path: Path
):
    """ROL-ART-001/RPL-003 validates the shipped compressed fixture semantically."""

    source_root = ROOT / "generative_agents" / "results"
    movement_path = source_root / "compressed" / "example" / "movement.json"
    original_hash = hashlib.sha256(movement_path.read_bytes()).hexdigest()
    movement = json.loads(movement_path.read_text(encoding="utf-8"))
    importer = LegacyImportService(
        database, project_root=ROOT, var_dir=tmp_path / "var"
    )
    imported = importer.import_runs(apply=True, source_root=source_root)
    assert imported["failed"] == 0, imported
    imported_item = next(item for item in imported["items"] if item["name"] == "example")
    run_id = imported_item["run_id"]

    # Legacy frames are deltas.  A semantic adapter must carry state forward
    # through frame 60, not sample the usually-empty frame 60 in isolation.
    expected = {
        name: {
            "movement": list(coord),
            "location": "",
            "description": "",
        }
        for name, coord in movement["persona_init_pos"].items()
    }
    for frame_no in range(0, 61):
        for name, value in (movement["all_movement"].get(str(frame_no)) or {}).items():
            expected[name] = {**expected.get(name, {}), **value}

    with database.session_factory() as session:
        run = session.get(Run, run_id)
        revision = session.get(ExperimentRevision, run.revision_id)
        name_to_key = {
            item["name"]: item["agent_key"]
            for item in revision.definition_json["agents"]
        }
        second = list(
            session.scalars(
                select(RunAgentStep).where(
                    RunAgentStep.run_id == run_id, RunAgentStep.step_no == 2
                )
            )
        )
        by_key = {item.agent_key: item for item in second}
        assert len(by_key) == len(expected) == 25
        for name, fact in expected.items():
            item = by_key[name_to_key[name]]
            assert [item.x, item.y] == fact["movement"]
            assert item.address == fact["location"]
            assert item.action_text == fact["description"]
            assert item.virtual_time.isoformat().startswith("2024-02-13T06:10:00")

        conversations = list(
            session.scalars(
                select(RunConversation)
                .where(RunConversation.run_id == run_id)
                .order_by(RunConversation.started_at)
            )
        )
        messages = list(
            session.scalars(
                select(RunMessage)
                .where(RunMessage.run_id == run_id)
                .order_by(RunMessage.observed_at, RunMessage.sequence_no)
            )
        )
        assert conversations, "all_movement.conversation must not be discarded"
        assert messages
        assert messages[0].content.startswith("早上好")
        assert messages[0].observed_at.isoformat().startswith("2024-02-13T06:00:00")
    assert hashlib.sha256(movement_path.read_bytes()).hexdigest() == original_hash


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
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *args):
            return self._handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self._handle, name)

        def read(self, size=-1):
            assert 0 <= size <= limit + 4, (
                "byte-window reader attempted an unbounded or oversized physical read"
            )
            read_sizes.append(size)
            return self._handle.read(size)

    def guarded_open(target, *args, **kwargs):
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


def test_def_069_cross_web_restart_resume_reuses_the_original_run_manifest(
    database, tmp_path: Path, monkeypatch
):
    """ROL-CHK-003: a later Attempt consumes, but never rematerializes, a Run manifest."""

    import generative_agents.runtime.supervisor as supervisor_module

    var_dir = tmp_path / "var"
    _experiment, revision, run = _publish_run(database, var_dir, "resume-manifest")
    scheduler = LocalRunSchedulerRepository(database)
    first_claim = scheduler.claim_next()
    assert first_claim is not None

    first_time = datetime(2026, 8, 9, 1, 2, 3, tzinfo=timezone.utc)
    second_time = first_time + timedelta(hours=1)

    class FirstWebClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return first_time

    class RestartedWebClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return second_time

    monkeypatch.setattr(supervisor_module, "datetime", FirstWebClock)
    first_web = LocalProcessSupervisor(
        database,
        var_dir=var_dir,
        code_build_id="stable-test-build",
    )
    first_web._materialize_manifest(first_claim)
    paths = RunPaths.under(var_dir, UUID(run["run_id"]))
    manifest_store = RunManifestStore(paths)
    before_bytes = paths.manifest.read_bytes()
    before = manifest_store.load_verified().document
    assert before["materialized_at"] == first_time.isoformat()

    # Prove the precise unstable field in the old implementation.  Rebuilding
    # otherwise identical inputs on Web restart changes only the creation time
    # and the hash derived from it; neither belongs to a later Attempt.
    rebuilt = build_manifest_document(
        run_id=UUID(run["run_id"]),
        experiment_id=UUID(run["experiment_id"]),
        revision_id=UUID(revision["id"]),
        definition=ExperimentDefinition.model_validate(before["definition"]),
        expected_definition_hash=before["definition_hash"],
        code_build_id=before["code_build_id"],
        assets=before["assets"],
        materialized_at=second_time,
        dependency_versions=before["dependency_versions"],
        workflows=before.get("workflows"),
    )
    assert {
        key for key in before if before[key] != rebuilt[key]
    } == {"materialized_at", "manifest_hash"}

    # Commit one real recoverable boundary and pause Attempt 1.
    assert scheduler.register_worker(first_claim, pid=os.getpid(), pid_create_time=1.0)
    step = _step(run["run_id"], first_claim.attempt_id, 1)
    frame = FrameStore(paths).write(step)
    checkpoint_path = CheckpointBundleWriter(
        paths,
        lambda result: CheckpointSnapshot(
            state={"virtual_time": result.virtual_time.isoformat(), "agents": {}},
            conversation={},
        ),
    ).write(step, frame)
    SqliteResultProjector(database, var_dir=var_dir).commit_step(
        step, frame=frame, checkpoint_path=checkpoint_path
    )
    with database.session_factory.begin() as session:
        row = session.get(Run, run["run_id"])
        row.status = "PAUSE_REQUESTED"
    assert scheduler.finish_worker(run["run_id"], first_claim.attempt_id, exit_code=0)
    resumed = RunService(database, var_dir=var_dir).resume_paused(run["run_id"])
    assert resumed["status"] == "QUEUED"
    second_claim = scheduler.claim_next()
    assert second_claim is not None
    assert second_claim.run_id == run["run_id"]
    assert second_claim.attempt_no == 2
    assert second_claim.start_step == 2

    # This represents a fresh Web process.  The immutable manifest belongs to
    # the Run, not to Attempt 1 or the first server process.
    monkeypatch.setattr(supervisor_module, "datetime", RestartedWebClock)
    restarted_web = LocalProcessSupervisor(
        database,
        var_dir=var_dir,
        code_build_id="stable-test-build",
    )
    restarted_web._materialize_manifest(second_claim)
    assert paths.manifest.read_bytes() == before_bytes
    assert manifest_store.load_verified().document["manifest_hash"] == before["manifest_hash"]

    # Negative control: reuse must not weaken the immutable store.  A genuinely
    # different code build document for the same Run remains a hard conflict.
    incompatible = build_manifest_document(
        run_id=UUID(run["run_id"]),
        experiment_id=UUID(run["experiment_id"]),
        revision_id=UUID(revision["id"]),
        definition=ExperimentDefinition.model_validate(before["definition"]),
        expected_definition_hash=before["definition_hash"],
        code_build_id="different-test-build",
        assets=before["assets"],
        materialized_at=first_time,
        dependency_versions=before["dependency_versions"],
        workflows=before.get("workflows"),
    )
    with pytest.raises(ManifestConflictError):
        manifest_store.materialize(incompatible)


def test_def_070_legacy_checkpoint_memory_dates_resume_with_an_aware_clock():
    """ROL-REC-001: pre-timezone memory metadata remains comparable after resume."""

    clock = SimulationClock(
        datetime(2026, 8, 9, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    )
    nodes = {
        "active": SimpleNamespace(
            metadata={
                "create": "20260809-08:00:00",
                "expire": "20260809-10:00:00",
            }
        ),
        "expired": SimpleNamespace(
            metadata={
                "create": "20260809-07:00:00",
                "expire": "20260809-08:30:00",
            }
        ),
        "future": SimpleNamespace(
            metadata={
                "create": "20260809-10:00:00",
                "expire": "20260809-11:00:00",
            }
        ),
    }
    removed: list[str] = []

    class LegacyCheckpointIndex:
        docstore = SimpleNamespace(docs=nodes)

        def delete_nodes(self, node_ids, *, delete_from_docstore=True):
            assert delete_from_docstore is True
            removed.extend(node_ids)
            for node_id in node_ids:
                nodes.pop(node_id, None)

    restored = object.__new__(LlamaIndex)
    restored._clock = clock
    restored._index = LegacyCheckpointIndex()

    # Old checkpoints serialized create/expire without an offset.  Resume must
    # interpret those values in the aware simulation clock's timezone, not mix
    # naive and aware datetime objects or silently change retention semantics.
    assert set(restored.cleanup()) == {"expired", "future"}
    assert removed == ["expired", "future"]
    assert list(nodes) == ["active"]


def test_def_071_zero_model_call_failure_does_not_project_a_missing_trace_file(
    monkeypatch, tmp_path: Path
):
    """ROL-TRACE-001/REC-001: cleanup preserves the primary worker failure."""

    run_id, attempt_id = uuid4(), uuid4()
    var_dir = tmp_path / "var"
    args = SimpleNamespace(
        database_url="sqlite:///unused.db",
        var_dir=str(var_dir),
        run_id=run_id,
        attempt_id=attempt_id,
        start_step=95,
    )
    calls: list[tuple] = []

    class FakeParser:
        @staticmethod
        def parse_args(_argv):
            return args

    class FakeDatabase:
        engine = SimpleNamespace(url="sqlite:///unused.db")

        def close(self):
            calls.append(("database-close",))

    class FakeRepository:
        def __init__(self, _database):
            pass

        def heartbeat(self, _run_id, _attempt_id):
            return "RUNNING"

        def finish_worker(
            self,
            _run_id,
            _attempt_id,
            *,
            exit_code,
            error_code=None,
            error_message=None,
        ):
            calls.append(("finish", exit_code, error_code, error_message))
            return True

    class FakeLock:
        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self):
            calls.append(("lock-acquire",))

        def release(self):
            calls.append(("lock-release",))

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

        def join(self, timeout=None):
            self.started = False

    class FakeLogger:
        def exception(self, message):
            calls.append(("exception", message))

    class FakeManifestStore:
        def __init__(self, _paths):
            pass

        def load_verified(self):
            return SimpleNamespace(
                definition=_definition("zero-model-call"),
                document={
                    "experiment_id": str(uuid4()),
                    "revision_id": str(uuid4()),
                    "definition_hash": "definition-hash",
                },
                manifest_hash="manifest-hash",
                workflows={},
            )

    class FakeMasterKeyStore:
        def __init__(self, _var_dir):
            pass

        @staticmethod
        def load_or_create():
            return b"test-master-key"

    class FakeTraceWriter:
        def __init__(self, paths, **_kwargs):
            self.path = paths.traces / "model-calls-003.jsonl"
            assert not self.path.exists()

    class ForbiddenProjector:
        def __init__(self, *_args, **_kwargs):
            pass

        def project(self, **_kwargs):
            calls.append(("project-missing-trace",))
            raise AssertionError("a zero-call Attempt has no trace file to project")

    monkeypatch.setattr(worker, "_parser", lambda: FakeParser())
    monkeypatch.setattr(worker, "create_database", lambda _url: FakeDatabase())
    monkeypatch.setattr(worker, "LocalRunSchedulerRepository", FakeRepository)
    monkeypatch.setattr(worker, "FileLock", FakeLock)
    monkeypatch.setattr(worker.threading, "Thread", FakeThread)
    monkeypatch.setattr(worker, "_logger", lambda *_args: FakeLogger())
    monkeypatch.setattr(worker, "RunManifestStore", FakeManifestStore)
    monkeypatch.setattr(worker, "MasterKeyStore", FakeMasterKeyStore)
    monkeypatch.setattr(worker, "SecretCipher", lambda _key: object())
    monkeypatch.setattr(worker, "ModelTraceWriter", FakeTraceWriter)
    monkeypatch.setattr(worker, "_secret_value", lambda *_args: "secret")
    monkeypatch.setattr(worker, "_attempt_no", lambda *_args: 3)
    monkeypatch.setattr(worker, "ModelTraceProjector", ForbiddenProjector)

    def fail_before_first_model_call(*_args, **_kwargs):
        raise TypeError("primary legacy checkpoint datetime failure")

    monkeypatch.setattr(worker, "_prepare_attempt_state", fail_before_first_model_call)

    assert worker.main([]) == 1
    assert ("exception", "worker attempt failed") in calls
    assert ("project-missing-trace",) not in calls
    assert ("exception", "final model trace projection failed") not in calls
    assert (
        "finish",
        1,
        "WORKER_EXECUTION_FAILED",
        "primary legacy checkpoint datetime failure",
    ) in calls
