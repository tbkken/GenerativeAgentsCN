"""Web adapter over the same file packages used by CLI Runtime and Replay."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
import shutil
from typing import Any, Literal
from uuid import uuid4
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from filelock import FileLock
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from generative_agents.ga_replay import ReplayReader
from generative_agents.ga_replay.reader import read_run_overview, read_run_quality, read_run_status
from generative_agents.ga_protocol import (
    PackageError,
    RunManifest,
    RunStatus,
    atomic_write_bytes,
    atomic_write_json,
    open_package,
    read_json,
    seal_directory,
    validate_experiment_directory,
)
from generative_agents.ga_protocol.io import canonical_json_bytes, checked_package_path, iter_package_files
from generative_agents.ga_protocol.artifacts import read_artifact_provenance, record_artifact_provenance
from generative_agents.ga_runtime.service import RunService as PortableRunService
from generative_agents.ga_runtime.supervisor import FileRunSupervisor
from generative_agents.ga_studio.catalog import StudioPackageCatalogService
from generative_agents.services.catalog import AssetService, SecretService
from generative_agents.ga_studio.resources import (
    StudioAgentDefinition,
    StudioResourceError,
    StudioResourceService,
)
from generative_agents.ga_studio.workspace import (
    WorkspaceConflictError,
    AgentPlacement,
    ExperimentSelection,
    ExperimentWorkspaceService,
)
from functools import wraps
from generative_agents.ga_protocol.locking import package_lock


class PortableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentResourceCreate(PortableRequest):
    definition: StudioAgentDefinition
    description: str = ""


class AgentResourceUpdate(AgentResourceCreate):
    row_version: int = Field(ge=1)


class CrowdResourceCreate(PortableRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    crowd_key: str | None = None
    agent_ids: list[str] = Field(default_factory=list)


class CrowdResourceUpdate(PortableRequest):
    row_version: int = Field(ge=1)
    name: str | None = None
    description: str | None = None
    agent_ids: list[str] = Field(default_factory=list)


class DocumentResourceCreate(PortableRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    key: str | None = None
    config: dict


class DocumentResourceUpdate(PortableRequest):
    row_version: int = Field(ge=1)
    name: str | None = None
    description: str | None = None
    config: dict


class PlacementRequest(PortableRequest):
    agent_id: str
    coord: tuple[int, int]


class ExperimentWorkspaceCreate(PortableRequest):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(default="", max_length=20_000)
    owner: str = Field(default="", max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)
    key: str | None = None
    timezone: str = "Asia/Shanghai"
    map_id: str
    brain_skill_id: str
    model_preset_id: str
    embedding_model_preset_id: str | None = None
    agent_ids: list[str] = Field(default_factory=list)
    crowd_ids: list[str] = Field(default_factory=list)
    evaluator_ids: list[str] = Field(default_factory=list)
    placements: list[PlacementRequest] = Field(default_factory=list)
    simulation: dict | None = None


class ExperimentEntrypointUpdate(PortableRequest):
    document: dict


class ExperimentDefinitionUpdate(PortableRequest):
    definition: dict
    expected_content_sha256: str | None = None


class ResumeRunRequest(PortableRequest):
    checkpoint_step: int | None = Field(default=None, ge=1)
    expected_attempt_id: str | None = None


class SecretCreate(PortableRequest):
    kind: str
    value: str = Field(min_length=1)


class ArtifactJobCreate(PortableRequest):
    job_type: str
    parameters: dict = Field(default_factory=dict)


class ExperimentBatchRequest(PortableRequest):
    experiment_ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["ARCHIVE", "RESTORE"]


def _catalog_item(row) -> dict:
    return {
        "package_kind": row.package_kind,
        "package_id": row.package_id,
        "experiment_id": row.experiment_id,
        "run_id": row.run_id,
        "location": row.location,
        "display_name": row.display_name,
        "status": row.package_status,
        "content_sha256": row.content_sha256,
        "is_archive": row.is_archive,
        "updated_at": row.updated_at.isoformat(),
    }


def _experiment_definition(root: Path, *, _reader=None, summary_only=False) -> tuple[dict, dict]:
    """Read the complete experiment definition from package entrypoints."""

    if _reader is None and root.is_file():
        # Sealed definitions can be read directly, without extracting every asset.
        with zipfile.ZipFile(root) as archive:
            return _experiment_definition(root, _reader=lambda relative: json.loads(archive.read(relative)), summary_only=summary_only)
    read_document = _reader or (lambda relative: read_json(root / relative))
    manifest = read_document("manifest.json")
    entrypoints = manifest.get("entrypoints") if isinstance(manifest, dict) else None
    identity = manifest.get("experiment") if isinstance(manifest, dict) else None
    if not isinstance(entrypoints, dict) or not isinstance(identity, dict):
        raise PackageError("experiment manifest is missing identity or entrypoints")

    def document(name: str) -> dict:
        relative = entrypoints.get(name)
        value = read_document(relative) if isinstance(relative, str) else None
        if not isinstance(value, dict):
            raise PackageError(f"experiment entrypoint is invalid: {name}")
        value = dict(value)
        value.pop("schema_version", None)
        return value

    agents = document("agents")
    if summary_only:
        return manifest, {
            "agents": list(agents.get("agents") or []),
            "world": document("world"),
            "simulation": document("simulation"),
        }
    evaluation = document("evaluation") if entrypoints.get("evaluation") else {}
    definition = {
        "schema_version": 1,
        "experiment": dict(identity),
        "engine": document("engine"),
        "simulation": document("simulation"),
        "results": dict(evaluation.get("results") or {}),
        "models": document("models"),
        "world": document("world"),
        "agents": list(agents.get("agents") or []),
        "crowds": list(agents.get("crowds") or []),
        "evaluation": {"evaluators": list(evaluation.get("evaluators") or [])},
    }
    return manifest, definition


def create_portable_router(
    database,
    *,
    package_root: str | Path,
    max_concurrent_runs: int = 2,
) -> APIRouter:
    """Expose catalog and Replay without routing through legacy result tables."""

    router = APIRouter(prefix="/api/studio", tags=["package-studio"])
    catalog = StudioPackageCatalogService(database)
    resources = StudioResourceService(database)
    runtime = PortableRunService()
    package_root = Path(package_root).resolve()
    package_root.mkdir(parents=True, exist_ok=True)
    asset_service = AssetService(database, var_dir=package_root.parent)
    secret_service = SecretService(database, var_dir=package_root.parent)
    workspaces = ExperimentWorkspaceService(
        database,
        package_root=package_root,
        var_dir=package_root.parent,
    )
    supervisor = FileRunSupervisor(
        max_concurrent_runs=max_concurrent_runs,
        on_complete=lambda path: catalog.upsert(path),
    )
    presentation_path = package_root.parent / "studio-presentation.json"

    def serialized_experiment(operation):
        @wraps(operation)
        def wrapped(experiment_id, *args, **kwargs):
            with package_lock(package_root / 'experiments' / f'{experiment_id}.identity'):
                row = catalog.get('experiment', experiment_id)
                if row is None:
                    return operation(experiment_id, *args, **kwargs)
                with package_lock(Path(row.location)):
                    return operation(experiment_id, *args, **kwargs)
        return wrapped

    @serialized_experiment
    def experiment_snapshot(experiment_id, *, summary_only=False):
        row = catalog.get('experiment', experiment_id)
        if row is None:
            raise HTTPException(status_code=404, detail='experiment is not in the Studio package catalog')
        manifest, definition = _experiment_definition(Path(row.location), summary_only=summary_only)
        return row, manifest, definition

    def read_presentation() -> dict[str, Any]:
        if not presentation_path.is_file():
            return {"schema_version": 1, "experiments": {}}
        document = read_json(presentation_path)
        if not isinstance(document, dict):
            raise PackageError("Studio presentation metadata is invalid")
        document.setdefault("schema_version", 1)
        document.setdefault("experiments", {})
        return document

    def write_presentation(document: dict[str, Any]) -> None:
        atomic_write_json(presentation_path, document)

    def experiment_presentation(experiment_id: str) -> dict[str, Any]:
        document = read_presentation()
        values = document.get("experiments") or {}
        item = values.get(experiment_id) if isinstance(values, dict) else None
        return dict(item) if isinstance(item, dict) else {}

    def update_experiment_presentation(experiment_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        if catalog.get("experiment", experiment_id) is None:
            raise HTTPException(status_code=404, detail="experiment is not in the Studio package catalog")
        document = read_presentation()
        values = document.setdefault("experiments", {})
        current = dict(values.get(experiment_id) or {})
        current.update(changes)
        values[experiment_id] = current
        write_presentation(document)
        return current

    def studio_call(operation):
        try:
            return operation()
        except (StudioResourceError, ValueError) as exc:
            status = 404 if "does not exist" in str(exc) else 409 if "changed" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    def run_summary(row) -> dict:
        try:
            summary, status, quality = read_run_overview(Path(row.location))
        except (PackageError, OSError) as exc:
            # A failed deletion or damaged package must remain selectable for
            # diagnosis/deletion. This is navigation metadata, never Replay.
            location = Path(row.location)
            if not location.exists():
                for journal in (package_root.parent / 'run-recycle-bin').glob('*/deletion.json'):
                    pending = read_json(journal)
                    if pending.get('run_id') == row.run_id and pending.get('source') == str(location.absolute()):
                        location = journal.parent / 'payload'
                        break
            with open_package(location) as root:
                manifest = read_json(root / 'run.json')
                status = RunStatus.model_validate(read_json(root / 'status.json'))
            if manifest.get('run_id') != row.run_id or status.run_id != row.run_id:
                raise HTTPException(status_code=409, detail='Run 包身份不一致') from exc
            summary = {
                'run_id': status.run_id, 'experiment_id': manifest['experiment']['experiment_id'],
                'status': status.status.value, 'committed_step': status.committed_step,
                'requested_steps': status.total_steps, 'recoverable_step': 0,
                'package_error': ('Run 已移入回收暂存目录，删除索引待更新；请重试删除。'
                                  if location != Path(row.location) else
                                  'Run 包不完整，无法回放或恢复；可重新执行删除以清理残留记录。'),
            }
            quality = None
        attempts = summary.get("attempts") or []
        started_at = attempts[0].get("started_at") if attempts else None
        terminal = summary["status"] in {"COMPLETED", "FAILED", "CANCELLED"}
        finished_at = attempts[-1].get("finished_at") if terminal and attempts else None
        return {
            **summary,
            "id": summary["run_id"],
            "status": summary["status"],
            "completed_steps": summary["committed_step"],
            "requested_steps": summary["requested_steps"],
            "active_attempt_id": status.active_attempt_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "recoverable": summary["status"] in {"PAUSED", "FAILED"}
            and summary.get("recoverable_step", 0) > 0,
            "recoverable_step": summary.get("recoverable_step", 0),
            "quality": quality,
            "updated_at": row.updated_at.isoformat(),
        }

    def run_list_summary(row) -> dict:
        manifest, status = read_run_status(Path(row.location))
        if manifest.run_id != row.run_id or manifest.experiment.experiment_id != row.experiment_id:
            raise HTTPException(status_code=409, detail='Run 包身份与目录索引不一致')
        return {
            'id': manifest.run_id,
            'run_id': manifest.run_id,
            'experiment_id': manifest.experiment.experiment_id,
            'status': status.status.value,
            'completed_steps': status.committed_step,
            'requested_steps': manifest.requested_steps,
            'active_attempt_id': status.active_attempt_id,
        }

    def read_run_facts(run_id: str) -> dict[str, Any]:
        try:
            return read_run_facts_unlocked(run_id)
        except (PackageError, OSError) as exc:
            if catalog.get('run', run_id) is None:
                raise HTTPException(status_code=404, detail='Run 已删除，请刷新列表') from exc
            raise HTTPException(status_code=409, detail='Run 包不完整或正在移入回收站，无法读取结果；请查看 Run 状态') from exc

    def read_run_facts_unlocked(run_id: str) -> dict[str, Any]:
        """Build Web projections exclusively from one Run package."""

        with ReplayReader(run_location(run_id)) as replay:
            summary = replay.summary()
            definitions = replay.experiment_agents()
            frames = list(replay.iter_steps())
            root, manifest, status = replay.root, replay.manifest, replay.status
            if root is None or manifest is None or status is None:  # pragma: no cover
                raise PackageError("Replay reader did not open the Run package")
            _experiment_manifest, definition = _experiment_definition(
                root / manifest.experiment.path
            )
            checkpoint_steps = {
                int(path.name.removeprefix("step-"))
                for path in (root / "checkpoints").glob("step-*")
                if path.name.removeprefix("step-").isdigit()
                and int(path.name.removeprefix("step-")) <= status.committed_step
                and (path / "bundle.json").is_file()
            }
            artifacts = []
            artifact_root = checked_package_path(root / "artifacts")
            if artifact_root.is_dir():
                for _relative, path in iter_package_files(artifact_root):
                    if not path.is_file() or path.is_symlink() or path.name.startswith("."):
                        continue
                    content = path.read_bytes()
                    relative = path.relative_to(artifact_root).as_posix()
                    digest = hashlib.sha256(content).hexdigest()
                    artifacts.append(
                        {
                            "artifact_id": digest[:32],
                            "type": path.suffix.removeprefix(".").upper() or "FILE",
                            "logical_name": relative,
                            "media_type": mimetypes.guess_type(path.name)[0]
                            or "application/octet-stream",
                            "size_bytes": len(content),
                            "sha256": digest,
                            **read_artifact_provenance(
                                root, run_id=run_id, logical_name=relative,
                                digest=digest, size_bytes=len(content),
                            ),
                            "state": "READY",
                        }
                    )
            log_path = checked_package_path(root / "logs" / "runtime-process.log")
            log = {
                "available": log_path.is_file(),
                "size_bytes": log_path.stat().st_size if log_path.is_file() else 0,
            }
            trace_records = []
            trace_root = root / "traces"
            if trace_root.is_dir():
                for path in sorted(trace_root.glob("*.jsonl")):
                    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(item, dict):
                            item["trace_id"] = f"{item.get('attempt_id', '')}:{item.get('event_seq', 0)}"
                            trace_records.append(item)
        return {
            "summary": summary,
            "definitions": definitions,
            "definition": definition,
            "frames": frames,
            "status": status.model_dump(mode="json"),
            "checkpoint_steps": checkpoint_steps,
            "artifacts": artifacts,
            "log": log,
            "traces": trace_records,
        }

    def definition_names(facts: dict[str, Any]) -> dict[str, str]:
        return {
            str(item.get("agent_key")): str(
                item.get("name") or item.get("display_name") or item.get("agent_key")
            )
            for item in facts["definitions"]
            if item.get("agent_key")
        }

    def packaged_asset_url(run_id: str, logical_path: object) -> str | None:
        value = str(logical_path or "")
        if not value.startswith("assets/"):
            return None
        return f"/api/studio/runs/{run_id}/replay/assets/{value.removeprefix('assets/')}"

    def event_view(
        self_event: dict[str, Any],
        *,
        step_no: int,
        virtual_time: str,
        names: dict[str, str],
    ) -> dict[str, Any]:
        payload = dict(self_event.get("payload") or {})
        agent_keys = list(self_event.get("agent_keys") or [])
        primary = agent_keys[0] if agent_keys else None
        structured = payload.get("structured_payload") or {}
        address = structured.get("after_address") or structured.get("address") or []
        title = payload.get("description") or structured.get("description") or ""
        if not title:
            title = " / ".join(
                str(payload.get(key) or "") for key in ("subject", "predicate", "object")
                if payload.get(key)
            ) or str(self_event.get("event_type") or "事件")
        return {
            "event_id": str(self_event.get("event_id") or f"{step_no}:{self_event.get('sequence', 0)}"),
            "step_no": step_no,
            "virtual_time": virtual_time,
            "event_type": str(self_event.get("event_type") or "DOMAIN_EVENT"),
            "primary_agent_key": primary,
            "primary_agent_name": names.get(str(primary), str(primary or "")),
            "title": title,
            "detail": structured.get("description") or payload.get("detail") or "",
            "location": " / ".join(str(item) for item in address),
            "importance_score": payload.get("importance_score"),
            "source_type": "STEP_RESULT",
            "source_id": str(self_event.get("event_id") or ""),
            "agent_keys": agent_keys,
            "payload": payload,
        }

    def conversation_views(
        facts: dict[str, Any], names: dict[str, str]
    ) -> list[dict[str, Any]]:
        conversations: dict[str, dict[str, Any]] = {}
        seen_messages: dict[str, set[str]] = {}
        for frame in facts["frames"]:
            for raw in frame.get("conversations") or []:
                conversation_id = str(raw.get("conversation_id") or "")
                if not conversation_id:
                    continue
                participants = list(raw.get("participant_agent_keys") or [])
                item = conversations.setdefault(
                    conversation_id,
                    {
                        "conversation_id": conversation_id,
                        "start_step": frame["step_no"],
                        "started_at": frame["virtual_time"],
                        "duration_minutes": raw.get("duration_minutes") or 0,
                        "duration_source": raw.get("duration_source") or "RECORDED",
                        "location": " / ".join(str(value) for value in raw.get("location") or []),
                        "participants": participants,
                        "participant_names": [names.get(str(key), str(key)) for key in participants],
                        "message_count": 0,
                        "summary": raw.get("summary"),
                        "ended_reason": raw.get("ended_reason"),
                        "messages": [],
                    },
                )
                item["duration_minutes"] = raw.get("duration_minutes") or item["duration_minutes"]
                item["summary"] = raw.get("summary") or item["summary"]
                item["ended_reason"] = raw.get("ended_reason") or item["ended_reason"]
                known = seen_messages.setdefault(conversation_id, set())
                for message in raw.get("messages") or []:
                    message_id = str(message.get("message_id") or f"{conversation_id}:{message.get('sequence')}")
                    if message_id in known:
                        continue
                    known.add(message_id)
                    speaker = str(message.get("speaker_agent_key") or "")
                    item["messages"].append(
                        {
                            **dict(message),
                            "message_id": message_id,
                            "speaker_name": names.get(speaker, speaker),
                            "observed_at": frame["virtual_time"],
                        }
                    )
                item["message_count"] = len(item["messages"])
        return sorted(conversations.values(), key=lambda item: item["start_step"], reverse=True)

    def memory_views(
        facts: dict[str, Any], names: dict[str, str]
    ) -> list[dict[str, Any]]:
        memories: dict[tuple[str, str], dict[str, Any]] = {}
        states = {
            "CREATED": "ACTIVE",
            "EXPIRED": "EXPIRED",
            "EVICTED": "EVICTED",
            "SUPERSEDED": "SUPERSEDED",
            "INVALIDATED": "INVALIDATED",
        }
        for frame in facts["frames"]:
            for raw in frame.get("memory_deltas") or []:
                agent_key = str(raw.get("agent_key") or "")
                memory_id = str(raw.get("memory_id") or "")
                if not agent_key or not memory_id:
                    continue
                item = memories.setdefault(
                    (agent_key, memory_id),
                    {
                        "memory_id": memory_id,
                        "agent_key": agent_key,
                        "agent_name": names.get(agent_key, agent_key),
                        "type": raw.get("memory_type") or "EVENT",
                        "origin": "STEP_RESULT",
                        "state": "ACTIVE",
                        "description": raw.get("description"),
                        "poignancy": raw.get("poignancy"),
                        "created_step": frame["step_no"],
                        "created_at": raw.get("created_at") or frame["virtual_time"],
                        "last_accessed_step": None,
                        "removed_step": None,
                        "supersedes_memory_id": raw.get("supersedes_memory_id"),
                        "superseded_by_memory_id": None,
                        "invalidated_reason": None,
                    },
                )
                kind = str(raw.get("kind") or "CREATED")
                if kind == "ACCESSED":
                    item["last_accessed_step"] = frame["step_no"]
                elif kind in states:
                    item["state"] = states[kind]
                    if kind != "CREATED":
                        item["removed_step"] = frame["step_no"]
                item["description"] = raw.get("description") or item["description"]
                item["poignancy"] = raw.get("poignancy") if raw.get("poignancy") is not None else item["poignancy"]
                if kind == "SUPERSEDED":
                    item["superseded_by_memory_id"] = raw.get("replacement_memory_id")
                if kind == "INVALIDATED":
                    item["invalidated_reason"] = raw.get("reason")
        return sorted(
            memories.values(),
            key=lambda item: (int(item["created_step"]), item["agent_key"], item["memory_id"]),
            reverse=True,
        )

    def agent_views(facts: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        names = definition_names(facts)
        stride = int(facts["definition"].get("simulation", {}).get("stride_minutes") or 1)
        definitions = {
            str(item.get("agent_key")): dict(item)
            for item in facts["definitions"]
            if item.get("agent_key") and item.get("enabled", True)
        }
        items: dict[str, dict[str, Any]] = {}
        details: dict[str, dict[str, Any]] = {}
        conversations = conversation_views(facts, names)
        memories = memory_views(facts, names)
        for key, definition in definitions.items():
            coord = list(definition.get("coord") or [0, 0])
            address_parts = (
                (definition.get("spatial") or {}).get("address") or {}
            ).get("initial_location") or []
            base = {
                "agent_key": key,
                "display_name": names.get(key, key),
                "coord": coord,
                "address": " / ".join(str(value) for value in address_parts),
                "currently": definition.get("initial_currently") or "",
                "action_count": 0,
                "movement_steps": 0,
                "conversation_count": 0,
                "message_count": 0,
                "memory_created_count": 0,
                "activity_minutes": {"REST": 0, "CHAT": 0, "MOVING": 0, "OTHER": 0},
                "updated_step": 0,
                "definition": {**definition, **(definition.get('scratch') or {}),
                               'initial_currently': definition.get('currently') or ''},
                "portrait_url": packaged_asset_url(
                    facts["summary"]["run_id"], definition.get("portrait_asset")
                ),
                "plan_count": 0,
                "event_count": 0,
                "latest_activity_kind": "OTHER",
                "latest_action": definition.get("initial_currently") or "",
                "latest_virtual_time": None,
                "run_status": facts["summary"]["status"],
            }
            items[key] = base
            details[key] = {
                "run_id": facts["summary"]["run_id"],
                "run_status": facts["summary"]["status"],
                "agent": base,
                "steps": [],
                "latest_schedule": None,
                "plan_revisions": [],
                "actions": [],
                "events": [],
                "conversations": [item for item in conversations if key in item["participants"]],
                "memories": [item for item in memories if item["agent_key"] == key],
                "state_changes": [],
            }
        previous: dict[str, dict[str, Any]] = {}
        for frame in facts["frames"]:
            for agent in frame.get("agents") or []:
                key = str(agent.get("agent_key") or "")
                if key not in items:
                    continue
                item = items[key]
                action = dict(agent.get("action") or {})
                activity = str(agent.get("activity_kind") or "OTHER")
                coord = list(agent.get("to_coord") or agent.get("coord") or item["coord"])
                address = " / ".join(str(value) for value in agent.get("location") or agent.get("address") or [])
                step_view = {
                    "step_no": frame["step_no"],
                    "virtual_time": frame["virtual_time"],
                    "coord": coord,
                    "address": address,
                    "action": action.get("description") or "",
                    "emoji": action.get("emoji"),
                    "activity_kind": activity,
                    "sample_kind": agent.get("path_source") or "OBSERVED",
                    "currently": agent.get("currently"),
                    "decision_context": agent.get("decision_context") or {},
                }
                detail = details[key]
                detail["steps"].append(step_view)
                detail["actions"].append(step_view)
                item["coord"] = coord
                item["address"] = address
                item["currently"] = agent.get("currently") or action.get("description") or item["currently"]
                item["action_count"] += 1
                if list(agent.get("from_coord") or coord) != coord:
                    item["movement_steps"] += 1
                item["activity_minutes"].setdefault(activity, 0)
                item["activity_minutes"][activity] += stride
                item["updated_step"] = frame["step_no"]
                item["latest_activity_kind"] = activity
                item["latest_action"] = action.get("description") or ""
                item["latest_virtual_time"] = frame["virtual_time"]
                before = previous.get(key)
                if before and before.get("currently") != item["currently"]:
                    detail["state_changes"].append(
                        {
                            "step_no": frame["step_no"],
                            "title": "当前状态",
                            "before": before.get("currently"),
                            "after": item["currently"],
                            "kind": "STATE",
                        }
                    )
                previous[key] = {"currently": item["currently"], "coord": coord}
            for schedule in frame.get("schedule_revisions") or []:
                key = str(schedule.get("agent_key") or "")
                if key not in details:
                    continue
                view = {
                    "revision_no": len(details[key]["plan_revisions"]) + 1,
                    "effective_step": frame["step_no"],
                    "effective_at": frame["virtual_time"],
                    "reason": schedule.get("reason") or "计划更新",
                    "items": list(schedule.get("schedule") or []),
                }
                details[key]["plan_revisions"].append(view)
                details[key]["latest_schedule"] = view
            for raw_event in frame.get("domain_events") or []:
                view = event_view(
                    raw_event,
                    step_no=frame["step_no"],
                    virtual_time=frame["virtual_time"],
                    names=names,
                )
                for key in raw_event.get("agent_keys") or []:
                    if str(key) in details:
                        details[str(key)]["events"].append(view)
        for key, item in items.items():
            own_conversations = details[key]["conversations"]
            item["conversation_count"] = len(own_conversations)
            item["message_count"] = sum(value["message_count"] for value in own_conversations)
            item["memory_created_count"] = len(details[key]["memories"])
            item["plan_count"] = len(details[key]["plan_revisions"])
            item["event_count"] = len(details[key]["events"])
            details[key]["steps"].reverse()
            details[key]["actions"].reverse()
            details[key]["plan_revisions"].reverse()
            details[key]["events"].reverse()
            details[key]["content_counts"] = {
                "plans": item["plan_count"],
                "actions": item["action_count"],
                "events": item["event_count"],
                "conversations": item["conversation_count"],
                "memories": item["memory_created_count"],
                "state_changes": len(details[key]["state_changes"]),
            }
        return sorted(items.values(), key=lambda item: item["agent_key"]), details

    @router.get("/experiments")
    def list_experiment_workspaces(
        archived: str = Query(default="active", pattern="^(active|archived|all)$"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=5, ge=5, le=5),
    ):
        rows = catalog.list(package_kind="experiment")
        presentation = read_presentation().get("experiments") or {}
        rows = [row for row in rows if archived == 'all' or
                bool((presentation.get(row.experiment_id) or {}).get('archived_at')) == (archived == 'archived')]
        rows.sort(key=lambda row: (row.updated_at, row.experiment_id), reverse=True)
        total = len(rows)
        start = (page - 1) * page_size
        rows = rows[start:start + page_size]
        visible_ids = {row.experiment_id for row in rows}
        by_experiment: dict[str, list] = {}
        if rows:
            for run in catalog.list(package_kind="run"):
                if run.experiment_id in visible_ids:
                    by_experiment.setdefault(run.experiment_id, []).append(run)
        items = []
        for row in rows:
            row, manifest, definition = experiment_snapshot(row.experiment_id, summary_only=True)
            editable = Path(row.location).is_dir()
            lifecycle = "DRAFT" if editable else "SEALED"
            metadata = dict(presentation.get(row.experiment_id) or {})
            archived_at = metadata.get("archived_at")
            item_owner = str(metadata.get("owner") or "")
            item_tags = [str(value) for value in metadata.get("tags") or []]
            recent = by_experiment.get(row.experiment_id) or []
            items.append(
                {
                    "id": row.experiment_id,
                    "experiment_id": row.experiment_id,
                    "experiment_key": manifest["experiment"].get("key"),
                    "name": row.display_name,
                    "goal": manifest["experiment"].get("goal", ""),
                    "owner": item_owner,
                    "tags": item_tags,
                    "archived_at": archived_at,
                    "status": lifecycle,
                    "package_status": lifecycle,
                    "editable": editable,
                    "content_sha256": row.content_sha256,
                    "updated_at": row.updated_at.isoformat(),
                    "run_count": len(recent),
                    "latest_run": run_list_summary(recent[0]) if recent else None,
                    "core_parameters": {
                        "agent_count": len(definition["agents"]),
                        "max_steps": definition["simulation"].get("max_steps"),
                        "world_name": definition["world"].get("world_name"),
                    },
                }
            )
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        }

    @router.get("/experiments/{experiment_id}")
    def get_experiment_workspace(experiment_id: str):
        row, manifest, definition = experiment_snapshot(experiment_id)
        runs = [item for item in catalog.list(package_kind="run") if item.experiment_id == experiment_id]
        editable = Path(row.location).is_dir()
        lifecycle = "DRAFT" if editable else "SEALED"
        presentation = experiment_presentation(experiment_id)
        snapshot = {
            "id": experiment_id,
            "state": lifecycle,
            "lock_version": 1,
            "definition_hash": row.content_sha256 or "",
            "definition": definition,
        }
        payload = {
            "id": experiment_id,
            "experiment_id": experiment_id,
            "name": manifest["experiment"].get("name", row.display_name),
            "goal": manifest["experiment"].get("goal", ""),
            "owner": str(presentation.get("owner") or ""),
            "tags": [str(value) for value in presentation.get("tags") or []],
            "archived_at": presentation.get("archived_at"),
            "row_version": 1,
            "manifest": manifest,
            "definition": definition,
            "status": lifecycle,
            "editable": editable,
            "current_draft": snapshot if editable else None,
            "current_published": snapshot if not editable else None,
            "content_sha256": row.content_sha256,
            "updated_at": row.updated_at.isoformat(),
            "run_count": len(runs),
            "latest_run": run_summary(runs[0]) if runs else None,
        }
        # This sync endpoint runs in the worker pool. Encode the large author
        # document here; recursive event-loop serialization otherwise blocks
        # unrelated Run status, replay and asset requests for seconds.
        return Response(json.dumps(payload, ensure_ascii=False), media_type="application/json")

    @router.put("/experiments/{experiment_id}")
    def replace_experiment_definition(
        experiment_id: str,
        body: ExperimentDefinitionUpdate,
    ):
        try:
            result = workspaces.replace_definition(experiment_id, body.definition, expected_content_sha256=body.expected_content_sha256)
        except WorkspaceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "id": experiment_id,
            "state": "DRAFT",
            "lock_version": 1,
            "definition_hash": result["content_sha256"],
            "definition": result["definition"],
        }

    @router.post("/experiments/{experiment_id}/validate")
    @serialized_experiment
    def validate_experiment_workspace(experiment_id: str):
        row = catalog.get("experiment", experiment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="experiment is not in the Studio package catalog")
        try:
            with open_package(Path(row.location)) as root:
                validate_experiment_directory(root)
        except Exception as exc:
            return {
                "valid": False,
                "definition_hash": row.content_sha256 or "",
                "counts": {"blocking": 1, "warning": 0, "automatic": 0, "passed": 0},
                "errors": [{"code": "PACKAGE_INVALID", "message": str(exc)}],
                "warnings": [],
            }
        return {
            "valid": True,
            "definition_hash": row.content_sha256 or "",
            "counts": {"blocking": 0, "warning": 0, "automatic": 0, "passed": 1},
            "errors": [],
            "warnings": [],
        }

    @router.get("/experiments/{experiment_id}/estimate")
    def estimate_experiment_run(experiment_id: str):
        row = catalog.get("experiment", experiment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="experiment is not in the Studio package catalog")
        with open_package(Path(row.location)) as root:
            _manifest, definition = _experiment_definition(root)
        agents = len([item for item in definition["agents"] if item.get("enabled", True)])
        from generative_agents.modules.game_object_interaction import GameObjectInteractionSystem
        objects = len(tuple(GameObjectInteractionSystem._from_world(definition["world"]["definition"])))
        steps = int(definition["simulation"].get("max_steps") or 1)
        calls = (agents + objects) * steps
        return {
            "experiment_id": experiment_id,
            "definition_hash": row.content_sha256 or "",
            "lock_version": 1,
            "basis": "按实验包内 Agent 与绑定 Skill 的对象数量、Step 数和 Skill 调用链估算；实际消耗受重试、上下文规模和模型吞吐影响。",
            "scale": {
                "execution_mode": "SKILL_BRAIN",
                "agents": agents,
                "skill_bound_objects": objects,
                "steps": steps,
                "brain_skill": definition["engine"].get("brain_skill", ""),
            },
            "estimate": {
                "model_calls": {"low": calls, "high": calls * 3},
                "tokens": {"low": calls * 500, "high": calls * 3000},
                "wall_seconds": {"low": calls * 2, "high": calls * 30},
                "storage_bytes": {"low": calls * 2048, "high": calls * 32768},
            },
            "high_scale": calls > 10_000,
            "threshold_reasons": ["（Agent + Skill 对象）× Step 超过 10000"] if calls > 10_000 else [],
        }

    @router.get("/experiments/{experiment_id}/runs")
    def list_experiment_runs(experiment_id: str):
        items = [
            run_summary(row)
            for row in catalog.list(package_kind="run")
            if row.experiment_id == experiment_id
        ]
        return {"items": items, "next_cursor": None}

    @router.get("/runs/{run_id}")
    def get_run(run_id: str):
        row = catalog.get("run", run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Run package is not in the Studio catalog")
        return run_summary(row)

    @router.get("/runs/{run_id}/results/timeline")
    def result_timeline(
        run_id: str,
        from_step: int = Query(default=1, ge=1),
        to_step: int | None = Query(default=None, ge=1),
        limit: int = Query(default=200, ge=1, le=500),
    ):
        facts = read_run_facts(run_id)
        names = definition_names(facts)
        frames = [
            frame for frame in facts["frames"]
            if frame["step_no"] >= from_step
            and (to_step is None or frame["step_no"] <= to_step)
        ][:limit]
        events = [
            event_view(
                raw,
                step_no=frame["step_no"],
                virtual_time=frame["virtual_time"],
                names=names,
            )
            for frame in frames
            for raw in frame.get("domain_events") or []
        ]
        agent_steps = []
        steps = []
        for frame in frames:
            agents = list(frame.get("agents") or [])
            conversations = list(frame.get("conversations") or [])
            memories = list(frame.get("memory_deltas") or [])
            usage = list(frame.get("committed_model_usage") or [])
            steps.append(
                {
                    "step_no": frame["step_no"],
                    "virtual_time": frame["virtual_time"],
                    "actions": len(agents),
                    "movements": sum(
                        list(item.get("from_coord") or []) != list(item.get("to_coord") or [])
                        for item in agents
                    ),
                    "conversations": len(conversations),
                    "messages": sum(len(item.get("messages") or []) for item in conversations),
                    "memories_created": sum(
                        str(item.get("kind")) == "CREATED" for item in memories
                    ),
                    "model_calls": len(usage),
                    "checkpoint": frame["step_no"] in facts["checkpoint_steps"],
                    "sample_kind": "OBSERVED",
                }
            )
            for agent in agents:
                key = str(agent.get("agent_key") or "")
                action = agent.get("action") or {}
                agent_steps.append(
                    {
                        "step_no": frame["step_no"],
                        "agent_key": key,
                        "agent_name": names.get(key, key),
                        "coord": list(agent.get("to_coord") or []),
                        "address": " / ".join(str(value) for value in agent.get("location") or []),
                        "action": action.get("description") or "",
                        "emoji": action.get("emoji"),
                        "activity_kind": agent.get("activity_kind") or "OTHER",
                        "sample_kind": agent.get("path_source") or "OBSERVED",
                    }
                )
        return {
            "run_id": run_id,
            "available_step": facts["summary"]["committed_step"],
            "requested_steps": facts["summary"]["requested_steps"],
            "steps": steps,
            "events": events,
            "agent_steps": agent_steps,
        }

    @router.get("/runs/{run_id}/results/agents")
    def result_agents(run_id: str):
        facts = read_run_facts(run_id)
        items, _details = agent_views(facts)
        return {"run_id": run_id, "items": items}

    @router.get("/runs/{run_id}/results/agents/{agent_key}")
    def result_agent(run_id: str, agent_key: str):
        facts = read_run_facts(run_id)
        _items, details = agent_views(facts)
        if agent_key not in details:
            raise HTTPException(status_code=404, detail="Agent is not present in this Run package")
        return details[agent_key]

    @router.get("/runs/{run_id}/results/conversations")
    def result_conversations(
        run_id: str,
        agent_key: str | None = None,
        q: str = "",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        facts = read_run_facts(run_id)
        items = conversation_views(facts, definition_names(facts))
        if agent_key:
            items = [item for item in items if agent_key in item["participants"]]
        if q:
            needle = q.casefold()
            items = [
                item for item in items
                if needle in json.dumps(item, ensure_ascii=False).casefold()
            ]
        selected = items[offset : offset + limit]
        return {
            "run_id": run_id,
            "items": [{key: value for key, value in item.items() if key != "messages"} for item in selected],
            "next_offset": offset + limit if offset + limit < len(items) else None,
        }

    @router.get("/runs/{run_id}/results/conversations/{conversation_id}")
    def result_conversation(run_id: str, conversation_id: str):
        facts = read_run_facts(run_id)
        for item in conversation_views(facts, definition_names(facts)):
            if item["conversation_id"] == conversation_id:
                return {"run_id": run_id, **item}
        raise HTTPException(status_code=404, detail="Conversation is not present in this Run package")

    @router.get("/runs/{run_id}/results/memories")
    def result_memories(
        run_id: str,
        agent_key: str | None = None,
        memory_type: str | None = None,
        state: str | None = None,
        q: str = "",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        facts = read_run_facts(run_id)
        items = memory_views(facts, definition_names(facts))
        if agent_key:
            items = [item for item in items if item["agent_key"] == agent_key]
        if memory_type:
            items = [item for item in items if item["type"] == memory_type]
        if state:
            items = [item for item in items if item["state"] == state]
        if q:
            needle = q.casefold()
            items = [item for item in items if needle in str(item.get("description") or "").casefold()]
        return {
            "run_id": run_id,
            "items": items[offset : offset + limit],
            "next_offset": offset + limit if offset + limit < len(items) else None,
        }

    @router.get("/runs/{run_id}/results/operations")
    def result_operations(run_id: str):
        facts = read_run_facts(run_id)
        usage: dict[tuple[str, str, str], dict[str, Any]] = {}
        for raw in facts['traces']:
            event_type = raw.get('event_type')
            if event_type not in {'LOGICAL_END', 'PHYSICAL_START', 'PHYSICAL_ATTEMPT'}:
                continue
            key = (str(raw.get('purpose') or 'unknown'), str(raw.get('provider') or 'unknown'),
                   str(raw.get('resolved_model') or 'unknown'))
            item = usage.setdefault(key, dict(purpose=key[0], provider=key[1], model=key[2],
                logical_calls=0, physical_attempts=0, retries=0, input_tokens=0,
                output_tokens=0, max_latency_ms=0))
            if event_type == 'LOGICAL_END':
                item['logical_calls'] += 1
            elif event_type == 'PHYSICAL_ATTEMPT':
                item['physical_attempts'] += 1
                item['retries'] += int(int(raw.get('attempt_no') or 1) > 1)
                item['input_tokens'] += int(raw.get('prompt_tokens') or 0)
                item['output_tokens'] += int(raw.get('completion_tokens') or 0)
                item['max_latency_ms'] = max(item['max_latency_ms'], int(raw.get('latency_ms') or 0))
        return {
            "run_id": run_id,
            "run_status": facts["summary"]["status"],
            "usage_consistency": "RUN_TRACE_EVENTS",
            "usage_committed_through_step": facts["summary"]["committed_step"],
            "attempts": facts["summary"].get("attempts") or [],
            "model_usage": list(usage.values()),
            "artifacts": facts["artifacts"],
            "artifact_jobs": [],
        }

    @router.get("/runs/{run_id}/events")
    def run_events(
        run_id: str,
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=500),
    ):
        facts = read_run_facts(run_id)
        names = definition_names(facts)
        events = []
        sequence = 0
        for frame in facts["frames"]:
            for raw in frame.get("domain_events") or []:
                sequence += 1
                if sequence <= after_id:
                    continue
                view = event_view(
                    raw,
                    step_no=frame["step_no"],
                    virtual_time=frame["virtual_time"],
                    names=names,
                )
                events.append(
                    {
                        "id": sequence,
                        "event_type": view["event_type"],
                        "created_at": frame["virtual_time"],
                        "payload": {
                            "step_no": frame["step_no"],
                            "event_id": view["event_id"],
                            **view["payload"],
                        },
                    }
                )
                if len(events) >= limit:
                    break
            if len(events) >= limit:
                break
        return {"items": events, "next_after_id": events[-1]["id"] if events else after_id}

    @router.get("/runs/{run_id}/attempts")
    def run_attempts(run_id: str):
        facts = read_run_facts(run_id)
        frame_steps: dict[str, list[int]] = {}
        for frame in facts["frames"]:
            frame_steps.setdefault(str(frame.get("attempt_id") or ""), []).append(frame["step_no"])
        items = []
        for raw in facts["summary"].get("attempts") or []:
            attempt_id = str(raw.get("attempt_id") or "")
            steps = frame_steps.get(attempt_id) or []
            items.append(
                {
                    "attempt_id": attempt_id,
                    "attempt_no": raw.get("ordinal") or len(items) + 1,
                    "status": raw.get("status") or "UNKNOWN",
                    "start_step": int(raw.get("resumed_from_step") or 0) + 1,
                    "end_step": max(steps) if steps else raw.get("resumed_from_step"),
                    "stop_reason": raw.get("failure") or raw.get("status"),
                    "started_at": raw.get("started_at"),
                    "ended_at": raw.get("finished_at"),
                    "error_message": raw.get("failure"),
                    "log": facts["log"],
                }
            )
        return {
            "run_id": run_id,
            "items": items,
            "default_attempt_id": facts["status"].get("active_attempt_id")
            or (items[-1]["attempt_id"] if items else None),
        }

    def trace_records_for(run_id):
        with open_package(run_location(run_id)) as root:
            records = []
            for path in sorted((root / 'traces').glob('*.jsonl')):
                for line in path.read_text(encoding='utf-8').splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    item['trace_id'] = f"{item.get('attempt_id', '')}:{item.get('event_seq', 0)}"
                    records.append(item)
            return records

    @router.get("/runs/{run_id}/model-traces")
    def run_model_traces(
        run_id: str,
        attempt_id: str | None = None,
        event_type: str | None = None,
        purpose: str = "",
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=500),
    ):
        records = trace_records_for(run_id)
        if attempt_id:
            records = [item for item in records if item.get("attempt_id") == attempt_id]
        if event_type == "PHYSICAL":
            records = [item for item in records if str(item.get("event_type") or "").startswith("PHYSICAL")]
        elif event_type:
            records = [item for item in records if item.get("event_type") == event_type]
        if purpose:
            records = [item for item in records if purpose.casefold() in str(item.get("purpose") or "").casefold()]
        selected = records[cursor : cursor + limit]
        next_cursor = cursor + len(selected)
        return {
            "items": selected,
            "next_cursor": next_cursor,
            "eof": next_cursor >= len(records),
        }

    @router.get("/runs/{run_id}/model-traces/{trace_id}")
    def run_model_trace_detail(
        run_id: str,
        trace_id: str,
        cursor: int = Query(default=0, ge=0),
        limit_bytes: int = Query(default=16_384, ge=1, le=1_048_576),
    ):
        record = next(
            (item for item in trace_records_for(run_id) if item["trace_id"] == trace_id),
            None,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Model trace is not present in this Run package")
        iteration_tools = []
        with ReplayReader(run_location(run_id)) as replay:
            for frame in replay.iter_steps(start=int(record.get('step_no') or 1), end=int(record.get('step_no') or 1)):
                if frame.get('step_no') != record.get('step_no') or frame.get('attempt_id') != record.get('attempt_id'):
                    continue
                for effect in frame.get('effects') or []:
                    if record.get('agent_key') not in (effect.get('agent_keys') or []):
                        continue
                    iteration_tools.extend(item for item in (effect.get('payload') or {}).get('trace', []) if item.get('event') == 'mcp.call')
        payload = record.get("payload")
        content = json.dumps(payload, ensure_ascii=False, indent=2) if payload is not None else ""
        chunk = content[cursor : cursor + limit_bytes]
        next_cursor = cursor + len(chunk)
        return {
            "trace": record,
            "iteration_tools": iteration_tools,
            "payload_diagnostic": "该 Run 未保存此请求的模型 Payload，无法还原历史请求全文；下方展示已有的同轮 MCP 事实记录。",
            "payload_available": payload is not None,
            "content": chunk,
            "next_cursor": next_cursor if next_cursor < len(content) else None,
            "file_id": record.get("payload_sha256"),
        }

    def run_log_bytes(run_id: str) -> tuple[bytes, bool]:
        with open_package(run_location(run_id)) as root:
            path = checked_package_path(root / "logs" / "runtime-process.log")
            content = path.read_bytes() if path.is_file() else b""
            status = RunStatus.model_validate(read_json(root / "status.json"))
        terminal = status.status.value in {"PAUSED", "CANCELLED", "COMPLETED", "FAILED"}
        return content, terminal

    @router.get("/runs/{run_id}/attempts/{attempt_id}/log")
    def run_attempt_log(
        run_id: str,
        attempt_id: str,
        cursor: int = Query(default=0, ge=0),
        limit_bytes: int = Query(default=65_536, ge=1, le=262_144),
    ):
        facts = read_run_facts(run_id)
        if attempt_id not in {
            str(item.get("attempt_id")) for item in facts["summary"].get("attempts") or []
        }:
            raise HTTPException(status_code=404, detail="Attempt is not present in this Run package")
        from generative_agents.services.byte_windows import read_utf8_window
        from generative_agents.services.errors import ServiceError

        terminal = facts["status"]["status"] in {"PAUSED", "CANCELLED", "COMPLETED", "FAILED"}
        with open_package(run_location(run_id)) as root:
            path = checked_package_path(root / "logs" / "runtime-process.log")
            if not path.is_file():
                return {"content": "", "next_cursor": 0, "file_id": None,
                        "starts_mid_line": False, "eof": True, "terminal": terminal}
            try:
                window = read_utf8_window(path, cursor=cursor, limit_bytes=limit_bytes)
            except ServiceError as exc:
                raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc
            starts_mid_line = False
            if cursor:
                with path.open("rb") as handle:
                    handle.seek(cursor - 1)
                    starts_mid_line = handle.read(1) not in {b"\n", b"\r"}
        return {
            "content": window.content,
            "next_cursor": window.next_cursor,
            "file_id": window.file_id,
            "starts_mid_line": starts_mid_line,
            "eof": window.eof,
            "terminal": terminal,
        }

    @router.get("/runs/{run_id}/attempts/{attempt_id}/log/download")
    def download_run_attempt_log(run_id: str, attempt_id: str):
        facts = read_run_facts(run_id)
        if attempt_id not in {str(item.get("attempt_id")) for item in facts["summary"].get("attempts") or []}:
            raise HTTPException(status_code=404, detail="Attempt is not present in this Run package")
        content, _terminal = run_log_bytes(run_id)
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="run-{run_id}-attempt-{attempt_id}.log"'},
        )

    def checkpoint_documents(run_id: str) -> list[dict[str, Any]]:
        with open_package(run_location(run_id)) as root:
            status = RunStatus.model_validate(read_json(root / "status.json"))
            documents = []
            checkpoint_root = root / "checkpoints"
            paths = {p.name: p for p in (root / "recovery").glob("step-*")}
            paths.update({p.name: p for p in checkpoint_root.glob("step-*")})
            from generative_agents.ga_protocol.recovery import validate_snapshot
            for path in sorted(paths.values(), key=lambda p: p.name, reverse=True):
                bundle_path = path / "bundle.json"
                bundle = read_json(bundle_path) if bundle_path.is_file() else None
                if not isinstance(bundle, dict):
                    continue
                step = int(bundle.get("step_no") or 0)
                try:
                    validate_snapshot(root, path, run_id, step)
                    valid, error = True, None
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    valid, error = False, str(exc)
                reason = error or (
                    f"已提交到 Step {status.committed_step}；该检查点不能重复执行之后的已提交步骤"
                    if step != status.committed_step else
                    "仅暂停或失败的仿真可以恢复" if status.status.value not in {"PAUSED", "FAILED"} else None
                )
                declared = list(bundle.get("files") or [])
                size = bundle_path.stat().st_size + sum(int(item.get("size") or 0) for item in declared if isinstance(item, dict))
                documents.append(
                    {
                        "run_id": run_id,
                        "step_no": int(bundle.get("step_no") or 0),
                        "status": "VALID" if valid else "INVALID",
                        "attempt_id": bundle.get("attempt_id"),
                        "bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
                        "virtual_time": bundle.get("virtual_time"),
                        "size_bytes": size,
                        "file_count": len(declared) + 1,
                        "validated": valid,
                        "snapshot_kind": path.parent.name,
                        "resumable": valid and reason is None,
                        "committed_step": status.committed_step,
                        "active_attempt_id": status.active_attempt_id,
                        "recovery_reason": reason,
                        "validation": {"code": "VALID" if valid else "INVALID", "reason": error},
                        "files": declared,
                    }
                )
        return documents

    @router.get("/runs/{run_id}/checkpoints")
    def run_checkpoints(run_id: str):
        return {"run_id": run_id, "items": checkpoint_documents(run_id)}

    @router.get("/runs/{run_id}/checkpoints/{step_no}")
    def run_checkpoint(run_id: str, step_no: int):
        summary = next(
            (item for item in checkpoint_documents(run_id) if item["step_no"] == step_no),
            None,
        )
        if summary is None:
            raise HTTPException(status_code=404, detail="Checkpoint is not present in this Run package")
        with open_package(run_location(run_id)) as root:
            checkpoint = root / summary["snapshot_kind"] / f"step-{step_no:06d}"
            state = read_json(checkpoint / "state.json") if summary["validated"] else {}
            conversation = read_json(checkpoint / "conversation.json") if summary["validated"] else {}
        state_agents = state.get("agents") if isinstance(state, dict) else {}
        agent_items = []
        if isinstance(state_agents, dict):
            for key, value in sorted(state_agents.items()):
                value = value if isinstance(value, dict) else {}
                agent_items.append(
                    {
                        "agent_key": key,
                        "coord": value.get("coord"),
                        "currently": value.get("currently"),
                        "action": value.get("action") or {},
                        "schedule_item_count": len((value.get("schedule") or {}).get("daily_schedule") or []) if isinstance(value.get("schedule"), dict) else len(value.get("schedule") or []),
                    }
                )
        conversation_items = []
        if isinstance(conversation, dict):
            candidate = conversation.get("items") or conversation.get("conversations") or []
            if isinstance(candidate, list):
                conversation_items = candidate
        storage_groups: dict[tuple[str, str], dict[str, Any]] = {}
        for item in summary["files"]:
            relative = str(item.get("path") or "")
            parts = relative.split("/")
            if len(parts) < 3 or parts[0] not in {"storage", "runtime-storage"}:
                continue
            key = (parts[1], parts[2] if len(parts) > 2 else parts[0])
            group = storage_groups.setdefault(
                key,
                {"agent_key": key[0], "index_type": key[1], "file_count": 0, "size_bytes": 0},
            )
            group["file_count"] += 1
            group["size_bytes"] += int(item.get("size") or 0)
        return {
            **summary,
            "agent_state": {"count": len(agent_items), "items": agent_items},
            "conversations": {"count": len(conversation_items), "items": conversation_items},
            "storage": {"group_count": len(storage_groups), "groups": list(storage_groups.values())},
            "files": [
                {
                    "path": "bundle.json",
                    "size_bytes": 0,
                    "sha256": summary["bundle_sha256"],
                },
                *[
                    {
                        "path": item.get("path"),
                        "size_bytes": item.get("size") or 0,
                        "sha256": item.get("sha256") or "",
                    }
                    for item in summary["files"]
                ],
            ],
        }

    @router.get("/runs/{run_id}/checkpoints/{step_no}/preview")
    def run_checkpoint_preview(
        run_id: str,
        step_no: int,
        section: str = Query(pattern="^(state|conversation)$"),
        cursor: int = Query(default=0, ge=0),
        limit_bytes: int = Query(default=32_768, ge=1, le=1_048_576),
        file_id: str | None = None,
    ):
        del file_id
        with open_package(run_location(run_id)) as root:
            path = root / "checkpoints" / f"step-{step_no:06d}" / f"{section}.json"
            if not path.is_file():
                path = root / "recovery" / f"step-{step_no:06d}" / f"{section}.json"
            if not path.is_file():
                raise HTTPException(status_code=404, detail="Checkpoint preview is not present")
            content = path.read_bytes()
        chunk = content[cursor : cursor + limit_bytes]
        next_cursor = cursor + len(chunk)
        return {
            "content": chunk.decode("utf-8", errors="replace"),
            "next_cursor": next_cursor if next_cursor < len(content) else None,
            "file_id": hashlib.sha256(content).hexdigest(),
        }

    @router.get("/runs/{run_id}/artifacts/{artifact_id}/download")
    def download_run_artifact(run_id: str, artifact_id: str):
        with open_package(run_location(run_id)) as root:
            artifact_root = checked_package_path(root / "artifacts")
            for _relative, path in iter_package_files(artifact_root) if artifact_root.is_dir() else []:
                if not path.is_file() or path.is_symlink() or path.name.startswith("."):
                    continue
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest()[:32] != artifact_id:
                    continue
                return Response(
                    content=content,
                    media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
                )
        raise HTTPException(status_code=404, detail="Artifact is not present in this Run package")

    def mutable_run_root(run_id: str) -> Path:
        location = run_location(run_id).resolve()
        if not location.is_dir():
            raise HTTPException(
                status_code=409,
                detail="sealed .garun files are already downloadable complete Run packages",
            )
        return location

    def write_zip(
        target: Path, root: Path, files: list[Path], *, overrides: dict[str, bytes] | None = None
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(files):
                    checked_package_path(path)
                    if path.is_file() and not path.is_symlink():
                        relative = path.relative_to(root).as_posix()
                        if relative not in (overrides or {}):
                            archive.write(path, relative)
                for relative, content in sorted((overrides or {}).items()):
                    archive.writestr(relative, content)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    @router.post("/runs/{run_id}/artifact-jobs", status_code=201)
    def create_run_artifact(run_id: str, body: ArtifactJobCreate):
        root = mutable_run_root(run_id)
        facts = read_run_facts(run_id)
        artifact_root = root / "artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        step = int(facts["summary"]["committed_step"])
        source_status = RunStatus.model_validate(facts["status"])
        if body.job_type == "RESULT_BUNDLE":
            # The existing export action also materializes the same read-only
            # quality projection shown in the UI. Keep prior artifacts intact.
            manifest = RunManifest.model_validate(read_json(root / "run.json"))
            quality = read_run_quality(root, manifest, source_status)
            quality_content = canonical_json_bytes(quality)
            quality_digest = hashlib.sha256(quality_content).hexdigest()
            quality_path = artifact_root / f"quality-report-step-{step:06d}-{quality_digest[:16]}-{uuid4().hex[:8]}.json"
            atomic_write_bytes(quality_path, quality_content)
            quality_metadata = record_artifact_provenance(root, quality_path, source_status)
            target = artifact_root / f"result-bundle-step-{step:06d}-{uuid4().hex[:8]}.zip"
            # The live Run may advance during compression. Freeze its committed
            # boundary, omit derived caches, and keep checkpoint pruning out of
            # this read. Logs/traces remain audit data, never the commit source.
            overrides = {"status.json": canonical_json_bytes(facts["status"])}
            overrides.update({
                f"attempts/{item['attempt_id']}/attempt.json": canonical_json_bytes(item)
                for item in facts["summary"].get("attempts", [])
            })
            with FileLock(str(root / "checkpoint.lock"), timeout=15), FileLock(str(root / "recovery.lock"), timeout=15):
                files = []
                for path in root.rglob("*"):
                    relative = path.relative_to(root)
                    if (
                        not path.is_file() or path.is_symlink()
                        or relative.parts[0] in {"artifacts", "artifact-metadata", "orphaned"}
                        or any(part.startswith(".") for part in relative.parts)
                        or relative.as_posix() in {"projection.json", "integrity/sha256.json"}
                        or path.name.endswith(".lock")
                    ):
                        continue
                    if relative.parts[0] in {"frames", "checkpoints", "recovery"}:
                        boundary = relative.parts[1].removeprefix("step-").removesuffix(".json.gz")
                        if not boundary.isdigit() or int(boundary) > step:
                            continue
                    if relative.parts[0] == "attempts":
                        if relative.parts[1] not in {
                            item["attempt_id"] for item in facts["summary"].get("attempts", [])
                        }:
                            continue
                        # These are the worker's mutable next-Step copies.
                        # Only checkpoint/recovery storage is committed memory.
                        if len(relative.parts) > 2 and relative.parts[2] in {"storage", "runtime-storage"}:
                            continue
                    files.append(path)
                files.extend([quality_path, quality_metadata])
                write_zip(target, root, files, overrides=overrides)
        elif body.job_type == "FILTERED_MEMORIES":
            items = memory_views(facts, definition_names(facts))
            agent_key = body.parameters.get("agent_key")
            memory_type = body.parameters.get("memory_type")
            query = str(body.parameters.get("q") or "").casefold()
            if agent_key:
                items = [item for item in items if item["agent_key"] == agent_key]
            if memory_type:
                items = [item for item in items if item["type"] == memory_type]
            if query:
                items = [item for item in items if query in str(item.get("description") or "").casefold()]
            target = artifact_root / f"memories-step-{step:06d}-{uuid4().hex[:8]}.ndjson"
            atomic_write_bytes(
                target,
                b"".join(
                    json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
                    for item in items
                ),
            )
        elif body.job_type == "FILTERED_CONVERSATIONS":
            items = conversation_views(facts, definition_names(facts))
            agent_key = body.parameters.get("agent_key")
            query = str(body.parameters.get("q") or "").casefold()
            if agent_key:
                items = [item for item in items if agent_key in item["participants"]]
            if query:
                items = [item for item in items if query in json.dumps(item, ensure_ascii=False).casefold()]
            target = artifact_root / f"conversations-step-{step:06d}-{uuid4().hex[:8]}.json"
            atomic_write_bytes(
                target,
                (json.dumps({"run_id": run_id, "items": items}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
        else:
            raise HTTPException(status_code=422, detail="unknown file-backed artifact type")
        record_artifact_provenance(root, target, source_status)
        return {
            "job_id": str(uuid4()),
            "run_id": run_id,
            "status": "SUCCEEDED",
            "artifact_name": target.name,
        }

    @router.post("/runs/{run_id}/checkpoints/{step_no}/artifact-job", status_code=201)
    def create_checkpoint_artifact(run_id: str, step_no: int):
        root = mutable_run_root(run_id)
        source_status = RunStatus.model_validate(read_json(root / "status.json"))
        if step_no < 1 or step_no > source_status.committed_step:
            raise HTTPException(status_code=422, detail="Checkpoint is outside the committed Run boundary")
        checkpoint = root / "checkpoints" / f"step-{step_no:06d}"
        if not checkpoint.is_dir():
            checkpoint = root / "recovery" / f"step-{step_no:06d}"
        if not checkpoint.is_dir():
            raise HTTPException(status_code=404, detail="Checkpoint is not present in this Run package")
        target = root / "artifacts" / f"checkpoint-step-{step_no:06d}-{uuid4().hex[:8]}.zip"
        lock = "recovery.lock" if checkpoint.parent.name == "recovery" else "checkpoint.lock"
        with FileLock(str(root / lock), timeout=15):
            if not checkpoint.is_dir() or not (checkpoint / "bundle.json").is_file():
                raise HTTPException(status_code=404, detail="Checkpoint was removed before export; refresh the checkpoint list")
            write_zip(target, checkpoint, [path for path in checkpoint.rglob("*") if path.is_file()])
        record_artifact_provenance(root, target, source_status, source_step=step_no)
        return {
            "job_id": str(uuid4()),
            "run_id": run_id,
            "status": "SUCCEEDED",
            "artifact_name": target.name,
        }

    @router.post("/experiments/{experiment_id}/archive")
    def archive_experiment(experiment_id: str):
        metadata = update_experiment_presentation(
            experiment_id,
            {"archived_at": datetime.now(UTC).isoformat()},
        )
        return {"experiment_id": experiment_id, **metadata}

    @router.post("/experiments/{experiment_id}/restore")
    def restore_experiment(experiment_id: str):
        metadata = update_experiment_presentation(experiment_id, {"archived_at": None})
        return {"experiment_id": experiment_id, **metadata}

    @router.post("/experiments/batch")
    def batch_experiments(body: ExperimentBatchRequest):
        unique_ids = list(dict.fromkeys(body.experiment_ids))
        if not unique_ids:
            raise HTTPException(status_code=422, detail="at least one experiment is required")
        action = body.action
        for experiment_id in unique_ids:
            changes = {"archived_at": datetime.now(UTC).isoformat() if action == "ARCHIVE" else None}
            update_experiment_presentation(experiment_id, changes)
        return {"affected": len(unique_ids), "action": action}

    @router.post("/experiments/{experiment_id}/duplicate", status_code=201)
    def duplicate_experiment(experiment_id: str):
        try:
            return workspaces.duplicate(experiment_id)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete("/experiments/{experiment_id}", status_code=204)
    @serialized_experiment
    def delete_experiment(experiment_id: str):
        location = catalog_location("experiment", experiment_id)
        if location.is_dir():
            shutil.rmtree(location)
        else:
            location.unlink()
        catalog.delete("experiment", experiment_id)
        document = read_presentation()
        metadata = document.get("experiments") or {}
        if isinstance(metadata, dict):
            metadata.pop(experiment_id, None)
        write_presentation(document)
        return Response(status_code=204)

    @router.delete("/runs/{run_id}", status_code=204)
    def delete_run(run_id: str):
        from generative_agents.ga_studio.run_deletion import recycle_run, RunRecycleBusy
        with package_lock(package_root / 'runs' / f'{run_id}.resume-submit.identity'):
            if supervisor.process_status(run_id).get('owned_by_this_studio'):
                raise HTTPException(status_code=409, detail='Run 执行进程仍在退出或整理结果，请稍后重试删除')
            try:
                recycle_run(catalog, run_id, package_root.parent / 'run-recycle-bin')
            except RunRecycleBusy as exc:
                raise HTTPException(status_code=409, detail='Run 文件仍被占用，尚未移入回收站；请关闭相关文件后重试，目录未被逐项删除') from exc
            except (PackageError, OSError) as exc:
                raise HTTPException(status_code=409, detail=f'删除未完成，可重试：{exc}') from exc
            except Exception as exc:
                raise HTTPException(status_code=409, detail='删除索引更新未完成；文件保留在回收暂存目录，请重试删除') from exc
        return Response(status_code=204)

    @router.post("/resources/agent-images", status_code=201)
    def upload_public_agent_images(
        portrait: UploadFile | None = File(None),
        sprite: UploadFile | None = File(None),
    ):
        images = {}
        if portrait is not None:
            images["portrait"] = (portrait.file, portrait.filename or "portrait.png")
        if sprite is not None:
            images["sprite"] = (sprite.file, sprite.filename or "sprite-4x4.png")
        try:
            result = asset_service.upload_database_images(images)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for item in result.values():
            item["content_url"] = f"/api/studio/resources/assets/{item['asset_id']}/content"
        return result

    @router.post("/resources/assets", status_code=201)
    def upload_studio_asset(file: UploadFile = File(...)):
        """Upload a mutable Studio asset used while authoring a public Map."""

        try:
            return asset_service.upload(
                file.file,
                logical_name=file.filename or "asset",
                media_type=file.content_type,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/resources/assets/{asset_id}")
    def get_studio_asset(asset_id: str):
        try:
            return asset_service.get(asset_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/resources/assets/{asset_id}/content")
    def public_asset_content(asset_id: str, request: Request):
        # Apply the same immutable content identity to file and database assets.
        try:
            metadata = asset_service.get(asset_id)
            etag = f'"{metadata["sha256"]}"'
            if etag in request.headers.get("if-none-match", "").split(", "):
                return Response(status_code=304, headers={"ETag": etag})
        except Exception as exc:
            raise HTTPException(status_code=404, detail="Asset is not available") from exc
        try:
            asset, content = asset_service.database_image_content(asset_id)
            return Response(
                content=content,
                media_type=asset.media_type,
                headers={
                    "ETag": f'"{asset.sha256}"',
                    "Cache-Control": "public, max-age=31536000, immutable",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except Exception:
            try:
                asset, path = asset_service.content(asset_id)
                return FileResponse(
                    path,
                    media_type=asset.media_type,
                    filename=asset.logical_name,
                    content_disposition_type="inline",
                    headers={
                        "ETag": f'"{asset.sha256}"',
                        "Cache-Control": "public, max-age=31536000, immutable",
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            except Exception as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/experiments/{experiment_id}/agent-images", status_code=201)
    async def upload_experiment_agent_images(
        experiment_id: str,
        expected_content_sha256: str | None = Form(None),
        portrait: UploadFile | None = File(None),
        sprite: UploadFile | None = File(None),
    ):
        uploads = {"portrait": portrait, "sprite": sprite}
        prepared: dict[str, bytes] = {}
        result: dict[str, dict[str, Any]] = {}
        for kind, upload in uploads.items():
            if upload is None:
                continue
            data = await upload.read()
            if len(data) > 2 * 1024 * 1024:
                raise HTTPException(status_code=422, detail="Agent 图片不能超过 2 MB")
            try:
                width, height = asset_service._validate_agent_png(data, kind=kind)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            logical = f"assets/agents/uploads/{uuid4().hex}-{kind}.png"
            prepared[logical] = data
            result[kind] = {
                "kind": kind,
                "width": width,
                "height": height,
                "logical_path": logical,
                "content_url": packaged_asset_url(experiment_id, logical),
            }
        if not prepared:
            raise HTTPException(status_code=422, detail="请至少选择一张 Agent 图片")
        try:
            saved = await run_in_threadpool(
                workspaces.add_assets, experiment_id, prepared,
                expected_content_sha256=expected_content_sha256,
            )
        except WorkspaceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for item in result.values():
            item["content_url"] = (
                f"/api/studio/experiments/{experiment_id}/assets/"
                f"{item['logical_path'].removeprefix('assets/')}"
            )
        return {**result, "content_sha256": saved["content_sha256"]}

    @router.get("/experiments/{experiment_id}/assets/{asset_path:path}")
    def experiment_asset(experiment_id: str, asset_path: str):
        location = catalog_location("experiment", experiment_id)
        with open_package(location) as root:
            asset_root = (root / "assets").resolve()
            target = (asset_root / asset_path).resolve()
            try:
                target.relative_to(asset_root)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="Experiment asset path is invalid") from exc
            if not target.is_file() or target.is_symlink():
                raise HTTPException(status_code=404, detail="Experiment asset is not present")
            content = target.read_bytes()
        return Response(
            content=content,
            media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )

    @router.post("/secrets", status_code=201)
    def create_secret(body: SecretCreate):
        try:
            return secret_service.create(kind=body.kind, value=body.value)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/secrets/{secret_id}/replacement", status_code=201)
    def replace_secret(secret_id: str, body: SecretCreate):
        try:
            return secret_service.create(
                kind=body.kind,
                value=body.value,
                supersedes_id=secret_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/resources/agents")
    def list_agents(include_archived: bool = False):
        return {"items": resources.list_agents(include_archived=include_archived)}

    @router.post("/resources/agents", status_code=201)
    def create_agent(body: AgentResourceCreate):
        return studio_call(
            lambda: resources.create_agent(body.definition, description=body.description)
        )

    @router.get("/resources/agents/{agent_id}")
    def get_agent(agent_id: str):
        return studio_call(lambda: resources.get_agent(agent_id))

    @router.put("/resources/agents/{agent_id}")
    def update_agent(agent_id: str, body: AgentResourceUpdate):
        return studio_call(
            lambda: resources.save_agent(
                agent_id,
                body.definition,
                expected_row_version=body.row_version,
                description=body.description,
            )
        )

    @router.delete("/resources/agents/{agent_id}", status_code=204)
    def delete_agent(agent_id: str):
        studio_call(lambda: resources.delete("agent", agent_id))
        return Response(status_code=204)

    @router.get("/resources/crowds")
    def list_crowds(include_archived: bool = False):
        return {"items": resources.list_crowds(include_archived=include_archived)}

    @router.get("/resources/crowds/{crowd_id}")
    def get_crowd(crowd_id: str):
        return studio_call(lambda: resources.get_crowd(crowd_id))

    @router.post("/resources/crowds", status_code=201)
    def create_crowd(body: CrowdResourceCreate):
        return studio_call(
            lambda: resources.create_crowd(
                name=body.name,
                description=body.description,
                crowd_key=body.crowd_key,
                agent_ids=body.agent_ids,
            )
        )

    @router.put("/resources/crowds/{crowd_id}")
    def update_crowd(crowd_id: str, body: CrowdResourceUpdate):
        return studio_call(
            lambda: resources.save_crowd(
                crowd_id,
                agent_ids=body.agent_ids,
                expected_row_version=body.row_version,
                name=body.name,
                description=body.description,
            )
        )

    @router.delete("/resources/crowds/{crowd_id}", status_code=204)
    def delete_crowd(crowd_id: str):
        studio_call(lambda: resources.delete("crowd", crowd_id))
        return Response(status_code=204)

    @router.get("/resources/model-presets")
    def list_model_presets(include_archived: bool = False):
        return {"items": resources.list_model_presets(include_archived=include_archived)}

    @router.get("/resources/model-presets/{preset_id}")
    def get_model_preset(preset_id: str):
        return studio_call(lambda: resources.get_model_preset(preset_id))

    @router.post("/resources/model-presets", status_code=201)
    def create_model_preset(body: DocumentResourceCreate):
        return studio_call(
            lambda: resources.create_model_preset(
                name=body.name,
                description=body.description,
                preset_key=body.key,
                config=body.config,
            )
        )

    @router.put("/resources/model-presets/{preset_id}")
    def update_model_preset(preset_id: str, body: DocumentResourceUpdate):
        return studio_call(
            lambda: resources.save_model_preset(
                preset_id,
                config=body.config,
                expected_row_version=body.row_version,
                name=body.name,
                description=body.description,
            )
        )

    @router.delete("/resources/model-presets/{preset_id}", status_code=204)
    def delete_model_preset(preset_id: str):
        studio_call(lambda: resources.delete("model", preset_id))
        return Response(status_code=204)

    @router.get("/resources/evaluators")
    def list_evaluators(include_archived: bool = False):
        return {"items": resources.list_evaluators(include_archived=include_archived)}

    @router.get("/resources/evaluators/{evaluator_id}")
    def get_evaluator(evaluator_id: str):
        return studio_call(lambda: resources.get_evaluator(evaluator_id))

    @router.post("/resources/evaluators", status_code=201)
    def create_evaluator(body: DocumentResourceCreate):
        return studio_call(
            lambda: resources.create_evaluator(
                name=body.name,
                description=body.description,
                evaluator_key=body.key,
                config=body.config,
            )
        )

    @router.put("/resources/evaluators/{evaluator_id}")
    def update_evaluator(evaluator_id: str, body: DocumentResourceUpdate):
        return studio_call(
            lambda: resources.save_evaluator(
                evaluator_id,
                config=body.config,
                expected_row_version=body.row_version,
                name=body.name,
                description=body.description,
            )
        )

    @router.delete("/resources/evaluators/{evaluator_id}", status_code=204)
    def delete_evaluator(evaluator_id: str):
        studio_call(lambda: resources.delete("evaluator", evaluator_id))
        return Response(status_code=204)

    @router.post("/resources/{kind}/{resource_id}/archive")
    def archive_resource(
        kind: str,
        resource_id: str,
    ):
        mapping = {
            "agents": "agent",
            "crowds": "crowd",
            "model-presets": "model",
            "evaluators": "evaluator",
        }
        if kind not in mapping:
            raise HTTPException(status_code=404, detail="unknown Studio resource kind")
        studio_call(lambda: resources.archive(mapping[kind], resource_id, archived=True))
        return {"resource_id": resource_id, "archived": True}

    @router.post("/resources/{kind}/{resource_id}/restore")
    def restore_resource(
        kind: str,
        resource_id: str,
    ):
        mapping = {
            "agents": "agent",
            "crowds": "crowd",
            "model-presets": "model",
            "evaluators": "evaluator",
        }
        if kind not in mapping:
            raise HTTPException(status_code=404, detail="unknown Studio resource kind")
        studio_call(lambda: resources.archive(mapping[kind], resource_id, archived=False))
        return {"resource_id": resource_id, "archived": False}

    @router.post("/experiments", status_code=201)
    def create_experiment_workspace(body: ExperimentWorkspaceCreate):
        created = studio_call(
            lambda: workspaces.create(
                ExperimentSelection(
                    name=body.name,
                    goal=body.goal,
                    key=body.key,
                    timezone=body.timezone,
                    map_id=body.map_id,
                    brain_skill_id=body.brain_skill_id,
                    model_preset_id=body.model_preset_id,
                    embedding_model_preset_id=body.embedding_model_preset_id,
                    agent_ids=tuple(body.agent_ids),
                    crowd_ids=tuple(body.crowd_ids),
                    evaluator_ids=tuple(body.evaluator_ids),
                    placements=tuple(
                        AgentPlacement(agent_id=item.agent_id, coord=item.coord)
                        for item in body.placements
                    ),
                    simulation=body.simulation,
                )
            )
        )
        # Owner and tags are presentation metadata maintained beside the
        # package.  Persist them during the same create request so the
        # overview immediately reflects the values entered in the wizard.
        metadata = update_experiment_presentation(
            created["experiment_id"],
            {
                "owner": body.owner.strip(),
                "tags": list(dict.fromkeys(
                    value.strip() for value in body.tags if value.strip()
                )),
            },
        )
        return {**created, **metadata}

    @router.put("/experiments/{experiment_id}/entrypoints/{section}")
    def update_experiment_entrypoint(
        experiment_id: str,
        section: str,
        body: ExperimentEntrypointUpdate,
    ):
        if section not in {"world", "agents", "models", "simulation", "engine", "evaluation"}:
            raise HTTPException(status_code=404, detail="unknown experiment entrypoint")
        try:
            return workspaces.update_entrypoint(experiment_id, section, body.document)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/packages")
    def list_packages(kind: str | None = Query(default=None, pattern="^(experiment|run)$")):
        return {"items": [_catalog_item(row) for row in catalog.list(package_kind=kind)]}

    @router.post("/packages/rebuild")
    def rebuild_packages():
        try:
            records = catalog.rebuild([package_root])
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "items": [
                {
                    "package_kind": item.package_kind,
                    "package_id": item.package_id,
                    "experiment_id": item.experiment_id,
                    "run_id": item.run_id,
                    "location": item.location,
                    "display_name": item.display_name,
                    "status": item.package_status,
                    "content_sha256": item.content_sha256,
                    "is_archive": item.is_archive,
                }
                for item in records
            ]
        }

    def catalog_location(package_kind: str, package_id: str) -> Path:
        if package_kind not in {"experiment", "run"}:
            raise HTTPException(status_code=404, detail="unknown package kind")
        row = catalog.get(package_kind, package_id)
        if row is None:
            raise HTTPException(status_code=404, detail="package is not in the Studio catalog")
        location = checked_package_path(Path(row.location))
        try:
            location.relative_to(package_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail="catalog package is outside the managed package root",
            ) from exc
        return location

    @router.get("/packages/{package_kind}/{package_id}")
    def package_detail(package_kind: str, package_id: str):
        location = catalog_location(package_kind, package_id)
        with open_package(location) as root:
            manifest_name = "manifest.json" if package_kind == "experiment" else "run.json"
            result = {
                "catalog": _catalog_item(catalog.get(package_kind, package_id)),
                "manifest": read_json(root / manifest_name),
            }
            if package_kind == "run":
                result["status"] = read_json(root / "status.json")
            return result

    @router.post("/experiments/{experiment_id}/seal")
    @serialized_experiment
    def seal_experiment(experiment_id: str):
        source = catalog_location("experiment", experiment_id)
        if source.is_file():
            return _catalog_item(catalog.get("experiment", experiment_id))
        validate_experiment_directory(source)
        archive = package_root / "experiments" / f"{experiment_id}.gaexp"
        try:
            seal_directory(source, archive)
            record = catalog.upsert(archive)
            # Once indexed, the validated archive is authoritative. Cleanup failure
            # must not delete it or turn a successful seal into a failed launch.
            cleanup_warning = None
            try:
                shutil.rmtree(source)
            except OSError as exc:
                cleanup_warning = f'实验已封存，旧工作目录清理失败：{exc}'
            return {
                "cleanup_warning": cleanup_warning,
                "experiment_id": experiment_id,
                "location": str(archive),
                "status": "SEALED",
                "content_sha256": record.content_sha256,
            }
        except Exception:
            if source.exists():
                archive.unlink(missing_ok=True)
            raise

    @router.post("/runs/{run_id}/seal")
    def seal_run(run_id: str):
        source = catalog_location("run", run_id)
        if source.is_file():
            return _catalog_item(catalog.get("run", run_id))
        archive = package_root / "runs" / f"{run_id}.garun"
        try:
            runtime.seal(source, archive)
            shutil.rmtree(source)
            record = catalog.upsert(archive)
            return {
                "run_id": run_id,
                "location": str(archive),
                "status": record.package_status,
            }
        except Exception:
            if source.exists():
                archive.unlink(missing_ok=True)
            raise

    @router.get("/packages/{package_kind}/{package_id}/download")
    def download_package(package_kind: str, package_id: str):
        location = catalog_location(package_kind, package_id)
        if not location.is_file():
            raise HTTPException(status_code=409, detail="seal the package before download")
        return FileResponse(location, filename=location.name, media_type="application/zip")

    @router.delete("/packages/{package_kind}/{package_id}", status_code=204)
    def delete_package(package_kind: str, package_id: str):
        if package_kind == 'run':
            return delete_run(package_id)
        location = catalog_location(package_kind, package_id)
        if location.is_dir():
            shutil.rmtree(location)
        else:
            location.unlink()
        catalog.delete(package_kind, package_id)
        return Response(status_code=204)

    def run_location(run_id: str) -> Path:
        location = catalog_location("run", run_id)
        with open_package(location) as root:
            if read_json(root / "run.json").get("run_id") != run_id:
                raise PackageError("catalog Run identity does not match its package")
        return location

    def experiment_location(experiment_id: str) -> Path:
        return catalog_location("experiment", experiment_id)

    def submit_directory(run_root: Path) -> dict:
        record = catalog.upsert(run_root)
        try:
            from generative_agents.ga_studio.model_services import HostModelCredentials
            environment = HostModelCredentials(database, package_root.parent).run_environment(run_root)
            supervisor.submit(run_root, environment=environment)
        except Exception as exc:
            catalog.upsert(run_root)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        status = RunStatus.model_validate(read_json(run_root / "status.json"))
        return {
            "run_id": record.run_id,
            "experiment_id": record.experiment_id,
            "status": status.status.value,
            "run_directory": str(run_root),
            "active_attempt_id": status.active_attempt_id,
            "completed_steps": status.committed_step,
            "requested_steps": status.total_steps,
        }

    @router.post("/experiments/{experiment_id}/runs")
    def start_experiment_run(
        experiment_id: str,
        steps: int | None = Query(default=None, ge=1),
    ):
        destination = package_root / "runs" / f"workspace-{uuid4().hex}"
        try:
            experiment = experiment_location(experiment_id)
            if not experiment.is_file():
                raise PackageError("seal the experiment before starting a Run")
            run_root = runtime.create(
                experiment,
                destination,
                requested_steps=steps,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return submit_directory(run_root)

    @router.post("/runs/{run_id}/resume")
    def resume_run(run_id: str, body: ResumeRunRequest | None = None):
        from generative_agents.ga_protocol.recovery import boundary_snapshot
        with package_lock(package_root / 'runs' / f'{run_id}.resume-submit.identity'):
            location = run_location(run_id)
            with open_package(location) as root:
                status = RunStatus.model_validate(read_json(root / "status.json"))
                if status.status.value not in {"PAUSED", "FAILED"}:
                    raise HTTPException(status_code=409, detail="只有暂停或失败的仿真可以恢复")
                if body and body.expected_attempt_id and body.expected_attempt_id != status.active_attempt_id:
                    raise HTTPException(status_code=409, detail="Attempt 已变化，请重新打开恢复确认")
                if body and body.checkpoint_step != status.committed_step:
                    raise HTTPException(status_code=409, detail=f"必须从已提交边界 Step {status.committed_step} 恢复，不能重复执行已提交步骤")
                try:
                    if status.committed_step:
                        boundary_snapshot(root, run_id, status.committed_step)
                except (OSError, ValueError) as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
            run_root = runtime.materialize(location, extracted_destination=package_root / "runs" / f"workspace-{uuid4().hex}") if location.is_file() else location
            return submit_directory(run_root)

    @router.post("/runs/{run_id}/rerun")
    def rerun(run_id: str, steps: int | None = Query(default=None, ge=1)):
        destination = package_root / "runs" / f"workspace-{uuid4().hex}"
        try:
            run_root = runtime.create_rerun(
                run_location(run_id),
                destination,
                requested_steps=steps,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return submit_directory(run_root)

    @router.post("/runs/{run_id}/pause")
    def pause_run(run_id: str):
        location = run_location(run_id)
        if location.is_file():
            raise HTTPException(status_code=409, detail="a sealed Run is not executing")
        runtime.request_pause(location)
        return {"run_id": run_id, "pause_requested": True}

    @router.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        location = run_location(run_id)
        if location.is_file():
            raise HTTPException(status_code=409, detail="a sealed Run is not executing")
        runtime.request_cancel(location)
        return {"run_id": run_id, "cancel_requested": True}

    @router.get("/runs/{run_id}/process")
    def runtime_process(run_id: str):
        return supervisor.process_status(run_id)

    def replay_web_manifest(run_id: str, facts: dict[str, Any]) -> dict[str, Any]:
        world = json.loads(json.dumps(facts["definition"]["world"]))
        definition = world.get("definition") or {}
        # Rendering does not consume per-tile semantic trees or the spatial
        # query index. Sending these on every poll duplicated ~15 MB of facts.
        definition.pop("semantic_index", None)
        definition["tiles"] = [
            {key: tile[key] for key in ("coord", "tile", "palette_key", "visual_slice_id", "visual_slice_part") if key in tile}
            if isinstance(tile, dict) else tile
            for tile in definition.get("tiles") or []
        ]
        editor = definition.get("editor") or {}
        editor_v2 = definition.get("editor_v2") or {}
        palette_items = definition.get("palette") or editor.get("palette") or []
        palette = {
            str(item.get("key") or item.get("id")): {
                "color": str(item.get("color") or "#d9e2df"),
                "label": str(item.get("label") or item.get("name") or item.get("key") or "Tile"),
            }
            for item in palette_items
            if isinstance(item, dict) and (item.get("key") or item.get("id"))
        }
        palette.setdefault("ground", {"color": "#d9e2df", "label": "Ground"})
        logical_by_hash = {
            str(item.get("asset_hash") or "").removeprefix("sha256:"): str(item.get("logical_path") or "")
            for item in world.get("assets") or []
            if isinstance(item, dict)
        }
        for source in editor_v2.get("material_sources") or []:
            if not isinstance(source, dict):
                continue
            logical = logical_by_hash.get(str(source.get("asset_hash") or ""))
            if logical and logical.startswith("assets/"):
                source["package_url"] = packaged_asset_url(run_id, logical)
        objects = []
        for node in editor_v2.get("hierarchy_nodes") or []:
            if not isinstance(node, dict) or node.get("kind") != "GAME_OBJECT":
                continue
            bounds = node.get("bounds") or {}
            extensions = node.get("extensions") or {}
            objects.append(
                {
                    "instance_key": str(node.get("id") or "game-object"),
                    "x": float(bounds.get("x") or 0),
                    "y": float(bounds.get("y") or 0),
                    "appearance": dict(extensions.get("appearance") or {}),
                    "state": dict(node.get("initial_state") or {}),
                }
            )
        world["render_asset"] = {
            "status": "READY",
            "source": "RUN_PACKAGE",
            "renderer": "SPATIAL_GRID",
            "pixels_per_tile": max(8, min(int(definition.get("tile_size") or 16), 64)),
            "palette": palette,
            "objects": objects,
        }
        agents = []
        for item in facts["definitions"]:
            if not item.get("enabled", True):
                continue
            key = str(item.get("agent_key") or "")
            sprite_path = item.get("sprite_asset")
            sprite_url = packaged_asset_url(run_id, sprite_path)
            sprite = (
                {
                    "status": "READY",
                    "source": "RUN_PACKAGE",
                    "texture_url": sprite_url,
                    "atlas_url": f"/static/console/replay-assets/agent-sprite-{item.get('sprite_layout') or '4x4'}.json",
                    "layout": item.get("sprite_layout") or "4x4",
                }
                if sprite_url
                else {
                    "status": "MISSING",
                    "source": "NONE",
                    "error_code": "AGENT_SPRITE_ASSET_UNRESOLVED",
                }
            )
            tags = {str(value).casefold() for value in item.get("tags") or []}
            agents.append(
                {
                    "agent_key": key,
                    "display_name": item.get("name") or item.get("display_name") or key,
                    "initial_coord": list(item.get("coord") or [0, 0]),
                    "sprite_asset": sprite,
                    "sprite_display_tiles": item.get("sprite_display_tiles"),
                    "role": "PEDESTRIAN" if any("pedestrian" in value or "行人" in value for value in tags) else None,
                }
            )
        summary = facts["summary"]
        return {
            "schema_version": 2,
            "generator_version": "ga-replay-package-v1",
            "source_kind": "RUN_FRAMES",
            "run_id": run_id,
            "experiment_id": summary["experiment_id"],
            "definition_hash": "",
            "world": world,
            "source_step": summary["committed_step"],
            "available_step": summary["committed_step"],
            "stride_minutes": int(facts["definition"]["simulation"].get("stride_minutes") or 1),
            "execution_mode": "SKILL_BRAIN",
            "brain_skill": facts["definition"]["engine"].get("brain_skill") or "",
            "step_interval_ms": None,
            "start_time": facts["definition"]["simulation"].get("start_time"),
            "requested_steps": summary["requested_steps"],
            "timezone": facts["definition"].get("experiment", {}).get("timezone") or "Asia/Shanghai",
            "agents": agents,
            "partial": summary["status"] != "COMPLETED",
        }

    def replay_web_step(
        frame: dict[str, Any], *, checkpoint: bool, attempt_boundary: bool
    ) -> dict[str, Any]:
        return {
            "step_no": frame["step_no"],
            "virtual_time": frame["virtual_time"],
            "attempt_id": frame.get("attempt_id"),
            "attempt_boundary": attempt_boundary,
            "checkpoint": checkpoint,
            "agents": [
                {
                    "agent_key": item.get("agent_key"),
                    "from_coord": list(item.get("from_coord") or []),
                    "coord": list(item.get("to_coord") or item.get("coord") or []),
                    "path": list(item.get("path") or []),
                    "path_source": item.get("path_source") or "OBSERVED",
                    "action": dict(item.get("action") or {}),
                    "address": list(item.get("location") or item.get("address") or []),
                    "currently": item.get("currently"),
                    "schedule_item_id": item.get("schedule_item_id"),
                    "decision_context": dict(item.get("decision_context") or {}),
                }
                for item in frame.get("agents") or []
            ],
            "conversations": list(frame.get("conversations") or []),
            "memory_deltas": list(frame.get("memory_deltas") or []),
            "schedule_revisions": list(frame.get("schedule_revisions") or []),
            "domain_events": list(frame.get("domain_events") or []),
            "effects": list(frame.get("effects") or []),
        }

    @router.get("/runs/{run_id}/replay/manifest")
    def replay_manifest(run_id: str):
        facts = read_run_facts(run_id)
        return replay_web_manifest(run_id, facts)

    @router.get("/runs/{run_id}/replay/availability")
    def replay_availability(run_id: str):
        summary, _status, _quality = read_run_overview(run_location(run_id))
        return {
            "run_id": run_id,
            "available_step": summary["committed_step"],
            "partial": summary["status"] != "COMPLETED",
        }

    @router.get("/runs/{run_id}/replay/steps")
    def replay_steps(
        run_id: str,
        from_step: int = Query(default=1, ge=1),
        limit: int = Query(default=100, ge=1, le=100),
    ):
        facts = read_run_facts(run_id)
        frames = facts["frames"]
        selected = [frame for frame in frames if frame["step_no"] >= from_step][:limit]
        previous_attempt = None
        for frame in frames:
            if frame["step_no"] >= from_step:
                break
            previous_attempt = frame.get("attempt_id")
        steps = []
        current_attempt = previous_attempt
        for frame in selected:
            attempt = frame.get("attempt_id")
            steps.append(
                replay_web_step(
                    frame,
                    checkpoint=frame["step_no"] in facts["checkpoint_steps"],
                    attempt_boundary=current_attempt != attempt,
                )
            )
            current_attempt = attempt
        world_state: dict[str, Any] = {}
        for frame in frames:
            if frame["step_no"] >= from_step:
                break
            for event in frame.get("domain_events") or []:
                if event.get("event_type") != "GAME_OBJECT_STATE_CHANGED":
                    continue
                payload = event.get("payload") or {}
                structured = payload.get("structured_payload") or {}
                object_key = structured.get("object_key") or structured.get("object_id")
                if object_key:
                    world_state[str(object_key)] = structured.get("after") or structured.get("state") or structured
        return {
            "run_id": run_id,
            "available_step": facts["summary"]["committed_step"],
            "result_version": facts["summary"]["committed_step"],
            "world_state_before": world_state,
            "steps": steps,
        }

    @router.get("/runs/{run_id}/replay")
    def replay_summary(run_id: str):
        with ReplayReader(run_location(run_id)) as replay:
            return replay.summary()

    @router.get("/runs/{run_id}/replay/world")
    def replay_world(run_id: str):
        with ReplayReader(run_location(run_id)) as replay:
            return replay.experiment_world()

    @router.get("/runs/{run_id}/replay/semantic-index")
    def replay_semantic_index(run_id: str):
        with ReplayReader(run_location(run_id)) as replay:
            return replay.semantic_index()

    @router.get("/runs/{run_id}/replay/assets/{asset_path:path}")
    def replay_asset(run_id: str, asset_path: str):
        try:
            with ReplayReader(run_location(run_id)) as replay:
                content, media_type = replay.asset(f"assets/{asset_path}")
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Replay asset does not exist") from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(content=content, media_type=media_type)

    @router.get("/runs/{run_id}/replay/timeline")
    def replay_timeline(
        run_id: str,
        start: int = Query(default=1, ge=1),
        end: int | None = Query(default=None, ge=1),
    ):
        with ReplayReader(run_location(run_id)) as replay:
            return {"items": replay.timeline(start=start, end=end)}

    @router.get("/runs/{run_id}/replay/state/{step_no}")
    def replay_state(run_id: str, step_no: int):
        try:
            with ReplayReader(run_location(run_id)) as replay:
                return replay.state_at(step_no)
        except (IndexError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
