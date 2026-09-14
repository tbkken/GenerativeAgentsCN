"""Execute one Run directory using only its embedded experiment and files."""

from __future__ import annotations

import logging
import random
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from filelock import FileLock

from generative_agents.ga_protocol import (
    AttemptRecord,
    AttemptState,
    PackageError,
    RunState,
    RunStatus,
    atomic_write_json,
    read_json,
    validate_run_directory,
)
from generative_agents.ga_protocol.quality import project_run_quality
from generative_agents.runtime.algorithm import get_algorithm_profile
from generative_agents.runtime.brain import BrainRuntime
from generative_agents.runtime.object_skills import ObjectSkillRuntime
from generative_agents.runtime.checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from generative_agents.runtime.context import (
    RunPaths,
    RecoveryPaths,
    SimulationClock,
    SimulationContext,
    SnapshotSkillInstructionRepository,
)
from generative_agents.runtime.model_trace import ModelTraceWriter
from generative_agents.skills import SnapshotSkillRegistry
from generative_agents.start import build_runner

from .control import FileRunControl
from .memory import FileMemoryStream
from .package import LoadedExperiment, load_experiment_directory


class RuntimeModelRegistry:
    def __init__(self, config: dict, recorder, *, control, logger) -> None:
        self.config = dict(config)
        self.recorder = recorder
        self.control = control
        self.logger = logger
        self._chat = None

    def get(self, purpose: str):
        if purpose != "chat":
            raise KeyError(f"unsupported model purpose: {purpose}")
        if self._chat is None:
            from generative_agents.modules.model.llm_model import create_llm_model

            self._chat = create_llm_model(
                self.config,
                recorder=self.recorder,
                control=self.control,
                logger=self.logger,
            )
        return self._chat


class _StatusCommitter:
    def __init__(self, inner, status_path: Path, total_steps: int) -> None:
        self.inner = inner
        self.status_path = status_path
        self.total_steps = total_steps

    def commit(self, result, *, force_checkpoint: bool):
        committed = self.inner.commit(result, force_checkpoint=force_checkpoint)
        status = RunStatus.model_validate(read_json(self.status_path))
        status.committed_step = result.step_no
        status.updated_at = datetime.now(UTC)
        status.status = RunState.RUNNING
        atomic_write_json(self.status_path, status.model_dump(mode="json"))
        return committed


def _logger(run_id: UUID, level: str) -> logging.LoggerAdapter:
    logger = logging.getLogger(f"generative_agents.ga_runtime.{run_id}")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s run=%(run_id)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logging.LoggerAdapter(logger, {"run_id": str(run_id)})


def _read_checkpoint(paths: RunPaths, committed_step: int):
    if committed_step == 0:
        return None, None, None
    from generative_agents.ga_protocol.recovery import boundary_snapshot
    target = boundary_snapshot(paths.root, str(paths.run_id), committed_step)
    selected_paths = RecoveryPaths(paths.root, paths.run_id) if target.parent.name == 'recovery' else paths
    reader = CheckpointBundleWriter(
        selected_paths,
        lambda _result: CheckpointSnapshot(state={}, conversation={}),
    )
    checkpoint = reader.validate(target)
    state = read_json(checkpoint.path / "state.json")
    conversation = read_json(checkpoint.path / "conversation.json")
    bundle = read_json(checkpoint.path / "bundle.json")
    if not isinstance(state, dict) or not isinstance(conversation, dict) or not isinstance(bundle, dict):
        raise PackageError("checkpoint state is invalid")
    return checkpoint.path, state, conversation


def _quarantine_uncommitted(paths: RunPaths, boundary: int) -> None:
    """Preserve failed commit outputs outside the new Attempt's writable paths."""
    batch = paths.orphaned / f'recovery-{uuid4()}'
    for directory, pattern in ((paths.frames, 'step-*.json.gz'), (paths.checkpoints, 'step-*'), (paths.root / 'recovery', 'step-*')):
        for candidate in sorted(directory.glob(pattern)):
            step = candidate.name.removeprefix('step-').removesuffix('.json.gz')
            if not step.isdigit() or int(step) <= boundary:
                continue
            if candidate.is_symlink() or candidate.resolve().parent != directory.resolve():
                raise PackageError('unsafe uncommitted Run output path')
            destination = batch / directory.name / candidate.name
            destination.resolve().relative_to(paths.orphaned.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            candidate.replace(destination)
    if boundary > 0:
        for selected in (paths, RecoveryPaths(paths.root, paths.run_id)):
            if (selected.checkpoints / f'step-{boundary:06d}').is_dir():
                reader = CheckpointBundleWriter(selected, lambda _result: CheckpointSnapshot(state={}, conversation={}))
                reader.select_for_recovery(boundary, orphan_root=batch / selected.checkpoints.name)


def _restore_storage(checkpoint: Path | None, attempt_root: Path) -> tuple[Path, Path]:
    agent_storage = attempt_root / "storage"
    memory_storage = attempt_root / "runtime-storage" / "skill-memory"
    if checkpoint is not None:
        source = checkpoint / "storage"
        if source.is_dir():
            shutil.copytree(source, agent_storage)
        source = checkpoint / "runtime-storage" / "skill-memory"
        if source.is_dir():
            shutil.copytree(source, memory_storage)
    agent_storage.mkdir(parents=True, exist_ok=True)
    memory_storage.mkdir(parents=True, exist_ok=True)
    return agent_storage, memory_storage


def _attempt_files(run_root: Path) -> list[Path]:
    attempts = run_root / "attempts"
    return sorted(attempts.glob("*/attempt.json")) if attempts.is_dir() else []


def execute_run_directory(run_root: str | Path) -> RunStatus:
    """Start or resume a Run at its latest complete checkpoint."""

    run_root = Path(run_root).resolve()
    manifest = validate_run_directory(run_root)
    paths = RunPaths(root=run_root, run_id=UUID(manifest.run_id))
    paths.ensure()
    with FileLock(str(run_root / "worker.lock"), timeout=0):
        status_path = run_root / "status.json"
        status = RunStatus.model_validate(read_json(status_path))
        if status.run_id != manifest.run_id:
            raise PackageError("Run status belongs to another Run")
        if status.status == RunState.COMPLETED:
            raise PackageError("a completed Run must be rerun, not resumed")
        if status.committed_step >= manifest.requested_steps:
            status.status = RunState.COMPLETED
            atomic_write_json(status_path, status.model_dump(mode="json"))
            return status

        experiment = load_experiment_directory(run_root / manifest.experiment.path)
        checkpoint, checkpoint_state, checkpoint_conversation = _read_checkpoint(
            paths, status.committed_step
        )
        _quarantine_uncommitted(paths, status.committed_step)
        attempt_id = uuid4()
        attempt_no = len(_attempt_files(run_root)) + 1
        attempt_root = run_root / "attempts" / str(attempt_id)
        attempt_root.mkdir(parents=True, exist_ok=False)
        attempt = AttemptRecord(
            attempt_id=str(attempt_id),
            run_id=manifest.run_id,
            ordinal=attempt_no,
            resumed_from_step=status.committed_step,
            started_at=datetime.now(UTC),
        )
        attempt_path = attempt_root / "attempt.json"
        atomic_write_json(attempt_path, attempt.model_dump(mode="json"))
        status.status = RunState.RUNNING
        status.active_attempt_id = str(attempt_id)
        status.reason = None
        status.updated_at = attempt.started_at
        atomic_write_json(status_path, status.model_dump(mode="json"))

        control = FileRunControl(run_root)
        control.reset()
        logger = _logger(paths.run_id, experiment.definition.simulation.log_level)
        recorder = ModelTraceWriter(
            paths,
            run_id=paths.run_id,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            capture_payloads=experiment.definition.results.capture_model_payloads,
        )
        try:
            status = _execute_attempt(
                paths=paths,
                manifest=manifest,
                experiment=experiment,
                attempt_id=attempt_id,
                attempt_no=attempt_no,
                attempt_root=attempt_root,
                checkpoint=checkpoint,
                checkpoint_state=checkpoint_state,
                checkpoint_conversation=checkpoint_conversation,
                control=control,
                recorder=recorder,
                logger=logger,
                status=status,
                status_path=status_path,
            )
            attempt.status = {
                RunState.COMPLETED: AttemptState.COMPLETED,
                RunState.PAUSED: AttemptState.PAUSED,
                RunState.CANCELLED: AttemptState.CANCELLED,
            }.get(status.status, AttemptState.PAUSED)
            attempt.finished_at = datetime.now(UTC)
            atomic_write_json(attempt_path, attempt.model_dump(mode="json"))
            return status
        except Exception as exc:
            # Committer updates the status file each Step; the outer object is stale.
            status = RunStatus.model_validate(read_json(status_path))
            attempt.status = AttemptState.FAILED
            attempt.failure = str(exc) or exc.__class__.__name__
            attempt.finished_at = datetime.now(UTC)
            status.status = RunState.FAILED
            status.reason = attempt.failure
            status.active_attempt_id = str(attempt_id)
            status.updated_at = datetime.now(UTC)
            atomic_write_json(attempt_path, attempt.model_dump(mode="json"))
            atomic_write_json(status_path, status.model_dump(mode="json"))
            raise


def _execute_attempt(
    *,
    paths: RunPaths,
    manifest,
    experiment: LoadedExperiment,
    attempt_id: UUID,
    attempt_no: int,
    attempt_root: Path,
    checkpoint: Path | None,
    checkpoint_state: dict | None,
    checkpoint_conversation: dict | None,
    control: FileRunControl,
    recorder: ModelTraceWriter,
    logger,
    status: RunStatus,
    status_path: Path,
) -> RunStatus:
    definition = experiment.definition
    agent_storage, memory_storage = _restore_storage(checkpoint, attempt_root)
    current_time = (
        datetime_from_checkpoint(checkpoint)
        + timedelta(minutes=definition.simulation.stride_minutes)
        if checkpoint is not None
        else definition.simulation.start_time
    )
    clock = SimulationClock(current_time)
    memory_stream = FileMemoryStream(
        memory_storage,
        run_id=paths.run_id,
        attempt_id=attempt_id,
        clock=lambda: clock.get_date(),
        logger=logger,
    )
    skill_registry = SnapshotSkillRegistry(
        experiment.skill_snapshot,
        root=attempt_root / "runtime-storage" / "skill-bundle",
    )
    skills = SnapshotSkillInstructionRepository(
        experiment.skill_snapshot,
        brain=experiment.skill_registry.brain_skill,
    )
    chat_config = definition.models.chat.model_dump(mode="json")
    chat_config.pop("secret_ref", None)
    chat_config["api_key"] = experiment.chat_api_key
    model_registry = RuntimeModelRegistry(chat_config, recorder, control=control, logger=logger)
    chat_model = model_registry.get("chat")
    brain_runtime = BrainRuntime(
        skill_registry,
        brain_skill=experiment.skill_registry.brain_skill,
        model_config=chat_config,
        memory_stream=memory_stream,
        model_client=chat_model,
        recorder=recorder,
        control=control,
        logger=logger,
    )
    object_runtime = ObjectSkillRuntime(
        skill_registry,
        model_config=chat_config,
        memory_stream=memory_stream,
        model_client=chat_model,
        recorder=recorder,
        control=control,
        logger=logger,
    )
    context = SimulationContext(
        run_id=paths.run_id,
        experiment_id=UUID(experiment.manifest.experiment.experiment_id),
        attempt_id=attempt_id,
        definition_hash=experiment.root_sha256,
        algorithm=get_algorithm_profile(definition.engine.algorithm_version),
        clock=clock,
        random=random.Random(definition.simulation.random_seed),
        paths=paths,
        skills=skills,
        models=model_registry,
        control=control,
        logger=logger,
        object_skill_runtime=object_runtime,
        memory_stream=memory_stream,
        brain_runtime=brain_runtime,
        metadata={"experiment_root_sha256": experiment.root_sha256, "attempt_no": attempt_no},
    )
    runner = build_runner(
        context,
        definition,
        embedding_api_key=experiment.embedding_api_key,
        checkpoint_state=checkpoint_state,
        checkpoint_conversation=checkpoint_conversation,
        storage_root=agent_storage,
    )
    runner.completed_steps = status.committed_step
    runner.committer = _StatusCommitter(runner.committer, status_path, manifest.requested_steps)
    remaining = manifest.requested_steps - runner.completed_steps
    completed = runner.run(remaining, stride_minutes=definition.simulation.stride_minutes)
    status = RunStatus.model_validate(read_json(status_path))
    status.committed_step = completed
    status.updated_at = datetime.now(UTC)
    if completed >= manifest.requested_steps:
        status.status = RunState.FINALIZING
        status.reason = "all Steps committed; building final quality artifacts"
    elif control.cancel_requested:
        status.status = RunState.CANCELLED
        status.reason = "cancel requested at a complete Step boundary"
    else:
        status.status = RunState.PAUSED
        status.reason = "pause requested at a complete Step boundary"
    atomic_write_json(status_path, status.model_dump(mode="json"))
    try:
        # Deterministic diagnostics are part of Runtime. Optional model-based
        # evaluators belong to an explicit experiment evaluation configuration
        # and must not silently add another model call after execution.
        quality = project_run_quality(
            paths.root,
            run_id=manifest.run_id,
            committed_step=status.committed_step,
            brain_skill=definition.engine.brain_skill,
            evaluated_at=status.updated_at.isoformat(),
        )
        atomic_write_json(run_root_artifact(paths, "quality-report.json"), quality)
    except Exception as exc:
        # Execution outcome and quality evaluation are independent dimensions.
        atomic_write_json(
            run_root_artifact(paths, "quality-report.json"),
            {
                "quality_status": "UNAVAILABLE",
                "issues": [],
                "evaluation_error": str(exc) or exc.__class__.__name__,
            },
        )
    if status.status == RunState.FINALIZING:
        status.status = RunState.COMPLETED
        status.reason = None
        status.updated_at = datetime.now(UTC)
        atomic_write_json(status_path, status.model_dump(mode="json"))
    return status


def datetime_from_checkpoint(checkpoint: Path):
    bundle = read_json(checkpoint / "bundle.json")
    if not isinstance(bundle, dict) or not isinstance(bundle.get("virtual_time"), str):
        raise PackageError("checkpoint virtual_time is missing")
    return datetime.fromisoformat(bundle["virtual_time"])


def run_root_artifact(paths: RunPaths, name: str) -> Path:
    return paths.artifacts / name
