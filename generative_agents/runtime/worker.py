"""Entry point for one isolated experiment worker process."""

from __future__ import annotations

import argparse
import json
import logging
import random
import shutil
import threading
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from filelock import FileLock
from generative_agents.config import get_algorithm_profile
from generative_agents.persistence import create_database
from generative_agents.persistence.models import Run
from generative_agents.security import MasterKeyStore, SecretCipher

from .checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from .commit import FileStepCommitter
from .context import (
    MappingPromptRepository,
    RunControl,
    RunPaths,
    SimulationClock,
    SimulationContext,
)
from .frame_store import FrameStore
from .manifest import RunManifestStore
from .model_trace import ModelTraceWriter
from .scheduler import LocalRunSchedulerRepository
from .sqlite_result_projector import SqliteResultProjector
from .trace_projector import ModelTraceProjector

if TYPE_CHECKING:
    from generative_agents.start import SimulationRunner


class ModelFactoryRegistry:
    """Create one traced chat model per Agent without global SDK settings."""

    def __init__(self, config: dict, recorder: ModelTraceWriter, *, control, logger):
        self._config = config
        self._recorder = recorder
        self._control = control
        self._logger = logger

    def get(self, purpose: str):
        if purpose != "chat":
            raise KeyError(f"unsupported model purpose: {purpose}")
        # This import pulls in the model SDK stack and is intentionally delayed
        # until the worker heartbeat thread is already running. On CPU-only
        # Windows hosts importing LlamaIndex/OpenAI can take tens of seconds.
        from generative_agents.modules.model.llm_model import create_llm_model

        return create_llm_model(
            dict(self._config),
            recorder=self._recorder,
            control=self._control,
            logger=self._logger,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="run one experiment attempt")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--var-dir", required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--attempt-id", type=UUID, required=True)
    parser.add_argument("--start-step", type=int, required=True)
    return parser


def _secret_value(definition, purpose: str, cipher: SecretCipher, database) -> str:
    model = getattr(definition.models, purpose)
    secret_id = model.secret_ref
    if not secret_id:
        return ""
    from generative_agents.persistence.models import Secret

    with database.session_factory() as session:
        secret = session.get(Secret, secret_id)
        if secret is None:
            raise RuntimeError(f"{purpose} model secret_ref does not exist")
        encrypted = secret.encrypted_value
    return cipher.decrypt(encrypted)


def _logger(run_id: UUID, level: str) -> logging.LoggerAdapter:
    logger = logging.getLogger(f"generative_agents.worker.{run_id}")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s run=%(run_id)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logging.LoggerAdapter(logger, {"run_id": str(run_id)})


def _install_sqlite_committer(
    runner: SimulationRunner,
    database,
    var_dir: Path,
    *,
    checkpoint_retention: int,
    trace_writer: ModelTraceWriter,
) -> None:
    checkpoint = CheckpointBundleWriter(
        runner.context.paths,
        lambda _result: CheckpointSnapshot(
            state=runner.game.snapshot_state(),
            conversation=runner.game.conversation,
            storage_exporters=runner.game.storage_exporters(),
        ),
        retention=checkpoint_retention,
    )
    result_projection = SqliteResultProjector(database, var_dir=var_dir)
    trace_projection = ModelTraceProjector(database, var_dir=var_dir)
    relative_trace_path = trace_writer.path.resolve().relative_to(var_dir).as_posix()

    class StepAndTraceProjection:
        """Project the durable frame first, then every complete trace record."""

        def commit_step(self, result, *, frame, checkpoint_path):
            version = result_projection.commit_step(
                result,
                frame=frame,
                checkpoint_path=checkpoint_path,
            )
            trace_projection.project(
                run_id=str(result.run_id),
                attempt_id=str(result.attempt_id),
                relative_path=relative_trace_path,
            )
            return version

    runner.committer = FileStepCommitter(
        FrameStore(runner.context.paths),
        StepAndTraceProjection(),
        checkpoint,
    )


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    var_dir = Path(args.var_dir).resolve()
    database = create_database(args.database_url)
    repository = LocalRunSchedulerRepository(database)
    paths = RunPaths.under(var_dir, args.run_id)
    paths.ensure()
    worker_lock = FileLock(str(paths.worker_lock), timeout=0)
    worker_lock.acquire()
    control = RunControl()
    stop_monitor = threading.Event()
    monitor = threading.Thread(
        target=_control_monitor,
        args=(repository, str(args.run_id), str(args.attempt_id), control, stop_monitor),
        name="run-control-monitor",
        daemon=True,
    )
    exit_code = 1
    logger = _logger(args.run_id, "INFO")
    recorder: ModelTraceWriter | None = None
    try:
        # Start renewing durable ownership before importing the legacy engine.
        # Those imports can exceed the normal heartbeat deadline on Windows.
        if repository.heartbeat(str(args.run_id), str(args.attempt_id)) is None:
            raise RuntimeError("worker does not own the current Run attempt")
        monitor.start()

        manifest = RunManifestStore(paths).load_verified()
        definition = manifest.definition
        logger = _logger(args.run_id, definition.simulation.log_level)
        cipher = SecretCipher(MasterKeyStore(var_dir).load_or_create())
        recorder = ModelTraceWriter(
            paths,
            run_id=args.run_id,
            attempt_id=args.attempt_id,
            attempt_no=_attempt_no(database, str(args.attempt_id)),
            capture_payloads=definition.results.capture_model_payloads,
        )
        chat_config = definition.models.chat.model_dump(mode="json", exclude_none=False)
        chat_config["api_key"] = _secret_value(definition, "chat", cipher, database)
        embedding_key = _secret_value(definition, "embedding", cipher, database)
        prompts = MappingPromptRepository(
            {key: prompt.content for key, prompt in definition.prompts.items()}
        )
        checkpoint_state, checkpoint_conversation, attempt_storage = _prepare_attempt_state(
            database,
            paths,
            run_id=str(args.run_id),
            attempt_id=str(args.attempt_id),
            start_step=args.start_step,
            stride_minutes=definition.simulation.stride_minutes,
        )
        start_time = (
            datetime.fromisoformat(checkpoint_state["virtual_time"])
            + timedelta(minutes=definition.simulation.stride_minutes)
            if checkpoint_state is not None
            else definition.simulation.start_time
        )
        context = SimulationContext(
            run_id=args.run_id,
            experiment_id=UUID(manifest.document["experiment_id"]),
            revision_id=UUID(manifest.document["revision_id"]),
            attempt_id=args.attempt_id,
            definition_hash=manifest.document["definition_hash"],
            algorithm=get_algorithm_profile(definition.engine.algorithm_version),
            clock=SimulationClock(start_time),
            random=random.Random(definition.simulation.random_seed),
            paths=paths,
            prompts=prompts,
            models=ModelFactoryRegistry(
                chat_config,
                recorder,
                control=control,
                logger=logger,
            ),
            control=control,
            logger=logger,
            metadata={"model_trace": recorder, "manifest_hash": manifest.manifest_hash},
        )
        # Keep the expensive legacy engine import behind the active heartbeat.
        from generative_agents.start import build_runner

        runner = build_runner(
            context,
            definition,
            embedding_api_key=embedding_key,
            checkpoint_state=checkpoint_state,
            checkpoint_conversation=checkpoint_conversation,
            storage_root=attempt_storage,
        )
        runner.completed_steps = args.start_step - 1
        _install_sqlite_committer(
            runner,
            database,
            var_dir,
            checkpoint_retention=definition.simulation.checkpoint_retention,
            trace_writer=recorder,
        )
        remaining = definition.simulation.max_steps - runner.completed_steps
        if remaining > 0:
            runner.run(remaining, stride_minutes=definition.simulation.stride_minutes)
        exit_code = 0
    except Exception:
        logger.exception("worker attempt failed")
    finally:
        stop_monitor.set()
        if monitor.is_alive():
            monitor.join(timeout=2)
        if recorder is not None and recorder.path.is_file():
            try:
                ModelTraceProjector(database, var_dir=var_dir).project(
                    run_id=str(args.run_id),
                    attempt_id=str(args.attempt_id),
                    relative_path=recorder.path.resolve().relative_to(var_dir).as_posix(),
                )
            except Exception:
                exit_code = 1
                logger.exception("final model trace projection failed")
        repository.finish_worker(
            str(args.run_id), str(args.attempt_id), exit_code=exit_code
        )
        worker_lock.release()
        database.close()
    return exit_code


def _attempt_no(database, attempt_id: str) -> int:
    from generative_agents.persistence.models import RunAttempt

    with database.session_factory() as session:
        attempt = session.get(RunAttempt, attempt_id)
        if attempt is None:
            raise RuntimeError("attempt was not registered in the scheduler database")
        return attempt.attempt_no


def _prepare_attempt_state(
    database,
    paths: RunPaths,
    *,
    run_id: str,
    attempt_id: str,
    start_step: int,
    stride_minutes: int,
) -> tuple[dict | None, dict | None, Path]:
    """Copy a verified checkpoint into a fresh, attempt-owned writable store."""

    if start_step < 1:
        raise ValueError("start_step must be positive")
    with database.session_factory() as session:
        run = session.get(Run, run_id)
        if run is None or run.current_attempt_id != attempt_id:
            raise RuntimeError("worker does not own the current Run attempt")
        if start_step != run.recoverable_step + 1:
            raise RuntimeError("attempt start_step does not match recoverable_step")

    attempt_root = paths.root / "attempts" / attempt_id
    storage_root = attempt_root / "storage"
    if attempt_root.exists():
        raise RuntimeError("attempt work directory already exists")
    if start_step == 1:
        storage_root.mkdir(parents=True, exist_ok=False)
        return None, None, storage_root

    reader = CheckpointBundleWriter(
        paths,
        lambda _: CheckpointSnapshot(state={}, conversation={}),
    )
    expected_step = start_step - 1
    checkpoint = reader.select_for_recovery(
        expected_step,
        orphan_root=paths.orphaned / f"attempt-{attempt_id}" / "checkpoints",
    )
    bundle = json.loads((checkpoint.path / "bundle.json").read_text(encoding="utf-8"))
    storage_root.mkdir(parents=True, exist_ok=False)
    state = json.loads((checkpoint.path / "state.json").read_text(encoding="utf-8"))
    conversation = json.loads(
        (checkpoint.path / "conversation.json").read_text(encoding="utf-8")
    )
    virtual_time = datetime.fromisoformat(state.get("virtual_time", ""))
    if virtual_time.tzinfo is None:
        raise RuntimeError("checkpoint virtual_time is not timezone-aware")
    source_storage = checkpoint.path / "storage"
    if source_storage.exists():
        for agent_dir in source_storage.iterdir():
            if not agent_dir.is_dir() or agent_dir.is_symlink():
                raise RuntimeError("checkpoint storage contains an unsafe member")
            shutil.copytree(agent_dir, storage_root / agent_dir.name)
    return state, conversation, storage_root


def _control_monitor(
    repository: LocalRunSchedulerRepository,
    run_id: str,
    attempt_id: str,
    control: RunControl,
    stop: threading.Event,
) -> None:
    while not stop.wait(0.5):
        status = repository.heartbeat(run_id, attempt_id)
        if status is None:
            control.request_cancel()
            return
        if status == "PAUSE_REQUESTED":
            control.request_pause()
        elif status == "CANCEL_REQUESTED":
            control.request_cancel()


if __name__ == "__main__":
    raise SystemExit(main())
