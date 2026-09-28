"""Read committed StepResult frames without executing Agents or Skills."""

from __future__ import annotations
from generative_agents.ga_replay.cache import _validated_directory, read_run_quality

import copy
import json
import mimetypes
import zipfile
from collections.abc import Iterator
from pathlib import Path

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.schemas.manifests import RunManifest
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.reading import open_readonly_package, read_package_json
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.validation import validate_run_integrity
from generative_agents.ga_protocol.schemas.manifests import validate_package_path
from generative_agents.ga_protocol.packages.io import checked_package_path
from generative_agents.ga_protocol.packages.definition import _experiment_definition
from generative_agents.ga_protocol.facts.commits import read_committed_frame






class ReplayReader:
    """Context-managed reader for both active directories and sealed archives."""

    def __init__(self, package: str | Path) -> None:
        self.package = checked_package_path(Path(package))
        self.root: Path | None = None
        self.manifest: RunManifest | None = None
        self.status: RunStatus | None = None
        self._package_context = None

    def __enter__(self) -> "ReplayReader":
        self._package_context = open_readonly_package(self.package)
        self.root = self._package_context.__enter__()
        try:
            self.manifest = _validated_directory(self.root, sealed=self.package.is_file())
            self.status = RunStatus.model_validate(read_json(self.root / "status.json"))
            if self.status.run_id != self.manifest.run_id:
                raise PackageError("Run status belongs to another Run")
            self.available_steps()
        except BaseException:
            self._package_context.__exit__(None, None, None)
            self.root = None
            raise
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
        from generative_agents.ga_replay.queries import definition_for
        self._require_open()
        return definition_for(self)["world"]

    def experiment_agents(self) -> list[dict]:
        """Return the Agent definitions physically embedded in this Run."""

        from generative_agents.ga_replay.queries import definition_for
        self._require_open()
        return definition_for(self)["agents"]

    def semantic_index(self) -> dict:
        """Return the exact four-level spatial index embedded by Studio."""

        document = self.experiment_world().get("definition", {}).get("semantic_index")
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
        target = checked_package_path(experiment_root / relative)
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
            int(checked_package_path(path).name.removeprefix("step-").removesuffix(".json.gz"))
            for path in sorted(checked_package_path(root / "frames").glob("step-*.json.gz"))
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
        return read_committed_frame(root, manifest.run_id, step_no)

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
    from generative_agents.ga_protocol.facts.recovery import boundary_snapshot
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

    return read_status(lambda relative: read_package_json(package, relative))


def read_run_overview(package):
    """Read current file-backed status for polling, without revalidating world/assets.

    This is a status overview, not an integrity verdict or a replay open operation.
    """
    with open_readonly_package(Path(package)) as root:
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
