"""运行时回归测试：覆盖 ``test_runtime_contracts`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from generative_agents.ga_protocol.schemas.engine import get_algorithm_profile
from generative_agents.ga_runtime.engine.context import RunPaths
from generative_agents.ga_runtime.storage.checkpoints import CheckpointBundleWriter
from generative_agents.ga_runtime.storage.checkpoints import CheckpointSnapshot
from generative_agents.ga_runtime.storage.frames import FrameConflictError
from generative_agents.ga_runtime.storage.frames import FrameStore
from generative_agents.ga_protocol.schemas.facts import ActionSnapshot
from generative_agents.ga_protocol.schemas.facts import ActivityKind
from generative_agents.ga_protocol.schemas.facts import AgentStepResult
from generative_agents.ga_runtime.engine.results import StepResultBuilder
from generative_agents.ga_runtime.models.trace import ModelTraceEvent
from generative_agents.ga_runtime.models.trace import ModelTraceEventType
from generative_agents.ga_runtime.models.trace import ModelTraceStatus
from generative_agents.ga_runtime.models.trace import ModelTraceWriter


def _builder(run_id, attempt_id, step_no=1):
    """为本测试模块封装 ``_builder`` 辅助步骤，减少重复的场景搭建代码。"""
    return StepResultBuilder(
        run_id=run_id,
        attempt_id=attempt_id,
        step_no=step_no,
        virtual_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _agent(key: str, x: int) -> AgentStepResult:
    """为本测试模块封装 ``_agent`` 辅助步骤，减少重复的场景搭建代码。"""
    return AgentStepResult(
        agent_key=key,
        from_coord=(x, 0),
        to_coord=(x + 1, 0),
        path=((x, 0), (x + 1, 0)),
        action=ActionSnapshot(description="walk"),
        activity_kind=ActivityKind.MOVING,
        location=("world", "street"),
    )


def test_algorithm_profile_is_versioned_and_fixed():
    """回归验证 ``test_algorithm_profile_is_versioned_and_fixed`` 所描述的业务结果、故障边界和隔离约束。"""
    profile = get_algorithm_profile("ga-cn-v1")
    assert profile.movement_tiles_per_minute == 4
    assert "sentence_chunk_size" not in profile.as_dict()
    assert "chat_chars_per_minute" not in profile.as_dict()
    with pytest.raises(ValueError, match="unsupported algorithm_version"):
        get_algorithm_profile("future")


def test_builder_stably_sorts_and_rejects_writes_after_freeze():
    """回归验证 ``test_builder_stably_sorts_and_rejects_writes_after_freeze`` 所描述的业务结果、故障边界和隔离约束。"""
    builder = _builder(uuid4(), uuid4())
    builder.add_agent(_agent("z-agent", 2))
    builder.add_agent(_agent("a-agent", 1))

    result = builder.freeze()

    assert [item.agent_key for item in result.agents] == ["a-agent", "z-agent"]
    with pytest.raises(RuntimeError, match="already frozen"):
        builder.add_agent(_agent("late", 3))


def test_frame_store_is_idempotent_and_rejects_cross_run_or_rewrite(tmp_path):
    """回归验证 ``test_frame_store_is_idempotent_and_rejects_cross_run_or_rewrite`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    store = FrameStore(paths)
    builder = _builder(run_id, uuid4())
    builder.add_agent(_agent("agent", 1))
    result = builder.freeze()

    first = store.write(result)
    second = store.write(result)

    assert first.created is True
    assert second.created is False
    assert first.sha256 == second.sha256
    assert store.read_document(1)["result"]["agents"][0]["path_source"] == "OBSERVED"
    assert type(result).from_dict(store.read_document(1)["result"]) == result

    changed_builder = _builder(run_id, result.attempt_id)
    changed_builder.add_agent(_agent("different", 5))
    with pytest.raises(FrameConflictError):
        store.write(changed_builder.freeze())

    cross_run_builder = _builder(uuid4(), uuid4(), step_no=2)
    with pytest.raises(ValueError, match="does not own"):
        store.write(cross_run_builder.freeze())


def test_result_rejects_naive_virtual_time():
    """回归验证 ``test_result_rejects_naive_virtual_time`` 所描述的业务结果、故障边界和隔离约束。"""
    builder = StepResultBuilder(
        run_id=uuid4(),
        attempt_id=uuid4(),
        step_no=1,
        virtual_time=datetime(2026, 1, 1),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        builder.freeze()


def test_checkpoint_bundle_is_verified_and_latest_is_idempotent(tmp_path):
    """回归验证 ``test_checkpoint_bundle_is_verified_and_latest_is_idempotent`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    store = FrameStore(paths)
    builder = _builder(run_id, uuid4())
    builder.add_agent(_agent("agent", 1))
    result = builder.freeze()
    frame = store.write(result)

    def export_runtime(target):
        """为本测试模块封装 ``export_runtime`` 辅助步骤，减少重复的场景搭建代码。"""
        (target / "memory.sqlite").write_bytes(b"run-scoped-memory")

    writer = CheckpointBundleWriter(
        paths,
        lambda _: CheckpointSnapshot(
            state={"step": 1, "agents": {"agent": {"coord": [2, 0]}}},
            conversation={},
            runtime_storage_exporters={"skill-memory": export_runtime},
        ),
    )
    checkpoint = writer.write(result, frame)

    assert checkpoint.name == "step-000001"
    assert writer.read_latest().path == checkpoint.resolve()
    assert (
        checkpoint / "runtime-storage/skill-memory/memory.sqlite"
    ).read_bytes() == b"run-scoped-memory"
    assert writer.write(result, frame) == checkpoint


def test_checkpoint_publish_retries_transient_windows_access_denied(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    (source / "complete.txt").write_text("complete", encoding="utf-8")
    original_rename = os.rename
    attempts = 0

    def transient_rename(old, new):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            error = PermissionError("temporarily held by Windows")
            error.winerror = 5
            raise error
        return original_rename(old, new)

    monkeypatch.setattr(os, "rename", transient_rename)
    monkeypatch.setattr("generative_agents.ga_runtime.storage.checkpoints.time.sleep", lambda _: None)

    CheckpointBundleWriter._publish_directory(source, target)

    assert attempts == 2
    assert not source.exists()
    assert (target / "complete.txt").read_text(encoding="utf-8") == "complete"


def test_checkpoint_rejects_unsafe_agent_storage_key(tmp_path):
    """回归验证 ``test_checkpoint_rejects_unsafe_agent_storage_key`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    result = _builder(run_id, uuid4()).freeze()
    frame = FrameStore(paths).write(result)
    writer = CheckpointBundleWriter(
        paths,
        lambda _: CheckpointSnapshot(
            state={},
            conversation={},
            storage_exporters={"../escape": lambda target: None},
        ),
    )
    with pytest.raises(ValueError, match="unsafe agent_key"):
        writer.write(result, frame)


def test_checkpoint_recovers_by_scanning_and_retains_only_configured_bundles(tmp_path):
    """回归验证 ``test_checkpoint_recovers_by_scanning_and_retains_only_configured_bundles`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id = uuid4()
    attempt_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    store = FrameStore(paths)
    writer = CheckpointBundleWriter(
        paths,
        lambda result: CheckpointSnapshot(
            state={"step": result.step_no}, conversation={}
        ),
        retention=3,
    )
    for step_no in range(1, 5):
        result = _builder(run_id, attempt_id, step_no=step_no).freeze()
        writer.write(result, store.write(result))

    assert not (paths.checkpoints / "step-000001").exists()
    (paths.checkpoints / "LATEST").write_text("not-json", encoding="utf-8")
    (paths.checkpoints / "step-000004" / "state.json").write_text(
        "corrupted", encoding="utf-8"
    )

    recovered = writer.read_latest()

    assert recovered is not None
    assert recovered.path.name == "step-000003"


def test_checkpoint_prune_defers_sharing_violation_without_partial_deletion(
    tmp_path, monkeypatch
):
    """回归验证 ``test_checkpoint_prune_defers_sharing_violation_without_partial_deletion`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id = uuid4()
    attempt_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    store = FrameStore(paths)

    def snapshot(result):
        """为本测试模块封装 ``snapshot`` 辅助步骤，减少重复的场景搭建代码。"""
        def export(target):
            """为本测试模块封装 ``export`` 辅助步骤，减少重复的场景搭建代码。"""
            (target / "index_store.json").write_text(
                f'{{"step":{result.step_no}}}', encoding="utf-8"
            )

        return CheckpointSnapshot(
            state={"step": result.step_no},
            conversation={},
            storage_exporters={"resident-001": export},
        )

    writer = CheckpointBundleWriter(paths, snapshot, retention=2)
    for step_no in (1, 2):
        result = _builder(run_id, attempt_id, step_no=step_no).freeze()
        writer.write(result, store.write(result))

    original_replace = os.replace
    block_oldest = True

    def replace_with_sharing_violation(source, destination):
        """为本测试模块封装 ``replace_with_sharing_violation`` 辅助步骤，减少重复的场景搭建代码。"""
        if block_oldest and Path(source).name == "step-000001":
            error = PermissionError("checkpoint member is in use")
            error.winerror = 32
            raise error
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace_with_sharing_violation)
    result3 = _builder(run_id, attempt_id, step_no=3).freeze()
    assert writer.write(result3, store.write(result3)).name == "step-000003"

    # A failed retirement rename leaves the public checkpoint whole and valid;
    # in particular it never reproduces the old half-deleted storage tree.
    oldest = paths.checkpoints / "step-000001"
    assert oldest.is_dir()
    assert (oldest / "storage/resident-001/associate/index_store.json").is_file()
    assert writer.validate(oldest).path == oldest.resolve()

    block_oldest = False
    result4 = _builder(run_id, attempt_id, step_no=4).freeze()
    writer.write(result4, store.write(result4))
    assert not oldest.exists()
    assert not list(paths.checkpoints.glob(".prune-*.tmp"))


def test_checkpoint_access_lock_serializes_reader_and_retention(tmp_path):
    """回归验证 ``test_checkpoint_access_lock_serializes_reader_and_retention`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id = uuid4()
    attempt_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    store = FrameStore(paths)
    writer = CheckpointBundleWriter(
        paths,
        lambda result: CheckpointSnapshot(
            state={"step": result.step_no}, conversation={}
        ),
        retention=2,
    )
    reader = CheckpointBundleWriter(
        paths, lambda _: CheckpointSnapshot(state={}, conversation={}), retention=2
    )
    for step_no in (1, 2):
        result = _builder(run_id, attempt_id, step_no=step_no).freeze()
        writer.write(result, store.write(result))

    reader_ready = threading.Event()
    release_reader = threading.Event()
    write_finished = threading.Event()

    def hold_validated_bundle():
        """为本测试模块封装 ``hold_validated_bundle`` 辅助步骤，减少重复的场景搭建代码。"""
        with reader.access():
            reader.validate(paths.checkpoints / "step-000001")
            reader_ready.set()
            assert release_reader.wait(timeout=5)

    def publish_next_step():
        """为本测试模块封装 ``publish_next_step`` 辅助步骤，减少重复的场景搭建代码。"""
        result = _builder(run_id, attempt_id, step_no=3).freeze()
        writer.write(result, store.write(result))
        write_finished.set()

    reader_thread = threading.Thread(target=hold_validated_bundle)
    writer_thread = threading.Thread(target=publish_next_step)
    reader_thread.start()
    assert reader_ready.wait(timeout=5)
    writer_thread.start()
    time.sleep(0.15)

    assert not write_finished.is_set()
    assert (paths.checkpoints / "step-000001").is_dir()

    release_reader.set()
    reader_thread.join(timeout=5)
    writer_thread.join(timeout=5)
    assert not reader_thread.is_alive()
    assert not writer_thread.is_alive()
    assert write_finished.is_set()
    assert not (paths.checkpoints / "step-000001").exists()


def test_checkpoint_retention_survives_a_live_member_file_handle(tmp_path):
    """Exercise the real host filesystem, including Windows sharing rules."""

    run_id = uuid4()
    attempt_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    store = FrameStore(paths)

    def snapshot(result):
        """为本测试模块封装 ``snapshot`` 辅助步骤，减少重复的场景搭建代码。"""
        def export(target):
            """为本测试模块封装 ``export`` 辅助步骤，减少重复的场景搭建代码。"""
            (target / "index_store.json").write_text("{}", encoding="utf-8")

        return CheckpointSnapshot(
            state={"step": result.step_no},
            conversation={},
            storage_exporters={"resident-013": export},
        )

    writer = CheckpointBundleWriter(paths, snapshot, retention=2)
    for step_no in (1, 2):
        result = _builder(run_id, attempt_id, step_no=step_no).freeze()
        writer.write(result, store.write(result))

    live_member = (
        paths.checkpoints
        / "step-000001/storage/resident-013/associate/index_store.json"
    )
    with live_member.open("rb") as handle:
        assert handle.read(1) == b"{"
        result3 = _builder(run_id, attempt_id, step_no=3).freeze()
        assert writer.write(result3, store.write(result3)).name == "step-000003"

        # Platforms differ on whether a directory containing an open member can
        # be renamed/unlinked.  Both safe outcomes exclude a partially deleted
        # public checkpoint: it is either still fully valid or already private.
        oldest = paths.checkpoints / "step-000001"
        if oldest.exists():
            assert writer.validate(oldest).path == oldest.resolve()

    result4 = _builder(run_id, attempt_id, step_no=4).freeze()
    writer.write(result4, store.write(result4))
    assert not (paths.checkpoints / "step-000001").exists()
    assert not list(paths.checkpoints.glob(".prune-*.tmp"))


def test_model_trace_is_attempt_scoped_contiguous_and_redacted(tmp_path):
    """回归验证 ``test_model_trace_is_attempt_scoped_contiguous_and_redacted`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id = uuid4()
    attempt_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    writer = ModelTraceWriter(
        paths,
        run_id=run_id,
        attempt_id=attempt_id,
        attempt_no=1,
        capture_payloads=False,
    )
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    event = ModelTraceEvent(
        event_type=ModelTraceEventType.PHYSICAL_ATTEMPT,
        run_id=run_id,
        attempt_id=attempt_id,
        call_id=uuid4(),
        step_no=1,
        agent_key="agent",
        purpose="schedule",
        prompt_key="schedule_init",
        provider="vllm",
        resolved_model="test-model",
        started_at=started_at,
        ended_at=started_at,
        latency_ms=0,
        attempt_no=1,
        status=ModelTraceStatus.FAILED,
        error_summary="Authorization: Bearer sk-super-secret-token",
        payload={"prompt": "must not be persisted"},
    )

    assert writer.append(event) == 1
    reopened = ModelTraceWriter(
        paths,
        run_id=run_id,
        attempt_id=attempt_id,
        attempt_no=1,
        capture_payloads=False,
    )
    assert reopened.append(event) == 2
    content = writer.path.read_text(encoding="utf-8")
    assert "must not be persisted" not in content
    assert "sk-super-secret-token" not in content
    assert "[REDACTED]" in content
