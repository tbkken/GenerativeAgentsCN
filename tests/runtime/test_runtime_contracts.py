from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from generative_agents.runtime.algorithm import get_algorithm_profile
from generative_agents.runtime.context import RunPaths, WorkflowPromptRepository
from generative_agents.runtime.checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from generative_agents.runtime.frame_store import FrameConflictError, FrameStore
from generative_agents.runtime.results import (
    ActionSnapshot,
    ActivityKind,
    AgentStepResult,
    StepResultBuilder,
)
from generative_agents.config import (
    canonical_json_bytes,
    definition_hash,
    make_default_workflows,
)
from generative_agents.config.schema import ExperimentDefinition, make_blank_definition
from generative_agents.runtime.manifest import (
    ManifestConflictError,
    RunManifestStore,
    build_manifest_document,
)
from generative_agents.runtime.model_trace import (
    ModelTraceEvent,
    ModelTraceEventType,
    ModelTraceStatus,
    ModelTraceWriter,
)


def _builder(run_id, attempt_id, step_no=1):
    return StepResultBuilder(
        run_id=run_id,
        attempt_id=attempt_id,
        step_no=step_no,
        virtual_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _agent(key: str, x: int) -> AgentStepResult:
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
    profile = get_algorithm_profile("ga-cn-v1")
    assert profile.sentence_chunk_size == 512
    assert profile.chat_chars_per_minute == 240
    with pytest.raises(ValueError, match="unsupported algorithm_version"):
        get_algorithm_profile("future")


def test_builder_stably_sorts_and_rejects_writes_after_freeze():
    builder = _builder(uuid4(), uuid4())
    builder.add_agent(_agent("z-agent", 2))
    builder.add_agent(_agent("a-agent", 1))

    result = builder.freeze()

    assert [item.agent_key for item in result.agents] == ["a-agent", "z-agent"]
    with pytest.raises(RuntimeError, match="already frozen"):
        builder.add_agent(_agent("late", 3))


def test_frame_store_is_idempotent_and_rejects_cross_run_or_rewrite(tmp_path):
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
    builder = StepResultBuilder(
        run_id=uuid4(),
        attempt_id=uuid4(),
        step_no=1,
        virtual_time=datetime(2026, 1, 1),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        builder.freeze()


def test_checkpoint_bundle_is_verified_and_latest_is_idempotent(tmp_path):
    run_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    store = FrameStore(paths)
    builder = _builder(run_id, uuid4())
    builder.add_agent(_agent("agent", 1))
    result = builder.freeze()
    frame = store.write(result)

    writer = CheckpointBundleWriter(
        paths,
        lambda _: CheckpointSnapshot(
            state={"step": 1, "agents": {"agent": {"coord": [2, 0]}}},
            conversation={},
        ),
    )
    checkpoint = writer.write(result, frame)

    assert checkpoint.name == "step-000001"
    assert writer.read_latest().path == checkpoint.resolve()
    assert writer.write(result, frame) == checkpoint


def test_checkpoint_rejects_unsafe_agent_storage_key(tmp_path):
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
    run_id = uuid4()
    attempt_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    store = FrameStore(paths)

    def snapshot(result):
        def export(target):
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
        with reader.access():
            reader.validate(paths.checkpoints / "step-000001")
            reader_ready.set()
            assert release_reader.wait(timeout=5)

    def publish_next_step():
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
        def export(target):
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


def test_run_manifest_is_verified_and_immutable(tmp_path):
    import hashlib

    run_id = uuid4()
    experiment_id = uuid4()
    revision_id = uuid4()
    definition = make_blank_definition(key="manifest-test", name="Manifest Test")
    document = build_manifest_document(
        run_id=run_id,
        experiment_id=experiment_id,
        revision_id=revision_id,
        definition=definition,
        expected_definition_hash=definition_hash(definition),
        code_build_id="test-build",
        assets=[],
        materialized_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        dependency_versions={"pydantic": "test"},
    )
    store = RunManifestStore(RunPaths.under(tmp_path, run_id))

    written = store.materialize(document)

    assert written.definition.experiment.key == "manifest-test"
    assert store.load_verified().manifest_hash == document["manifest_hash"]
    changed = dict(document)
    changed["code_build_id"] = "different-build"
    unsigned = dict(changed)
    unsigned.pop("manifest_hash")
    changed["manifest_hash"] = hashlib.sha256(
        canonical_json_bytes(unsigned)
    ).hexdigest()
    with pytest.raises(ManifestConflictError):
        store.materialize(changed)


def test_run_manifest_resume_reuses_provenance_but_rejects_definition_change(tmp_path):
    run_id = uuid4()
    experiment_id = uuid4()
    revision_id = uuid4()
    definition = make_blank_definition(key="resume-manifest", name="Resume Manifest")
    definition_digest = definition_hash(definition)
    document = build_manifest_document(
        run_id=run_id,
        experiment_id=experiment_id,
        revision_id=revision_id,
        definition=definition,
        expected_definition_hash=definition_digest,
        code_build_id="first-service-build",
        assets=[],
        materialized_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        dependency_versions={"pydantic": "first-service-version"},
    )
    store = RunManifestStore(RunPaths.under(tmp_path, run_id))
    store.materialize(document)
    before = store.load_verified().path.read_bytes()

    reused = store.reuse_for_revision(
        experiment_id=experiment_id,
        revision_id=revision_id,
        definition=definition,
        expected_definition_hash=definition_digest,
        assets=[],
    )

    assert reused.document["code_build_id"] == "first-service-build"
    assert reused.document["materialized_at"] == "2026-01-01T00:00:00+00:00"
    assert reused.path.read_bytes() == before

    changed_payload = definition.model_dump(mode="json", exclude_none=False)
    changed_payload["experiment"]["goal"] = "material definition changed"
    changed_definition = ExperimentDefinition.model_validate(changed_payload)
    with pytest.raises(ManifestConflictError):
        store.reuse_for_revision(
            experiment_id=experiment_id,
            revision_id=revision_id,
            definition=changed_definition,
            expected_definition_hash=definition_hash(changed_definition),
            assets=[],
        )


def test_run_manifest_pins_workflow_bundle_and_runtime_prompt_placement(tmp_path):
    import copy
    import hashlib

    run_id = uuid4()
    experiment_id = uuid4()
    revision_id = uuid4()
    definition = make_blank_definition(key="workflow-manifest", name="Workflow Manifest")
    workflows = make_default_workflows()
    function_sources = {
        "custom_normalize": (
            "def main(inputs, context):\n"
            "    return {'result': inputs.get('input')}\n"
        )
    }
    document = build_manifest_document(
        run_id=run_id,
        experiment_id=experiment_id,
        revision_id=revision_id,
        definition=definition,
        expected_definition_hash=definition_hash(definition),
        code_build_id="workflow-build",
        assets=[],
        materialized_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        dependency_versions={},
        workflows=workflows,
        workflow_functions=function_sources,
    )
    store = RunManifestStore(RunPaths.under(tmp_path, run_id))
    verified = store.materialize(document)
    assert set(verified.workflows) == set(workflows)
    assert verified.workflow_functions == function_sources
    repository = WorkflowPromptRepository(
        {
            **{key: value.content for key, value in definition.prompts.items()},
            "unused_optional_prompt": "not placed and never executed",
        },
        verified.workflows,
    )
    assert repository.node_for_prompt("decide_chat") == (
        "social",
        "prompt_decide_chat",
    )
    assert repository.config_for_prompt("decide_chat")["retry_policy"] == {
        "max_attempts": 3,
        "retry_on_schema_error": True,
    }
    with pytest.raises(KeyError, match="not placed"):
        repository.get("unused_optional_prompt")

    tampered = copy.deepcopy(document)
    tampered["workflows"]["social"]["title"] = "tampered"
    unsigned = dict(tampered)
    unsigned.pop("manifest_hash")
    tampered["manifest_hash"] = hashlib.sha256(
        canonical_json_bytes(unsigned)
    ).hexdigest()
    tampered_run_id = uuid4()
    tampered_store = RunManifestStore(RunPaths.under(tmp_path, tampered_run_id))
    tampered["run_id"] = str(tampered_run_id)
    unsigned = dict(tampered)
    unsigned.pop("manifest_hash")
    tampered["manifest_hash"] = hashlib.sha256(
        canonical_json_bytes(unsigned)
    ).hexdigest()
    with pytest.raises(ValueError, match="workflow_bundle_hash mismatch"):
        tampered_store.materialize(tampered)

    function_tampered = copy.deepcopy(document)
    function_tampered["workflow_functions"]["custom_normalize"] += "# changed\n"
    function_tampered_run_id = uuid4()
    function_tampered["run_id"] = str(function_tampered_run_id)
    unsigned = dict(function_tampered)
    unsigned.pop("manifest_hash")
    function_tampered["manifest_hash"] = hashlib.sha256(
        canonical_json_bytes(unsigned)
    ).hexdigest()
    with pytest.raises(ValueError, match="workflow_function_bundle_hash mismatch"):
        RunManifestStore(
            RunPaths.under(tmp_path, function_tampered_run_id)
        ).materialize(function_tampered)


def test_model_trace_is_attempt_scoped_contiguous_and_redacted(tmp_path):
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
