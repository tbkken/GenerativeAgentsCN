"""Idempotent import of bundled catalog data and pre-Web experiment results."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select

from generative_agents.config import definition_hash, make_builtin_definition
from generative_agents.config.schema import ExperimentDefinition
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    BuiltinCatalogSnapshot,
    Experiment,
    ExperimentRevision,
    LegacyImport,
    Run,
    RunAgentStep,
    RunAgentSummary,
    RunArtifact,
    RunAttempt,
    RunConversation,
    RunConversationParticipant,
    RunEvent,
    RunMessage,
    RunResultSummary,
    RunStep,
)


@dataclass(frozen=True, slots=True)
class LegacyCandidate:
    name: str
    checkpoint_dir: Path | None
    compressed_dir: Path | None
    source_path: str
    fingerprint: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_manifest(paths: Iterable[Path], *, root: Path) -> list[dict[str, Any]]:
    files: list[Path] = []
    for path in paths:
        if path.is_symlink():
            continue
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(item for item in path.rglob("*") if item.is_file() and not item.is_symlink())
    manifest = []
    for path in sorted(set(files), key=lambda item: item.as_posix().casefold()):
        manifest.append(
            {
                "path": path.resolve().relative_to(root.resolve()).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return manifest


def _manifest_fingerprint(manifest: list[dict[str, Any]]) -> str:
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_time(value: str) -> datetime:
    for pattern in ("%Y%m%d-%H:%M:%S", "%Y%m%d-%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unsupported legacy time: {value}")


def _safe_key(name: str, fingerprint: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "run"
    return f"legacy-{slug[:48]}-{fingerprint[:8]}"


class LegacyImportService:
    """Imports one source object per transaction and never mutates the source tree."""

    def __init__(self, database: Database, *, project_root: str | Path, var_dir: str | Path):
        self.database = database
        self.project_root = Path(project_root).resolve()
        self.var_dir = Path(var_dir).resolve()

    def bootstrap_catalog(self, *, apply: bool) -> dict[str, Any]:
        package = self.project_root / "generative_agents"
        source_paths = [
            package / "data" / "config.json",
            package / "data" / "prompts",
            package / "frontend" / "static" / "assets" / "village",
        ]
        manifest = _tree_manifest(source_paths, root=self.project_root)
        fingerprint = _manifest_fingerprint(manifest)
        with self.database.session_factory() as session:
            existing = session.scalar(
                select(BuiltinCatalogSnapshot).where(
                    BuiltinCatalogSnapshot.source_fingerprint == fingerprint
                )
            )
            if existing is not None:
                return {
                    "mode": "apply" if apply else "dry-run",
                    "created": 0,
                    "skipped": 1,
                    "warnings": [],
                    "catalog_snapshot_id": existing.id,
                    "source_fingerprint": fingerprint,
                }
        definition = make_builtin_definition(
            key="builtin-catalog", name="标准小镇目录", goal="内置创建目录快照"
        )
        digest = definition_hash(definition)
        if not apply:
            return {
                "mode": "dry-run",
                "created": 1,
                "skipped": 0,
                "warnings": [],
                "definition_hash": digest,
                "source_fingerprint": fingerprint,
                "source_file_count": len(manifest),
            }
        snapshot_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with self.database.session_factory.begin() as session:
            existing = session.scalar(
                select(BuiltinCatalogSnapshot).where(
                    BuiltinCatalogSnapshot.source_fingerprint == fingerprint
                )
            )
            if existing is not None:
                return {
                    "mode": "apply",
                    "created": 0,
                    "skipped": 1,
                    "warnings": [],
                    "catalog_snapshot_id": existing.id,
                    "source_fingerprint": fingerprint,
                }
            session.add(
                BuiltinCatalogSnapshot(
                    id=snapshot_id,
                    source_fingerprint=fingerprint,
                    definition_hash=digest,
                    definition_json=definition.model_dump(mode="json", exclude_none=False),
                    source_manifest_json={"schema_version": 1, "files": manifest},
                    created_at=now,
                )
            )
            session.add(
                LegacyImport(
                    id=str(uuid4()),
                    source_type="BUILTIN_CATALOG",
                    source_path=str(self.project_root),
                    source_fingerprint=fingerprint,
                    target_type="CATALOG_SNAPSHOT",
                    target_id=snapshot_id,
                    imported_at=now,
                )
            )
        return {
            "mode": "apply",
            "created": 1,
            "skipped": 0,
            "warnings": [],
            "catalog_snapshot_id": snapshot_id,
            "definition_hash": digest,
            "source_fingerprint": fingerprint,
            "source_file_count": len(manifest),
        }

    def discover_runs(self, source_root: str | Path | None = None) -> list[LegacyCandidate]:
        root = (
            Path(source_root).resolve()
            if source_root
            else self.project_root / "generative_agents" / "results"
        )
        checkpoints = root / "checkpoints"
        compressed = root / "compressed"
        names = {
            item.name for base in (checkpoints, compressed) if base.is_dir()
            for item in base.iterdir() if item.is_dir() and not item.is_symlink()
        }
        candidates = []
        for name in sorted(names, key=str.casefold):
            checkpoint_dir = checkpoints / name if (checkpoints / name).is_dir() else None
            compressed_dir = compressed / name if (compressed / name).is_dir() else None
            paths = [path for path in (checkpoint_dir, compressed_dir) if path]
            manifest = _tree_manifest(paths, root=root)
            candidates.append(
                LegacyCandidate(
                    name=name,
                    checkpoint_dir=checkpoint_dir,
                    compressed_dir=compressed_dir,
                    source_path=" | ".join(str(path.resolve()) for path in paths),
                    fingerprint=_manifest_fingerprint(manifest),
                )
            )
        return candidates

    def import_runs(
        self, *, apply: bool, source_root: str | Path | None = None
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "mode": "apply" if apply else "dry-run",
            "created": 0,
            "skipped": 0,
            "failed": 0,
            "warnings": [],
            "items": [],
        }
        for candidate in self.discover_runs(source_root):
            with self.database.session_factory() as session:
                existing = session.scalar(
                    select(LegacyImport).where(
                        LegacyImport.source_type == "LEGACY_RUN",
                        LegacyImport.source_path == candidate.source_path,
                        LegacyImport.source_fingerprint == candidate.fingerprint,
                    )
                )
            if existing is not None:
                result["skipped"] += 1
                result["items"].append(
                    {"name": candidate.name, "action": "skip", "run_id": existing.target_id}
                )
                continue
            if not apply:
                result["created"] += 1
                result["items"].append(
                    {
                        "name": candidate.name,
                        "action": "create",
                        "source_path": candidate.source_path,
                        "source_fingerprint": candidate.fingerprint,
                    }
                )
                continue
            try:
                item, warnings = self._import_candidate(candidate)
                result["created"] += 1
                result["items"].append(item)
                result["warnings"].extend(warnings)
            except Exception as exc:  # one source object must not poison the rest
                result["failed"] += 1
                result["items"].append(
                    {"name": candidate.name, "action": "failed", "error": str(exc)}
                )
        return result

    def _import_candidate(self, candidate: LegacyCandidate) -> tuple[dict[str, Any], list[str]]:
        checkpoints = self._checkpoint_samples(candidate.checkpoint_dir)
        movement_path = (
            candidate.compressed_dir / "movement.json"
            if candidate.compressed_dir and (candidate.compressed_dir / "movement.json").is_file()
            else None
        )
        movement = _json(movement_path) if movement_path else None
        if not checkpoints and movement is None:
            raise ValueError("legacy directory contains neither checkpoint JSON nor movement.json")
        definition, samples, warnings = self._legacy_definition_and_samples(
            candidate, checkpoints=checkpoints, movement=movement
        )
        run_id = str(uuid4())
        experiment_id = str(uuid4())
        revision_id = str(uuid4())
        attempt_id = str(uuid4())
        now = datetime.now(timezone.utc)
        first_time = samples[0][1]
        last_time = samples[-1][1]
        max_step = samples[-1][0]
        run_root = self.var_dir / "runs" / run_id
        artifact_root = run_root / "artifacts" / "legacy"
        artifact_root.mkdir(parents=True, exist_ok=True)
        copied_artifacts = self._copy_legacy_artifacts(
            candidate, run_id=run_id, artifact_root=artifact_root
        )
        log_path = run_root / "logs" / "legacy-import.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event": "legacy_import",
                    "source_path": candidate.source_path,
                    "source_fingerprint": candidate.fingerprint,
                    "snapshot_complete": False,
                    "imported_at": now.isoformat(),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        digest = definition_hash(definition)
        name_to_key = {agent.name: agent.agent_key for agent in definition.agents}
        summaries: dict[str, dict[str, Any]] = {}
        conversations = self._legacy_conversations(
            candidate, run_id=run_id, start=first_time, stride=definition.simulation.stride_minutes,
            name_to_key=name_to_key,
        )
        conversations_by_step: dict[int, list[dict[str, Any]]] = {}
        conversation_stats: dict[str, dict[str, int]] = {}
        for conversation in conversations:
            conversations_by_step.setdefault(conversation["step"], []).append(conversation)
            for agent_key in {conversation["initiator"], conversation["responder"]}:
                stats = conversation_stats.setdefault(
                    agent_key, {"conversation_count": 0, "message_count": 0}
                )
                stats["conversation_count"] += 1
                stats["message_count"] += len(conversation["messages"])
        capabilities = {
            "summary": {"state": "AVAILABLE", "reason": "LEGACY_PARTIAL"},
            "timeline": {"state": "PARTIAL", "reason": "RECONSTRUCTED_FROM_LEGACY"},
            "agents": {"state": "PARTIAL", "reason": "RECONSTRUCTED_FROM_LEGACY"},
            "conversations": {
                "state": "AVAILABLE" if conversations else "UNAVAILABLE",
                "reason": None if conversations else "LEGACY_STRUCTURED_CONVERSATION_MISSING",
            },
            "memories": {"state": "UNAVAILABLE", "reason": "LEGACY_MEMORY_HISTORY_INCOMPLETE"},
            "operations": {"state": "PARTIAL", "reason": "LEGACY_MODEL_TRACE_MISSING"},
        }
        with self.database.session_factory.begin() as session:
            experiment = Experiment(
                id=experiment_id,
                experiment_key=_safe_key(candidate.name, candidate.fingerprint),
                name=f"旧实验 · {candidate.name}",
                goal="由旧 checkpoint / compressed 目录导入；历史快照不完整。",
                status="COMPLETED",
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            revision = ExperimentRevision(
                id=revision_id,
                experiment_id=experiment_id,
                revision_no=1,
                state="PUBLISHED",
                schema_version=definition.schema_version,
                definition_json=definition.model_dump(mode="json", exclude_none=False),
                definition_hash=digest,
                validation_json={"valid": True, "errors": [], "warnings": warnings},
                validated_hash=digest,
                provenance_json={
                    "source_type": "LEGACY_RUN",
                    "source_path": candidate.source_path,
                    "source_fingerprint": candidate.fingerprint,
                    "snapshot_complete": False,
                },
                snapshot_complete=False,
                lock_version=1,
                created_at=now,
                updated_at=now,
                published_at=now,
            )
            run = Run(
                id=run_id,
                experiment_id=experiment_id,
                revision_id=revision_id,
                status="COMPLETED",
                start_step=0,
                requested_steps=max_step,
                completed_steps=max_step,
                recoverable_step=0,
                stride_minutes=definition.simulation.stride_minutes,
                virtual_time=last_time,
                run_dir=f"runs/{run_id}",
                created_at=now,
                started_at=first_time,
                finished_at=last_time,
            )
            session.add(experiment)
            session.flush()
            session.add(revision)
            session.flush()
            session.add(run)
            session.flush()
            experiment.current_published_revision_id = revision_id
            experiment.latest_run_id = run_id
            session.add(
                RunAttempt(
                    id=attempt_id,
                    run_id=run_id,
                    attempt_no=1,
                    status="ENDED",
                    slot_no=1,
                    log_path=log_path.relative_to(self.var_dir).as_posix(),
                    start_step=1,
                    end_step=max_step,
                    started_at=first_time,
                    ended_at=last_time,
                    exit_code=0,
                    stop_reason="LEGACY_IMPORT",
                )
            )
            action_count = 0
            for step_no, observed_at, agents, source_path, source_hash in samples:
                step_conversations = conversations_by_step.get(step_no, [])
                session.add(
                    RunStep(
                        run_id=run_id,
                        step_no=step_no,
                        attempt_id=attempt_id,
                        virtual_time=observed_at,
                        frame_path=str(source_path),
                        frame_sha256=source_hash,
                        action_count=len(agents),
                        movement_count=len(agents),
                        conversation_count=len(step_conversations),
                        message_count=sum(
                            len(conversation["messages"])
                            for conversation in step_conversations
                        ),
                        memory_created_count=0,
                        memory_accessed_count=0,
                        model_logical_calls=0,
                        model_retry_count=0,
                        active_agent_count=len(agents),
                        checkpoint=bool(candidate.checkpoint_dir),
                        committed_at=now,
                    )
                )
                session.flush()
                action_count += len(agents)
                for agent_name, fact in agents.items():
                    agent_key = name_to_key.get(agent_name)
                    if not agent_key:
                        continue
                    coord = fact.get("coord") or fact.get("movement") or [0, 0]
                    address = fact.get("address") or fact.get("location") or ""
                    if isinstance(address, list):
                        address = " / ".join(str(item) for item in address)
                    # `description` is the durable carried state in legacy
                    # deltas; `action` is a transient interpolation hint.
                    action = fact.get("description") or fact.get("action") or ""
                    emoji = fact.get("emoji")
                    session.add(
                        RunAgentStep(
                            run_id=run_id,
                            step_no=step_no,
                            agent_key=agent_key,
                            virtual_time=observed_at,
                            x=int(coord[0]),
                            y=int(coord[1]),
                            address=str(address),
                            action_text=str(action),
                            action_emoji=str(emoji) if emoji else None,
                            activity_kind="OTHER",
                            currently_text=fact.get("currently"),
                            path_source="RECONSTRUCTED",
                        )
                    )
                    item = summaries.setdefault(
                        agent_key,
                        {"count": 0, "coord": coord, "address": str(address), "currently": fact.get("currently")},
                    )
                    item.update({"coord": coord, "address": str(address), "currently": fact.get("currently")})
                    item["count"] += 1
            for agent_key, item in summaries.items():
                stats = conversation_stats.get(
                    agent_key, {"conversation_count": 0, "message_count": 0}
                )
                session.add(
                    RunAgentSummary(
                        run_id=run_id,
                        agent_key=agent_key,
                        x=int(item["coord"][0]),
                        y=int(item["coord"][1]),
                        address=item["address"],
                        currently_text=item["currently"],
                        action_count=item["count"],
                        movement_steps=item["count"],
                        conversation_count=stats["conversation_count"],
                        message_count=stats["message_count"],
                        memory_created_count=0,
                        rest_minutes=0,
                        chat_minutes=0,
                        moving_minutes=0,
                        other_minutes=item["count"] * definition.simulation.stride_minutes,
                        updated_step=max_step,
                    )
                )
            message_count = self._persist_conversations(session, conversations)
            session.add(
                RunResultSummary(
                    run_id=run_id,
                    available_step=max_step,
                    virtual_time=last_time,
                    action_count=action_count,
                    conversation_count=len(conversations),
                    message_count=message_count,
                    memory_count=0,
                    model_call_count=0,
                    model_retry_count=0,
                    result_state="PARTIAL",
                    capabilities_json=capabilities,
                    projection_version="legacy-v1",
                    result_version=1,
                    updated_at=now,
                )
            )
            session.add_all(copied_artifacts)
            session.add(
                RunEvent(
                    run_id=run_id,
                    event_type="legacy_import",
                    payload_json={"status": "COMPLETED", "snapshot_complete": False},
                    created_at=now,
                )
            )
            session.add(
                LegacyImport(
                    id=str(uuid4()),
                    source_type="LEGACY_RUN",
                    source_path=candidate.source_path,
                    source_fingerprint=candidate.fingerprint,
                    target_type="RUN",
                    target_id=run_id,
                    imported_at=now,
                )
            )
        return (
            {
                "name": candidate.name,
                "action": "create",
                "experiment_id": experiment_id,
                "run_id": run_id,
                "available_step": max_step,
                "artifact_count": len(copied_artifacts),
            },
            warnings,
        )

    def _checkpoint_samples(self, checkpoint_dir: Path | None) -> list[tuple[Path, dict[str, Any]]]:
        if checkpoint_dir is None:
            return []
        rows = []
        for path in sorted(checkpoint_dir.glob("simulate-*.json"), key=lambda item: item.name):
            document = _json(path)
            if "step" in document and "agents" in document and "time" in document:
                rows.append((path, document))
        return rows

    def _legacy_definition_and_samples(self, candidate, *, checkpoints, movement):
        warnings = [
            "旧运行缺少当时完整 Prompt/资源哈希/解析后模型信息；不可声明完全可复现。"
        ]
        base = make_builtin_definition(
            key=_safe_key(candidate.name, candidate.fingerprint),
            name=f"旧实验 · {candidate.name}",
            goal="历史运行导入",
        ).model_dump(mode="json", exclude_none=False)
        samples = []
        if checkpoints:
            first = checkpoints[0][1]
            last_step = max(int(document["step"]) for _, document in checkpoints)
            base["simulation"].update(
                {
                    "start_time": _legacy_time(first["time"]).isoformat(),
                    "stride_minutes": int(first.get("stride", 10)),
                    "max_steps": last_step,
                    "checkpoint_interval_steps": 1,
                }
            )
            self._overlay_legacy_config(base, first.get("agent_base") or {})
            present = set(first.get("agents") or {})
            for agent in base["agents"]:
                agent["enabled"] = agent["name"] in present
            for path, document in checkpoints:
                agents = {}
                for name, value in (document.get("agents") or {}).items():
                    event = ((value.get("action") or {}).get("event") or {})
                    agents[name] = {
                        "coord": value.get("coord") or [0, 0],
                        "address": event.get("address") or [],
                        "action": event.get("describe") or event.get("object") or "",
                        "emoji": event.get("emoji"),
                        "currently": value.get("currently"),
                    }
                samples.append(
                    (int(document["step"]), _legacy_time(document["time"]), agents, path, _sha256_file(path))
                )
        else:
            start = _legacy_time(movement["start_datetime"])
            stride = int(movement.get("stride", 10))
            all_movement = movement.get("all_movement") or {}
            numeric = sorted(int(key) for key in all_movement if str(key).isdigit())
            frame_stride = int(movement.get("replay_interpolation_frames") or 60)
            selected = numeric[::frame_stride]
            if numeric and selected[-1] != numeric[-1]:
                selected.append(numeric[-1])
            base["simulation"].update(
                {
                    "start_time": start.isoformat(),
                    "stride_minutes": stride,
                    "max_steps": max(1, len(selected)),
                    "checkpoint_interval_steps": max(1, len(selected)),
                }
            )
            descriptions = all_movement.get("description") or {}
            present = set(descriptions) or set(movement.get("persona_init_pos") or {})
            for agent in base["agents"]:
                agent["enabled"] = agent["name"] in present
                if agent["name"] in descriptions:
                    agent["currently"] = descriptions[agent["name"]].get("currently", agent["currently"])
                    agent["scratch"].update(descriptions[agent["name"]].get("scratch") or {})
            source_hash = hashlib.sha256(
                json.dumps(movement, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            # Legacy movement frames are sparse deltas, not snapshots.  Fold
            # every interpolation frame in order and only then sample the
            # requested boundary so frame 60 carries unchanged frame 0 facts.
            carried: dict[str, dict[str, Any]] = {
                name: {
                    "movement": list(coord),
                    "location": "",
                    "description": "",
                }
                for name, coord in (movement.get("persona_init_pos") or {}).items()
            }
            selected_set = set(selected)
            selected_index = {frame_no: index for index, frame_no in enumerate(selected, start=1)}
            for frame_no in numeric:
                delta = all_movement.get(str(frame_no)) or {}
                if isinstance(delta, dict):
                    for name, fact in delta.items():
                        if isinstance(fact, dict):
                            carried[name] = {**carried.get(name, {}), **fact}
                if frame_no not in selected_set:
                    continue
                step_no = selected_index[frame_no]
                samples.append(
                    (
                        step_no,
                        start + timedelta(minutes=(step_no - 1) * stride),
                        {name: dict(fact) for name, fact in carried.items()},
                        candidate.compressed_dir / "movement.json",
                        source_hash,
                    )
                )
            warnings.append("movement.json 路径按 60 个插值帧采样为模拟步骤，path_source=RECONSTRUCTED。")
        return ExperimentDefinition.model_validate(base), samples, warnings

    @staticmethod
    def _overlay_legacy_config(base: dict[str, Any], config: dict[str, Any]) -> None:
        percept = config.get("percept") or {}
        schedule = config.get("schedule") or {}
        think = config.get("think") or {}
        associate = config.get("associate") or {}
        base["behavior"]["percept"].update(
            {
                "mode": percept.get("mode", base["behavior"]["percept"]["mode"]),
                "vision_radius": percept.get("vision_r", base["behavior"]["percept"]["vision_radius"]),
                "attention_bandwidth": percept.get("att_bandwidth", base["behavior"]["percept"]["attention_bandwidth"]),
            }
        )
        base["behavior"]["schedule"].update(
            {key: schedule[key] for key in ("max_try", "diversity") if key in schedule}
        )
        if "poignancy_max" in think:
            base["behavior"]["think"]["poignancy_max"] = think["poignancy_max"]
        if "chat_iter" in config:
            base["behavior"]["chat"]["max_iterations"] = config["chat_iter"]
        if "retention" in associate:
            base["behavior"]["memory"]["retention"] = associate["retention"]
        llm = think.get("llm") or {}
        if llm.get("provider") == "vllm" and llm.get("base_url"):
            base["models"]["chat"].update(
                {
                    "provider": "vllm",
                    "model": llm.get("model", "auto"),
                    "resolved_model": None,
                    "base_url": llm["base_url"],
                    "timeout_seconds": llm.get("timeout", 300),
                    "max_tokens": llm.get("max_tokens", 2048),
                    "enable_thinking": llm.get("enable_thinking", False),
                    "secret_ref": None,
                }
            )
        embedding = associate.get("embedding") or {}
        if embedding.get("provider") == "openai_compatible" and embedding.get("base_url"):
            base["models"]["embedding"].update(
                {
                    "provider": "openai_compatible",
                    "model": embedding.get("model", "auto"),
                    "resolved_model": None,
                    "base_url": embedding["base_url"],
                    "timeout_seconds": embedding.get("timeout", 120),
                    "transport_retry_attempts": embedding.get("max_retries", 3),
                    "secret_ref": None,
                }
            )

    def _copy_legacy_artifacts(self, candidate, *, run_id: str, artifact_root: Path):
        paths: list[tuple[str, Path]] = []
        if candidate.checkpoint_dir:
            paths.extend(
                ("checkpoints", path)
                for path in sorted(
                    candidate.checkpoint_dir.glob("*.json"), key=lambda item: item.name
                )
            )
        if candidate.compressed_dir:
            paths.extend(
                ("compressed", path)
                for path in sorted(
                    candidate.compressed_dir.glob("*"), key=lambda item: item.name
                )
            )
        artifacts = []
        source_files = (
            (group, path)
            for group, path in paths
            if path.is_file() and not path.is_symlink()
        )
        for index, (group, source) in enumerate(source_files):
            digest = _sha256_file(source)
            suffix = source.suffix.lower()
            target = artifact_root / f"{index:04d}-{digest[:16]}{suffix}"
            shutil.copy2(source, target)
            media_type = "application/json" if suffix == ".json" else "text/markdown" if suffix == ".md" else "application/octet-stream"
            artifacts.append(
                RunArtifact(
                    id=str(uuid4()),
                    run_id=run_id,
                    artifact_type="LEGACY_SOURCE",
                    logical_name=f"{group}/{source.name}",
                    media_type=media_type,
                    relative_path=target.relative_to(self.var_dir).as_posix(),
                    size_bytes=target.stat().st_size,
                    sha256=digest,
                    source_kind="RAW",
                    generator_version="legacy-v1",
                    state="READY",
                    created_at=datetime.now(timezone.utc),
                )
            )
        return artifacts

    def _legacy_conversations(self, candidate, *, run_id, start, stride, name_to_key):
        path = candidate.checkpoint_dir / "conversation.json" if candidate.checkpoint_dir else None
        if path is None or not path.is_file():
            return self._compressed_conversations(
                candidate,
                run_id=run_id,
                start=start,
                stride=stride,
                name_to_key=name_to_key,
            )
        document = _json(path)
        rows = []
        for time_key, groups in sorted(document.items()):
            observed = _legacy_time(time_key)
            step = max(1, int((observed - start).total_seconds() // (stride * 60)) + 1)
            for group_index, group in enumerate(groups if isinstance(groups, list) else []):
                if not isinstance(group, dict):
                    continue
                for header, messages in group.items():
                    pair, _, location = header.partition(" @ ")
                    initiator_name, separator, responder_name = pair.partition(" -> ")
                    if not separator:
                        continue
                    initiator = name_to_key.get(initiator_name.strip())
                    responder = name_to_key.get(responder_name.strip())
                    if not initiator or not responder or not isinstance(messages, list):
                        continue
                    conversation_id = str(uuid5(UUID(run_id), f"legacy-conversation:{time_key}:{group_index}:{header}"))
                    rows.append(
                        {
                            "id": conversation_id,
                            "run_id": run_id,
                            "step": step,
                            "time": observed,
                            "location": location,
                            "initiator": initiator,
                            "responder": responder,
                            "messages": [
                                (name_to_key.get(str(message[0])), str(message[1]))
                                for message in messages
                                if isinstance(message, list) and len(message) >= 2 and name_to_key.get(str(message[0]))
                            ],
                        }
                    )
        return rows

    @staticmethod
    def _compressed_conversations(candidate, *, run_id, start, stride, name_to_key):
        movement_path = (
            candidate.compressed_dir / "movement.json"
            if candidate.compressed_dir
            else None
        )
        if movement_path is None or not movement_path.is_file():
            return []
        movement = _json(movement_path)
        documents = (movement.get("all_movement") or {}).get("conversation") or {}
        rows = []
        for time_key, transcript in sorted(documents.items()):
            if not isinstance(transcript, str) or not transcript.strip():
                continue
            observed = _legacy_time(time_key)
            step = max(1, int((observed - start).total_seconds() // (stride * 60)) + 1)
            blocks = re.split(r"(?:^|\n)地点[：:]\s*", transcript.strip())
            for block_index, block in enumerate(item for item in blocks if item.strip()):
                lines = [line.strip() for line in block.splitlines() if line.strip()]
                if len(lines) < 3:
                    continue
                location = lines[0]
                messages = []
                participants = []
                for line in lines[1:]:
                    speaker_name, separator, content = line.partition("：")
                    if not separator:
                        speaker_name, separator, content = line.partition(":")
                    speaker = name_to_key.get(speaker_name.strip())
                    if not separator or not speaker or not content.strip():
                        continue
                    if speaker not in participants:
                        participants.append(speaker)
                    messages.append((speaker, content.strip()))
                if len(participants) < 2 or not messages:
                    continue
                conversation_id = str(
                    uuid5(
                        UUID(run_id),
                        f"legacy-compressed-conversation:{time_key}:{block_index}:{location}",
                    )
                )
                rows.append(
                    {
                        "id": conversation_id,
                        "run_id": run_id,
                        "step": step,
                        "time": observed,
                        "location": location,
                        "initiator": participants[0],
                        "responder": participants[1],
                        "messages": messages,
                    }
                )
        return rows

    @staticmethod
    def _persist_conversations(session, rows) -> int:
        total = 0
        for row in rows:
            messages = row["messages"]
            session.add(
                RunConversation(
                    id=row["id"],
                    run_id=row["run_id"],
                    start_step=row["step"],
                    end_step=row["step"],
                    started_at=row["time"],
                    ended_at=row["time"] + timedelta(minutes=1),
                    duration_minutes=1,
                    duration_source="INFERRED",
                    location=row["location"],
                    initiator_agent_key=row["initiator"],
                    responder_agent_key=row["responder"],
                    message_count=len(messages),
                    summary=None,
                    ended_reason="LEGACY_IMPORT",
                )
            )
            session.flush()
            for agent_key in {row["initiator"], row["responder"]}:
                session.add(
                    RunConversationParticipant(
                        run_id=row["run_id"], conversation_id=row["id"], agent_key=agent_key
                    )
                )
            for sequence, (speaker, content) in enumerate(messages, start=1):
                session.add(
                    RunMessage(
                        id=str(uuid5(UUID(row["run_id"]), f"legacy-message:{row['id']}:{sequence}")),
                        run_id=row["run_id"],
                        conversation_id=row["id"],
                        sequence_no=sequence,
                        speaker_agent_key=speaker,
                        content=content,
                        observed_at=row["time"],
                        source_step=row["step"],
                    )
                )
            total += len(messages)
        return total
