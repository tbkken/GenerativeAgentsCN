"""Read committed StepResult frames without executing Agents or Skills."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import mimetypes
import zipfile
from collections import OrderedDict
from threading import RLock
from collections.abc import Iterator
from pathlib import Path

from generative_agents.ga_protocol import (
    PackageError,
    RunManifest,
    RunStatus,
    open_package,
    read_json,
    validate_run_integrity,
    validate_package_path,
)
from generative_agents.ga_protocol.quality import project_run_quality


_validated_runs = OrderedDict()
_validation_lock = RLock()
_quality_reports = OrderedDict()
_quality_lock = RLock()


def _validated_directory(root: Path) -> RunManifest:
    """Reuse validation only while every immutable package file is unchanged.

    Mutable status, projections and frames are always read afresh. The cache is
    bounded and disposable; neither it nor the Studio catalog is a Run fact.
    """
    manifest = RunManifest.model_validate(read_json(root / "run.json"))
    experiment_root = root / manifest.experiment.path
    try:
        experiment_root.resolve().relative_to(root)
    except ValueError as exc:
        raise PackageError("embedded experiment path escapes Run package") from exc
    if experiment_root.is_symlink():
        raise PackageError("embedded experiment cannot be a symbolic link")
    paths = [root / "run.json", *sorted(experiment_root.rglob("*"))]
    signature = tuple(
        (str(path), stat.st_mode, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
        for path in paths
        for stat in [path.lstat()]
    )
    with _validation_lock:
        cached = _validated_runs.get(root)
        if cached is not None and cached[0] == signature:
            _validated_runs.move_to_end(root)
            return cached[1]
        verified = validate_run_integrity(root)
        _validated_runs[root] = (signature, verified)
        _validated_runs.move_to_end(root)
        while len(_validated_runs) > 8:
            _validated_runs.popitem(last=False)
        return verified


class ReplayReader:
    """Context-managed reader for both active directories and sealed archives."""

    def __init__(self, package: str | Path) -> None:
        self.package = Path(package).resolve()
        self.root: Path | None = None
        self.manifest: RunManifest | None = None
        self.status: RunStatus | None = None
        self._package_context = None

    def __enter__(self) -> "ReplayReader":
        self._package_context = open_package(self.package)
        self.root = self._package_context.__enter__()
        self.manifest = (
            validate_run_integrity(self.root, sealed=True)
            if self.package.is_file() else _validated_directory(self.root)
        )
        self.status = RunStatus.model_validate(read_json(self.root / "status.json"))
        if self.status.run_id != self.manifest.run_id:
            raise PackageError("Run status belongs to another Run")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._package_context is not None:
            self._package_context.__exit__(exc_type, exc, traceback)
        self.root = None

    def _require_open(self) -> tuple[Path, RunManifest, RunStatus]:
        if self.root is None or self.manifest is None or self.status is None:
            raise RuntimeError("ReplayReader must be used as a context manager")
        return self.root, self.manifest, self.status

    def summary(self) -> dict:
        root, manifest, status = self._require_open()
        return _run_summary(root, manifest, status)

    def experiment_world(self) -> dict:
        root, manifest, _status = self._require_open()
        experiment_root = root / manifest.experiment.path
        experiment_manifest = read_json(experiment_root / "manifest.json")
        if not isinstance(experiment_manifest, dict):
            raise PackageError("embedded experiment manifest is invalid")
        entrypoints = experiment_manifest.get("entrypoints")
        if not isinstance(entrypoints, dict) or not isinstance(entrypoints.get("world"), str):
            raise PackageError("embedded experiment world entrypoint is missing")
        world = read_json(experiment_root / entrypoints["world"])
        if not isinstance(world, dict):
            raise PackageError("embedded world is invalid")
        return world

    def experiment_agents(self) -> list[dict]:
        """Return the Agent definitions physically embedded in this Run."""

        root, manifest, _status = self._require_open()
        experiment_root = root / manifest.experiment.path
        experiment_manifest = read_json(experiment_root / "manifest.json")
        entrypoints = experiment_manifest.get("entrypoints") if isinstance(experiment_manifest, dict) else None
        relative = entrypoints.get("agents") if isinstance(entrypoints, dict) else None
        document = read_json(experiment_root / relative) if isinstance(relative, str) else None
        if not isinstance(document, dict) or not isinstance(document.get("agents"), list):
            raise PackageError("embedded experiment Agent entrypoint is invalid")
        return [dict(item) for item in document["agents"] if isinstance(item, dict)]

    def semantic_index(self) -> dict:
        """Return the exact four-level spatial index embedded by Studio."""

        root, manifest, _status = self._require_open()
        experiment_root = root / manifest.experiment.path
        experiment_manifest = read_json(experiment_root / "manifest.json")
        entrypoints = experiment_manifest.get("entrypoints") if isinstance(experiment_manifest, dict) else None
        relative = entrypoints.get("semantic_index") if isinstance(entrypoints, dict) else None
        if not isinstance(relative, str):
            raise PackageError("embedded experiment semantic index entrypoint is missing")
        document = read_json(experiment_root / relative)
        if not isinstance(document, dict):
            raise PackageError("embedded experiment semantic index is invalid")
        return document

    def asset(self, relative_path: str) -> tuple[bytes, str]:
        """Read one package-owned rendering asset without leaving the Run."""

        root, manifest, _status = self._require_open()
        relative = validate_package_path(relative_path)
        if not relative.startswith("assets/"):
            raise PackageError("Replay assets must live under the experiment assets/ directory")
        experiment_root = (root / manifest.experiment.path).resolve()
        target = (experiment_root / relative).resolve()
        try:
            target.relative_to(experiment_root)
        except ValueError as exc:
            raise PackageError("Replay asset path escapes the embedded experiment") from exc
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError(relative)
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return target.read_bytes(), media_type

    def available_steps(self) -> tuple[int, ...]:
        root, _manifest, status = self._require_open()
        expected = tuple(range(1, status.committed_step + 1))
        # A projection may lag, be locked or be deleted. Only durable status
        # and immutable frames determine which Steps are visible.
        committed_frames = tuple(
            int(path.name.removeprefix("step-").removesuffix(".json.gz"))
            for path in sorted((root / "frames").glob("step-*.json.gz"))
            if int(path.name.removeprefix("step-").removesuffix(".json.gz"))
            <= status.committed_step
        )
        if committed_frames != expected:
            raise PackageError("Replay frames disagree with Run committed boundary")
        return committed_frames

    def read_step(self, step_no: int) -> dict:
        root, manifest, status = self._require_open()
        if step_no < 1 or step_no > status.committed_step:
            raise IndexError(f"step {step_no} is outside the committed Replay boundary")
        path = root / "frames" / f"step-{step_no:06d}.json.gz"
        compressed = path.read_bytes()
        projection_path = root / "projection.json"
        if projection_path.is_file():
            try:
                projection = read_json(projection_path)
            except PackageError:
                projection = {}  # Optional derived cache, never a read barrier.
            record = (projection.get("steps") or {}).get(str(step_no)) if isinstance(projection, dict) else None
            if isinstance(record, dict) and record.get("frame_sha256") != hashlib.sha256(compressed).hexdigest():
                raise PackageError(f"Replay frame hash mismatch at step {step_no}")
        try:
            document = json.loads(gzip.decompress(compressed).decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PackageError(f"invalid Replay frame at step {step_no}") from exc
        result = document.get("result") if isinstance(document, dict) else None
        if not isinstance(result, dict):
            raise PackageError(f"Replay frame has no StepResult at step {step_no}")
        if result.get("run_id") != manifest.run_id or result.get("step_no") != step_no:
            raise PackageError(f"Replay frame identity mismatch at step {step_no}")
        return result

    def iter_steps(self, *, start: int = 1, end: int | None = None) -> Iterator[dict]:
        _root, _manifest, status = self._require_open()
        final = min(end or status.committed_step, status.committed_step)
        for step_no in range(max(1, start), final + 1):
            yield self.read_step(step_no)

    def timeline(self, *, start: int = 1, end: int | None = None) -> list[dict]:
        timeline: list[dict] = []
        for result in self.iter_steps(start=start, end=end):
            timeline.append(
                {
                    "step_no": result["step_no"],
                    "virtual_time": result["virtual_time"],
                    "agents": result.get("agents") or [],
                    "effects": result.get("effects") or [],
                    "domain_events": result.get("domain_events") or [],
                    "conversations": result.get("conversations") or [],
                }
            )
        return timeline

    def state_at(self, step_no: int) -> dict:
        """Reduce committed facts into a Web-friendly state projection."""

        agents: dict[str, dict] = {}
        object_states: dict[str, object] = {}
        conversations: dict[str, dict] = {}
        virtual_time = None
        for result in self.iter_steps(end=step_no):
            virtual_time = result.get("virtual_time")
            for agent in result.get("agents") or []:
                agents[agent["agent_key"]] = {
                    "coord": agent.get("to_coord"),
                    "location": agent.get("location"),
                    "currently": agent.get("currently"),
                    "action": agent.get("action"),
                    "path": agent.get("path"),
                }
            for event in result.get("domain_events") or []:
                if event.get("event_type") != "GAME_OBJECT_STATE_CHANGED":
                    continue
                payload = event.get("payload") or {}
                structured = payload.get("structured_payload") or {}
                object_id = structured.get("object_key")
                if object_id:
                    object_states[str(object_id)] = copy.deepcopy(structured["after"])
            for conversation in result.get("conversations") or []:
                conversations[str(conversation["conversation_id"])] = conversation
        return {
            "step_no": step_no,
            "virtual_time": virtual_time,
            "agents": agents,
            "object_states": object_states,
            "conversations": conversations,
        }


def _run_summary(root, manifest, status):
    from generative_agents.ga_protocol.recovery import boundary_snapshot
    recoverable_step = 0
    if status.committed_step:
        try:
            boundary_snapshot(root, manifest.run_id, status.committed_step)
            recoverable_step = status.committed_step
        except (OSError, ValueError):
            pass
    experiment = read_json(root / manifest.experiment.path / "manifest.json")
    attempts = []
    for path in sorted((root / "attempts").glob("*/attempt.json")):
        value = read_json(path)
        if isinstance(value, dict):
            attempts.append(value)
    return {
        "run_id": manifest.run_id,
        "experiment_id": manifest.experiment.experiment_id,
        "experiment": experiment.get("experiment") if isinstance(experiment, dict) else None,
        "status": status.status.value,
        "committed_step": status.committed_step,
        "requested_steps": manifest.requested_steps,
        "recoverable_step": recoverable_step,
        "attempts": attempts,
        "lineage": manifest.lineage.model_dump(mode="json"),
    }



def read_run_quality(root: Path, manifest: RunManifest, status: RunStatus):
    """Rebuild disposable diagnostics from every committed Step, without writes."""
    quality_path = root / "artifacts/quality-report.json"
    experiment_root = root / manifest.experiment.path
    experiment_manifest = read_json(experiment_root / "manifest.json")
    engine_path = experiment_root / experiment_manifest["entrypoints"]["engine"]
    frame_paths = [root / "frames" / f"step-{step:06d}.json.gz"
                   for step in range(1, status.committed_step + 1)]
    observed_paths = [engine_path, *frame_paths]
    if quality_path.is_file():
        observed_paths.append(quality_path)
    signature = (
        manifest.run_id, status.committed_step, status.updated_at.isoformat(),
        tuple((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
              for path in observed_paths for stat in [path.stat()]),
    )
    with _quality_lock:
        cached = _quality_reports.get(root)
        if cached is not None and cached[0] == signature:
            _quality_reports.move_to_end(root)
            return copy.deepcopy(cached[1])
    previous = read_json(quality_path) if quality_path.is_file() else None
    engine = read_json(engine_path)
    quality = project_run_quality(
        root, run_id=manifest.run_id, committed_step=status.committed_step,
        brain_skill=engine["brain_skill"], evaluated_at=status.updated_at.isoformat(),
        previous_report=previous if isinstance(previous, dict) else None,
    )
    with _quality_lock:
        _quality_reports[root] = (signature, copy.deepcopy(quality))
        _quality_reports.move_to_end(root)
        while len(_quality_reports) > 8:
            _quality_reports.popitem(last=False)
    return quality


def read_run_status(package):
    """Read navigation status only; no frames, quality projection or recovery audit.

    Archives are read in place so a list never extracts a complete Run. Identity
    checks still use the manifests inside the package, not the catalog record.
    """
    package = Path(package)

    def read_status(document):
        manifest = RunManifest.model_validate(document('run.json'))
        status = RunStatus.model_validate(document('status.json'))
        experiment = document(f'{manifest.experiment.path}/manifest.json')
        if status.run_id != manifest.run_id:
            raise PackageError('Run status belongs to another Run')
        if (experiment.get('experiment') or {}).get('experiment_id') != manifest.experiment.experiment_id:
            raise PackageError('Run status experiment identity mismatch')
        return manifest, status

    if package.is_file():
        with zipfile.ZipFile(package) as archive:
            return read_status(lambda relative: json.loads(archive.read(relative)))
    return read_status(lambda relative: read_json(package / relative))


def read_run_overview(package):
    """Read current file-backed status for polling, without revalidating world/assets.

    This is a status overview, not an integrity verdict or a replay open operation.
    """
    with open_package(Path(package)) as root:
        manifest = RunManifest.model_validate(read_json(root / 'run.json'))
        status = RunStatus.model_validate(read_json(root / 'status.json'))
        if status.run_id != manifest.run_id:
            raise PackageError('Run status belongs to another Run')
        summary = _run_summary(root, manifest, status)
        if (summary.get('experiment') or {}).get('experiment_id') != manifest.experiment.experiment_id:
            raise PackageError('Run overview experiment identity mismatch')
        try:
            quality = read_run_quality(root, manifest, status)
        except (PackageError, OSError, KeyError, TypeError) as exc:
            # A missing diagnostic source must not mislabel execution or hide
            # the Run; the quality view reports its own reconstruction failure.
            quality = {"quality_status": "UNAVAILABLE", "issues": [],
                       "evaluation_error": str(exc) or exc.__class__.__name__}
        return summary, status, quality
