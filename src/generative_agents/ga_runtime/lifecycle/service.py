"""Portable Run lifecycle: create, start, resume, rerun, control, and seal."""

from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from filelock import FileLock

from generative_agents.ga_protocol.schemas.manifests import EmbeddedExperiment
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.schemas.manifests import RunLineage
from generative_agents.ga_protocol.schemas.manifests import RunManifest
from generative_agents.ga_protocol.schemas.manifests import RunState
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.packages.io import copy_package_tree
from generative_agents.ga_protocol.packages.io import extract_archive
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import seal_directory
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_protocol.packages.validation import validate_run_directory
from generative_agents.ga_protocol.packages.io import verify_integrity
from generative_agents.ga_protocol.packages.io import write_integrity_manifest
from generative_agents.ga_protocol.packages.constants import INTEGRITY_MANIFEST

from generative_agents.ga_runtime.lifecycle.control import FileRunControl
class RunService:
    """No-database application service for one local Runtime installation."""

    def create(
        self,
        experiment_package: str | Path,
        destination: str | Path,
        *,
        requested_steps: int | None = None,
        origin_run_id: str | None = None,
    ) -> Path:
        destination = Path(destination).resolve()
        if destination.exists():
            raise PackageError(f"Run destination already exists: {destination}")
        with open_package(Path(experiment_package)) as experiment_root:
            experiment_manifest = validate_experiment_directory(experiment_root)
            experiment_integrity = verify_integrity(experiment_root)
            simulation = read_json(experiment_root / experiment_manifest.entrypoints.simulation)
            if not isinstance(simulation, dict):
                raise PackageError("experiment simulation config is invalid")
            max_steps = int(simulation.get("max_steps") or 0)
            steps = int(requested_steps or max_steps)
            if steps < 1 or steps > max_steps:
                raise PackageError(f"requested_steps must be between 1 and {max_steps}")
            run_id = uuid4()
            destination.mkdir(parents=True)
            try:
                copy_package_tree(experiment_root, destination / "experiment")
                manifest = RunManifest(
                    run_id=str(run_id),
                    created_at=datetime.now(UTC),
                    requested_steps=steps,
                    experiment=EmbeddedExperiment(
                        experiment_id=experiment_manifest.experiment.experiment_id,
                        path="experiment",
                        root_sha256=str(experiment_integrity["root_sha256"]),
                    ),
                    lineage=RunLineage(
                        mode="rerun" if origin_run_id else "start",
                        origin_run_id=origin_run_id,
                    ),
                )
                atomic_write_json(destination / "run.json", manifest.model_dump(mode="json"))
                status = RunStatus(
                    run_id=str(run_id),
                    status=RunState.CREATED,
                    total_steps=steps,
                    updated_at=manifest.created_at,
                )
                atomic_write_json(destination / "status.json", status.model_dump(mode="json"))
                FileRunControl(destination)
                (destination / "attempts").mkdir()
                validate_run_directory(destination)
                return destination
            except Exception:
                shutil.rmtree(destination, ignore_errors=True)
                raise

    def start(self, experiment_package: str | Path, destination: str | Path, *, requested_steps: int | None = None) -> RunStatus:
        from generative_agents.ga_runtime.lifecycle.executor import execute_run_directory

        run_root = self.create(experiment_package, destination, requested_steps=requested_steps)
        return execute_run_directory(run_root)

    def resume(self, run_package: str | Path, *, extracted_destination: str | Path | None = None) -> RunStatus:
        from generative_agents.ga_runtime.lifecycle.executor import execute_run_directory

        run_root = self.materialize(run_package, extracted_destination=extracted_destination)
        return execute_run_directory(run_root)

    def rerun(
        self,
        run_package: str | Path,
        destination: str | Path,
        *,
        requested_steps: int | None = None,
    ) -> RunStatus:
        from generative_agents.ga_runtime.lifecycle.executor import execute_run_directory

        created = self.create_rerun(
            run_package,
            destination,
            requested_steps=requested_steps,
        )
        return execute_run_directory(created)

    def create_rerun(
        self,
        run_package: str | Path,
        destination: str | Path,
        *,
        requested_steps: int | None = None,
    ) -> Path:
        """Create a new Run from an old Run's embedded experiment without executing it."""

        with open_package(Path(run_package)) as source_root:
            source_manifest = validate_run_directory(
                source_root,
                sealed=Path(run_package).is_file(),
            )
            return self.create(
                source_root / source_manifest.experiment.path,
                destination,
                requested_steps=requested_steps or source_manifest.requested_steps,
                origin_run_id=source_manifest.run_id,
            )

    def materialize(
        self,
        run_package: str | Path,
        *,
        extracted_destination: str | Path | None = None,
    ) -> Path:
        source = Path(run_package).resolve()
        if source.is_dir():
            validate_run_directory(source)
            return source
        if extracted_destination is None:
            raise PackageError("resuming a .garun requires an extracted_destination")
        destination = Path(extracted_destination).resolve()
        extract_archive(source, destination)
        try:
            validate_run_directory(destination, sealed=True)
            (destination / INTEGRITY_MANIFEST).unlink()
            integrity_dir = destination / "integrity"
            if integrity_dir.is_dir() and not any(integrity_dir.iterdir()):
                integrity_dir.rmdir()
            return destination
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def request_pause(self, run_directory: str | Path) -> None:
        FileRunControl(run_directory).request_pause()

    def request_cancel(self, run_directory: str | Path) -> None:
        FileRunControl(run_directory).request_cancel()

    def seal(self, run_directory: str | Path, archive: str | Path) -> Path:
        run_root = Path(run_directory).resolve()
        archive = Path(archive).resolve()
        if archive.suffix.casefold() != ".garun":
            raise PackageError("Run archives must use the .garun suffix")
        manifest = validate_run_directory(run_root)
        status = RunStatus.model_validate(read_json(run_root / "status.json"))
        if status.status in {RunState.QUEUED, RunState.RUNNING, RunState.FINALIZING}:
            raise PackageError("an active Run cannot be sealed")
        with FileLock(str(run_root / "worker.lock"), timeout=0):
            temporary = Path(tempfile.mkdtemp(prefix="ga-run-seal-"))
            staging = temporary / "run"
            staging.mkdir()
            try:
                excluded = {"worker.lock", "checkpoint.lock", "artifact.lock", INTEGRITY_MANIFEST}
                for path in sorted(run_root.rglob("*")):
                    if path.is_symlink():
                        raise PackageError(f"Run contains a symbolic link: {path}")
                    if not path.is_file():
                        continue
                    relative = path.relative_to(run_root).as_posix()
                    if relative in excluded or relative.endswith(".lock"):
                        continue
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
                write_integrity_manifest(staging)
                validate_run_directory(staging, sealed=True)
                return seal_directory(staging, archive)
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
