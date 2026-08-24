"""Local process supervisor for concurrently isolated experiment Runs."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import UUID

import psutil
from filelock import FileLock

from generative_agents.config import ExperimentDefinition
from generative_agents.config.schema import REQUIRED_ATOMIC_SKILLS
from generative_agents.skills import SkillRegistry
from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    Asset,
    ExperimentRevision,
    Run,
    RunEvent,
)

from .context import RunPaths
from .manifest import RunManifestStore, build_manifest_document
from .scheduler import ClaimedRun, LocalRunSchedulerRepository


@dataclass(slots=True)
class SupervisedChild:
    claimed: ClaimedRun
    process: subprocess.Popen
    log_handle: object


class LocalProcessSupervisor:
    """Own worker processes while durable ownership remains in SQLite.

    The in-memory child map is deliberately only an optimization. Reconciliation
    uses PID plus process creation time, so a Web service restart does not confuse
    an unrelated process that later reuses the same PID with an experiment worker.
    """

    def __init__(
        self,
        database: Database,
        *,
        var_dir: str | Path,
        max_concurrent_runs: int = 2,
        poll_interval_seconds: float = 0.5,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        code_build_id: str | None = None,
    ):
        self._database = database
        self._var_dir = Path(var_dir).resolve()
        self._var_dir.mkdir(parents=True, exist_ok=True)
        self._repository = LocalRunSchedulerRepository(
            database, max_concurrent_runs=max_concurrent_runs
        )
        self.max_concurrent_runs = max_concurrent_runs
        self.poll_interval_seconds = poll_interval_seconds
        self._process_factory = process_factory
        self._code_build_id = code_build_id or self._default_build_id()
        self._children: dict[str, SupervisedChild] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._scheduler_lock = FileLock(
            str(self._var_dir / "scheduler.lock"), timeout=0
        )

    @property
    def repository(self) -> LocalRunSchedulerRepository:
        return self._repository

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._scheduler_lock.acquire()
        self._stop.clear()
        self._repository.reconcile()
        self._thread = threading.Thread(
            target=self._run_loop, name="experiment-run-supervisor", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop scheduling; active workers continue and remain reconcilable."""

        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        for child in self._children.values():
            try:
                child.log_handle.close()
            except OSError:
                pass
        self._children.clear()
        if self._scheduler_lock.is_locked:
            self._scheduler_lock.release()

    def tick(self) -> None:
        self._reap_children()
        self._enforce_force_cancellations()
        self._repository.reconcile()
        while not self._stop.is_set():
            claimed = self._repository.claim_next()
            if claimed is None:
                break
            try:
                self._materialize_manifest(claimed)
                self._spawn(claimed)
            except Exception as exc:
                self._repository.mark_spawn_failed(
                    claimed,
                    error_code="WORKER_SPAWN_FAILED",
                    error_message=f"{type(exc).__name__}: {exc}",
                )

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                # One scheduler iteration must not permanently stop future Runs.
                pass
            self._stop.wait(self.poll_interval_seconds)

    def _materialize_manifest(self, claimed: ClaimedRun) -> None:
        with self._database.session_factory() as session:
            revision = session.get(ExperimentRevision, claimed.revision_id)
            if revision is None or revision.state != "PUBLISHED":
                raise RuntimeError("claimed Run does not reference a published Revision")
            definition = ExperimentDefinition.model_validate(revision.definition_json)
            assets: list[dict] = []
            for reference in definition.world.assets:
                digest = reference.asset_hash.removeprefix("sha256:")
                asset = session.query(Asset).filter(Asset.sha256 == digest).one_or_none()
                if asset is None:
                    raise RuntimeError(
                        f"published Revision references missing asset {reference.asset_hash}"
                    )
                asset_path = (self._var_dir / "assets" / asset.relative_path).resolve()
                if not asset_path.is_relative_to(self._var_dir) or not asset_path.is_file():
                    raise RuntimeError(f"asset content is missing: {reference.logical_path}")
                assets.append(
                    {
                        "logical_path": reference.logical_path,
                        "asset_hash": reference.asset_hash,
                        "media_type": asset.media_type,
                        "size_bytes": reference.size,
                        "relative_path": f"assets/{asset.relative_path}",
                    }
                )
            paths = RunPaths.under(self._var_dir, UUID(claimed.run_id))
            store = RunManifestStore(paths)
            if store.exists():
                store.reuse_for_revision(
                    experiment_id=UUID(claimed.experiment_id),
                    revision_id=UUID(claimed.revision_id),
                    definition=definition,
                    expected_definition_hash=revision.definition_hash,
                    assets=assets,
                )
                return
            skill_roots = {
                definition.engine.brain_skill,
                *(key.replace("_", "-") for key in REQUIRED_ATOMIC_SKILLS),
                *self._bound_skill_names(
                    definition.world.model_dump(mode="json", exclude_none=False)
                ),
            }
            skill_bundle = SkillRegistry().snapshot(skill_roots)
            document = build_manifest_document(
                run_id=UUID(claimed.run_id),
                experiment_id=UUID(claimed.experiment_id),
                revision_id=UUID(claimed.revision_id),
                definition=definition,
                expected_definition_hash=revision.definition_hash,
                code_build_id=self._code_build_id,
                assets=assets,
                materialized_at=datetime.now(timezone.utc),
                skill_bundle=skill_bundle,
            )
            store.materialize(document)

    @staticmethod
    def _bound_skill_names(value) -> set[str]:
        names: set[str] = set()
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "skill_name" and isinstance(item, str) and item.strip():
                    names.add(item.strip().casefold().replace("_", "-"))
                else:
                    names.update(LocalProcessSupervisor._bound_skill_names(item))
        elif isinstance(value, list):
            for item in value:
                names.update(LocalProcessSupervisor._bound_skill_names(item))
        return names

    def _spawn(self, claimed: ClaimedRun) -> None:
        log_path = (self._var_dir / claimed.log_path).resolve()
        if not log_path.is_relative_to(self._var_dir):
            raise RuntimeError("worker log path escaped var_dir")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("ab", buffering=0)
        command = [
            sys.executable,
            "-m",
            "generative_agents.runtime.worker",
            "--database-url",
            str(self._database.engine.url),
            "--var-dir",
            str(self._var_dir),
            "--run-id",
            claimed.run_id,
            "--attempt-id",
            claimed.attempt_id,
            "--start-step",
            str(claimed.start_step),
        ]
        child_env = os.environ.copy()
        # stdout/stderr are redirected as raw bytes.  Windows otherwise lets
        # the child inherit the active ANSI console code page (often cp936),
        # which violates the appendable UTF-8 log contract at the source.
        child_env["PYTHONUTF8"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        try:
            process = self._process_factory(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                cwd=str(Path(__file__).resolve().parents[2]),
                shell=False,
                env=child_env,
            )
            create_time = psutil.Process(process.pid).create_time()
            if not self._repository.register_worker(
                claimed, pid=process.pid, pid_create_time=create_time
            ):
                process.kill()
                raise RuntimeError("claimed slot ownership changed before worker registration")
        except Exception:
            log_handle.close()
            raise
        self._children[claimed.attempt_id] = SupervisedChild(
            claimed=claimed, process=process, log_handle=log_handle
        )

    def _reap_children(self) -> None:
        for attempt_id, child in list(self._children.items()):
            exit_code = child.process.poll()
            if exit_code is None:
                continue
            child.log_handle.close()
            self._repository.finish_worker(
                child.claimed.run_id,
                child.claimed.attempt_id,
                exit_code=exit_code,
            )
            self._children.pop(attempt_id, None)

    def _enforce_force_cancellations(self) -> None:
        with self._database.session_factory() as session:
            runs = list(session.query(Run).filter(Run.status == "CANCEL_REQUESTED"))
            targets: list[tuple[int, float]] = []
            for run in runs:
                latest = (
                    session.query(RunEvent)
                    .filter(RunEvent.run_id == run.id, RunEvent.event_type == "state")
                    .order_by(RunEvent.id.desc())
                    .first()
                )
                if (
                    latest
                    and latest.payload_json.get("force")
                    and run.pid is not None
                    and run.pid_create_time is not None
                ):
                    targets.append((run.pid, run.pid_create_time))
        for pid, create_time in targets:
            try:
                process = psutil.Process(pid)
                if abs(process.create_time() - create_time) < 0.01:
                    process.kill()
            except (psutil.Error, OSError):
                continue

    @staticmethod
    def _default_build_id() -> str:
        configured = os.environ.get("GA_CODE_BUILD_ID", "").strip()
        if configured:
            return configured
        digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
        return f"workspace-{digest}"
