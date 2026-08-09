"""Validated checkpoint inventory, safe details and bounded previews."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import Run, RunStep
from generative_agents.runtime.checkpoint import CheckpointBundleWriter, CheckpointSnapshot
from generative_agents.runtime.context import RunPaths
from generative_agents.runtime.model_trace import redact_error

from .byte_windows import read_utf8_window
from .errors import ServiceError, not_found
from .run_storage import RunStorageBoundary


_CHECKPOINT_NAME = re.compile(r"^step-([0-9]{6})$")
_RESUMABLE_RUN_STATES = frozenset({"PAUSED", "FAILED", "INTERRUPTED"})
_PREVIEW_FILES = {
    "bundle": "bundle.json",
    "state": "state.json",
    "conversation": "conversation.json",
}


class CheckpointService:
    def __init__(self, database: Database, *, var_dir: str | Path):
        self._database = database
        self._boundary = RunStorageBoundary(var_dir)

    def list_checkpoints(self, run_id: str) -> dict[str, Any]:
        run, paths, database_markers = self._context(run_id)
        physical: dict[int, Path] = {}
        if paths.checkpoints.exists():
            for candidate in paths.checkpoints.iterdir():
                match = _CHECKPOINT_NAME.fullmatch(candidate.name)
                if match and (candidate.is_dir() or candidate.is_symlink()):
                    physical[int(match.group(1))] = candidate
        steps = set(database_markers) | set(physical)
        if run.recoverable_step > 0:
            steps.add(run.recoverable_step)
        items = [
            self._item(
                run,
                paths,
                step_no,
                database_marker=database_markers.get(step_no),
                physical_path=physical.get(step_no),
            )
            for step_no in sorted(steps, reverse=True)
        ]
        can_resume = any(item["resumable"] for item in items)
        return {
            "run_id": run.id,
            "run_status": run.status,
            "recoverable_step": run.recoverable_step,
            "can_resume": can_resume,
            "items": items,
        }

    def detail(self, run_id: str, step_no: int) -> dict[str, Any]:
        run, paths, database_markers = self._context(run_id)
        item = self._item(
            run,
            paths,
            step_no,
            database_marker=database_markers.get(step_no),
            physical_path=paths.checkpoints / f"step-{step_no:06d}",
        )
        if item["status"] == "NOT_FOUND":
            raise not_found("checkpoint", f"{run_id}:{step_no}")
        if item["status"] == "PRUNED":
            raise ServiceError(
                "CHECKPOINT_PRUNED",
                "检查点已按保留策略清理",
                status_code=410,
                details={"step_no": step_no},
            )
        if not item["validated"]:
            raise ServiceError(
                "CHECKPOINT_INVALID",
                "检查点完整性校验失败",
                status_code=409,
                details={"step_no": step_no, "validation": item["validation"]},
            )
        checkpoint = paths.checkpoints / f"step-{step_no:06d}"
        bundle = self._read_json(checkpoint / "bundle.json")
        state = self._read_json(checkpoint / "state.json")
        conversation = self._read_json(checkpoint / "conversation.json")
        agents = state.get("agents", {}) if isinstance(state, dict) else {}
        if not isinstance(agents, dict):
            agents = {}
        agent_items = [
            self._agent_summary(str(agent_key), value)
            for agent_key, value in sorted(agents.items(), key=lambda pair: str(pair[0]))
        ]
        conversation_items = self._conversation_items(conversation)
        files = [self._file_summary(entry) for entry in bundle.get("files", [])]
        storage = self._storage_summary(files)
        return {
            **item,
            "run_id": run.id,
            "bundle": {
                "schema_version": bundle.get("bundle_schema_version"),
                "run_id": bundle.get("run_id"),
                "attempt_id": bundle.get("attempt_id"),
                "step_no": bundle.get("step_no"),
                "virtual_time": bundle.get("virtual_time"),
                "frame_sha256": bundle.get("frame_sha256"),
                "bundle_sha256": item["bundle_sha256"],
            },
            "agent_state": {
                "count": len(agent_items),
                "items": agent_items[:200],
                "truncated": len(agent_items) > 200,
            },
            "conversations": {
                "count": len(conversation_items),
                "items": conversation_items[:100],
                "truncated": len(conversation_items) > 100,
            },
            "storage": storage,
            "files": files,
            "preview_sections": sorted(_PREVIEW_FILES),
        }

    def preview(
        self,
        run_id: str,
        step_no: int,
        section: str,
        *,
        cursor: int = 0,
        limit_bytes: int = 32_768,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        if section not in _PREVIEW_FILES:
            raise ServiceError(
                "CHECKPOINT_PREVIEW_SECTION_INVALID",
                "检查点预览分区不受支持",
                status_code=422,
                details={"allowed": sorted(_PREVIEW_FILES)},
            )
        # Detail performs a fresh full bundle validation before any member is
        # opened, preventing previews of undeclared or modified JSON files.
        self.detail(run_id, step_no)
        _run, paths, _markers = self._context(run_id)
        target = paths.checkpoints / f"step-{step_no:06d}" / _PREVIEW_FILES[section]
        window = read_utf8_window(
            target,
            cursor=cursor,
            limit_bytes=limit_bytes,
            expected_file_id=file_id,
            missing_code="CHECKPOINT_MEMBER_MISSING",
            truncated_code="CHECKPOINT_MEMBER_TRUNCATED",
            rotated_code="CHECKPOINT_MEMBER_CHANGED",
            encoding_code="CHECKPOINT_MEMBER_ENCODING_INVALID",
        )
        return {
            "run_id": run_id,
            "step_no": step_no,
            "section": section,
            "cursor": window.start_cursor,
            "next_cursor": None if window.eof else window.next_cursor,
            "content": window.content,
            "size_bytes": window.size_bytes,
            "file_id": window.file_id,
            "eof": window.eof,
        }

    def validate_for_export(self, run_id: str, step_no: int) -> dict[str, Any]:
        detail = self.detail(run_id, step_no)
        if not detail["validated"]:
            raise ServiceError(
                "CHECKPOINT_INVALID", "检查点完整性校验失败", status_code=409
            )
        return detail

    def _context(self, run_id: str) -> tuple[Run, RunPaths, dict[int, RunStep]]:
        try:
            parsed_run_id = UUID(run_id)
        except ValueError as exc:
            raise not_found("run", run_id) from exc
        with self._database.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise not_found("run", run_id)
            markers = {
                row.step_no: row
                for row in session.scalars(
                    select(RunStep).where(
                        RunStep.run_id == run_id, RunStep.checkpoint.is_(True)
                    )
                )
            }
            root = self._boundary.run_root(run)
            session.expunge(run)
            for row in markers.values():
                session.expunge(row)
        return run, RunPaths(root=root, run_id=parsed_run_id), markers

    def _item(
        self,
        run: Run,
        paths: RunPaths,
        step_no: int,
        *,
        database_marker: RunStep | None,
        physical_path: Path | None,
    ) -> dict[str, Any]:
        base = {
            "step_no": step_no,
            "database_marker": database_marker is not None,
            "retained": False,
            "validated": False,
            "status": "PRUNED",
            "attempt_id": database_marker.attempt_id if database_marker else None,
            "virtual_time": (
                database_marker.virtual_time.isoformat() if database_marker else None
            ),
            "bundle_sha256": None,
            "size_bytes": 0,
            "file_count": 0,
            "resumable": False,
            "validation": None,
        }
        if physical_path is None:
            if step_no == run.recoverable_step:
                base["status"] = "INVALID"
                base["validation"] = {
                    "code": "CHECKPOINT_AUTHORIZED_BUNDLE_MISSING",
                    "reason": "database-authorized recovery checkpoint is not retained",
                }
            elif database_marker is None:
                base["status"] = "NOT_FOUND"
            return base
        if physical_path.is_symlink():
            base["retained"] = True
            base["status"] = "INVALID"
            base["validation"] = {
                "code": "CHECKPOINT_SYMLINK_REJECTED",
                "reason": "checkpoint directory must not be a symbolic link",
            }
            return base
        if not physical_path.exists():
            if step_no == run.recoverable_step:
                base["status"] = "INVALID"
                base["validation"] = {
                    "code": "CHECKPOINT_AUTHORIZED_BUNDLE_MISSING",
                    "reason": "database-authorized recovery checkpoint is not retained",
                }
            elif database_marker is None:
                base["status"] = "NOT_FOUND"
            return base
        base["retained"] = True
        reader = CheckpointBundleWriter(
            paths, lambda _: CheckpointSnapshot(state={}, conversation={})
        )
        try:
            stored = reader.validate(physical_path)
            bundle = self._read_json(physical_path / "bundle.json")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            base["status"] = "INVALID"
            base["validation"] = {
                "code": "CHECKPOINT_VALIDATION_FAILED",
                "reason": self._validation_reason(exc, paths.root),
            }
            return base
        files = bundle.get("files", [])
        base.update(
            {
                "validated": True,
                "status": (
                    "RECOVERABLE"
                    if step_no == run.recoverable_step
                    else "RETAINED"
                ),
                "attempt_id": bundle.get("attempt_id"),
                "virtual_time": bundle.get("virtual_time"),
                "bundle_sha256": stored.bundle_sha256,
                "size_bytes": sum(
                    int(item.get("size", 0)) for item in files if isinstance(item, dict)
                )
                + (physical_path / "bundle.json").stat().st_size,
                "file_count": len(files) + 1,
                "resumable": (
                    step_no == run.recoverable_step
                    and run.status in _RESUMABLE_RUN_STATES
                ),
                "validation": {"code": "VALID", "reason": None},
            }
        )
        return base

    @staticmethod
    def _read_json(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @classmethod
    def _agent_summary(cls, agent_key: str, value: Any) -> dict[str, Any]:
        state = value if isinstance(value, dict) else {}
        action = state.get("action") if isinstance(state.get("action"), dict) else {}
        schedule = state.get("schedule") or state.get("daily_schedule") or []
        return {
            "agent_key": agent_key,
            "coord": cls._safe_value(state.get("coord")),
            "currently": cls._safe_value(state.get("currently")),
            "action": cls._safe_value(
                {
                    key: action.get(key)
                    for key in ("event", "address", "emoji", "start_time", "duration")
                    if key in action
                }
            ),
            "schedule_item_count": len(schedule) if isinstance(schedule, list) else 0,
        }

    @classmethod
    def _conversation_items(cls, value: Any) -> list[Any]:
        if isinstance(value, list):
            items = value
        elif isinstance(value, dict):
            candidate = value.get("items", value.get("conversations", []))
            items = candidate if isinstance(candidate, list) else []
        else:
            items = []
        return [cls._safe_value(item) for item in items]

    @staticmethod
    def _file_summary(value: Any) -> dict[str, Any]:
        item = value if isinstance(value, dict) else {}
        return {
            "path": str(item.get("path", "")),
            "size_bytes": int(item.get("size", 0)),
            "sha256": item.get("sha256"),
        }

    @staticmethod
    def _storage_summary(files: list[dict[str, Any]]) -> dict[str, Any]:
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for item in files:
            parts = Path(item["path"]).parts
            if len(parts) < 3 or parts[0] != "storage":
                continue
            key = (parts[1], parts[2])
            group = groups.setdefault(
                key,
                {
                    "agent_key": parts[1],
                    "index_type": parts[2],
                    "file_count": 0,
                    "size_bytes": 0,
                },
            )
            group["file_count"] += 1
            group["size_bytes"] += item["size_bytes"]
        return {"groups": list(groups.values()), "group_count": len(groups)}

    @classmethod
    def _safe_value(cls, value: Any, *, depth: int = 0) -> Any:
        if depth > 4:
            return "[TRUNCATED]"
        if isinstance(value, dict):
            output = {}
            for key, item in list(value.items())[:100]:
                folded = str(key).casefold()
                if any(token in folded for token in ("embedding", "vector", "api_key", "secret", "token")):
                    continue
                output[str(key)] = cls._safe_value(item, depth=depth + 1)
            return output
        if isinstance(value, list):
            return [cls._safe_value(item, depth=depth + 1) for item in value[:100]]
        if isinstance(value, str):
            return redact_error(value[:2_000])
        if value is None or isinstance(value, (int, float, bool)):
            return value
        return str(value)[:2_000]

    @staticmethod
    def _validation_reason(exc: Exception, run_root: Path) -> str:
        message = str(exc).replace(str(run_root), "[run]")
        return redact_error(f"{type(exc).__name__}: {message}")[:1_000]
