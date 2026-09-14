"""Database-free, Run-owned memory stream stored as canonical JSON files."""

from __future__ import annotations

import copy
import hashlib
import re
import shutil
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from filelock import FileLock

from generative_agents.ga_protocol import atomic_write_json, read_json


class FileMemoryStream:
    """Agent-isolated memory with history, checkpoint export, and no database.

    The canonical state is ``memories.json``.  Atomic replacement keeps every
    public operation crash-safe; the Run's immutable StepResult remains the
    audit source for committed behavior.
    """

    def __init__(
        self,
        storage_root: str | Path,
        *,
        run_id: str | UUID,
        attempt_id: str | UUID,
        clock=None,
        logger=None,
    ) -> None:
        self.root = Path(storage_root).resolve()
        self.path = self.root / "memories.json"
        self.lock = FileLock(str(self.root / "memory.lock"), timeout=-1)
        self.run_id = str(run_id)
        self.attempt_id = str(attempt_id)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._logger = logger
        self._step_no = 0
        self._virtual_time: datetime | None = None
        self._pending_events: list[dict] = []
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            atomic_write_json(self.path, {"schema_version": 1, "run_id": self.run_id, "items": []})
        self._load()

    def _load(self) -> list[dict]:
        document = read_json(self.path)
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported file memory schema")
        if document.get("run_id") != self.run_id:
            raise ValueError("file memory belongs to another Run")
        items = document.get("items")
        if not isinstance(items, list):
            raise ValueError("file memory items must be an array")
        return copy.deepcopy(items)

    def _save(self, items: list[dict]) -> None:
        atomic_write_json(
            self.path,
            {"schema_version": 1, "run_id": self.run_id, "items": items},
        )

    def begin_step(self, step_no: int, virtual_time: datetime) -> None:
        if step_no < 1 or virtual_time.tzinfo is None:
            raise ValueError("memory step must be positive and timezone-aware")
        self._step_no = int(step_no)
        self._virtual_time = virtual_time

    def _scope(self) -> tuple[int, datetime]:
        if self._step_no < 1 or self._virtual_time is None:
            raise RuntimeError("memory stream must be bound to a simulation step")
        return self._step_no, self._virtual_time

    def begin_iteration(self, agent_key: str) -> dict:
        agent_key = str(agent_key).strip()
        if not agent_key:
            raise ValueError("agent_key must not be empty")
        with self.lock:
            selected = [item for item in self._load() if item["agent_key"] == agent_key]
        return {
            "run_id": self.run_id,
            "agent_key": agent_key,
            "items": selected,
            "pending_events": copy.deepcopy(self._pending_events),
        }

    def rollback_iteration(self, snapshot: dict) -> None:
        if str(snapshot.get("run_id")) != self.run_id:
            raise ValueError("memory snapshot belongs to another Run")
        agent_key = str(snapshot.get("agent_key") or "").strip()
        replacement = copy.deepcopy(snapshot.get("items") or [])
        with self.lock:
            items = [item for item in self._load() if item["agent_key"] != agent_key]
            items.extend(replacement)
            self._save(items)
        self._pending_events = copy.deepcopy(snapshot.get("pending_events") or [])

    def append(
        self,
        *,
        agent_key: str,
        content: str,
        kind: str = "event",
        poignancy: int = 1,
        memory_id: str | None = None,
        expires_at: datetime | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        object: str | None = None,
        address=(),
        evidence_memory_ids=(),
        emit_event: bool = True,
        _supersedes_memory_id: str | None = None,
        **_ignored,
    ) -> dict:
        agent_key = str(agent_key).strip()
        content = str(content).strip()
        if not agent_key or not content:
            raise ValueError("agent_key and content are required")
        if not 1 <= int(poignancy) <= 10:
            raise ValueError("poignancy must be between 1 and 10")
        step_no, now = self._scope()
        with self.lock:
            items = self._load()
            sequence = 1 + sum(
                item["agent_key"] == agent_key and item["created_step"] == step_no
                for item in items
            )
            stable = f"{step_no}:{agent_key}:{sequence}:{kind}:{hashlib.sha256(content.encode()).hexdigest()}"
            item_id = str(memory_id or uuid5(uuid5(NAMESPACE_URL, self.run_id), stable))
            existing = next((item for item in items if item["id"] == item_id), None)
            if existing is not None:
                if existing["content"] != content or existing["kind"] != kind:
                    raise ValueError("memory_id already exists with different content")
                return self._public(existing)
            item = {
                "id": item_id,
                "agent_key": agent_key,
                "content": content,
                "kind": str(kind).strip() or "event",
                "poignancy": int(poignancy),
                "state": "ACTIVE",
                "created_step": step_no,
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "last_accessed_step": step_no,
                "last_accessed_at": now.isoformat(),
                "removed_step": None,
                "removed_at": None,
                "subject": subject,
                "predicate": predicate,
                "object": object,
                "address": list(address),
                "evidence_memory_ids": list(evidence_memory_ids),
                "created_attempt_id": self.attempt_id,
                "supersedes_memory_id": _supersedes_memory_id,
                "superseded_by_memory_id": None,
                "invalidated_reason": None,
            }
            items.append(item)
            self._save(items)
        if emit_event:
            self._pending_events.append(self._event("CREATED", item))
        return self._public(item)

    def search(self, *, agent_key: str, query: str = "", limit: int = 8, emit_event: bool = True) -> list[dict]:
        step_no, now = self._scope()
        query_tokens = self._tokens(query)
        with self.lock:
            items = [
                item for item in self._load()
                if item["agent_key"] == agent_key.strip() and item["state"] == "ACTIVE"
            ]
            for item in items:
                document_tokens = self._tokens(
                    " ".join(str(item.get(key) or "") for key in ("content", "subject", "predicate", "object"))
                )
                item["_score"] = (
                    len(query_tokens & document_tokens) / max(1, len(query_tokens))
                    if query_tokens else 1.0
                )
            items = [item for item in items if item["_score"] > 0]
            items.sort(key=lambda item: (item["_score"], item["poignancy"], item["created_at"]), reverse=True)
            selected = items[: max(1, min(int(limit), 100))]
            all_items = self._load()
            selected_ids = {item["id"] for item in selected}
            for item in all_items:
                if item["id"] in selected_ids:
                    item["last_accessed_step"] = step_no
                    item["last_accessed_at"] = now.isoformat()
            if selected:
                self._save(all_items)
        output = []
        for item in selected:
            public = self._public(item)
            public["retrieval_score"] = round(float(item["_score"]), 6)
            public["retrieval_method"] = "file_lexical"
            output.append(public)
            if emit_event:
                self._pending_events.append(self._event("ACCESSED", item))
        return output

    def access(self, memory_id: str, *, emit_event: bool = True) -> None:
        step_no, now = self._scope()
        with self.lock:
            items = self._load()
            item = next((value for value in items if value["id"] == memory_id and value["state"] == "ACTIVE"), None)
            if item is None:
                raise ValueError(f"active memory does not exist: {memory_id}")
            item["last_accessed_step"] = step_no
            item["last_accessed_at"] = now.isoformat()
            self._save(items)
        if emit_event:
            self._pending_events.append(self._event("ACCESSED", item))

    def remove(
        self,
        memory_id: str,
        *,
        state,
        agent_key: str | None = None,
        replacement_memory_id: str | None = None,
        reason: str | None = None,
        emit_event: bool = True,
        **_ignored,
    ) -> None:
        state_value = getattr(state, "value", state)
        if state_value not in {"EXPIRED", "EVICTED", "SUPERSEDED", "INVALIDATED"}:
            raise ValueError("unsupported removed memory state")
        if state_value == "SUPERSEDED" and not replacement_memory_id:
            raise ValueError("SUPERSEDED requires replacement_memory_id")
        if state_value == "INVALIDATED" and not str(reason or "").strip():
            raise ValueError("INVALIDATED requires a reason")
        step_no, now = self._scope()
        with self.lock:
            items = self._load()
            item = next(
                (
                    value for value in items
                    if value["id"] == memory_id
                    and value["state"] == "ACTIVE"
                    and (agent_key is None or value["agent_key"] == agent_key)
                ),
                None,
            )
            if item is None:
                raise ValueError(f"active memory does not exist: {memory_id}")
            item["state"] = state_value
            item["removed_step"] = step_no
            item["removed_at"] = now.isoformat()
            item["superseded_by_memory_id"] = replacement_memory_id
            item["invalidated_reason"] = str(reason or "").strip() or None
            self._save(items)
        if emit_event:
            self._pending_events.append(
                self._event(state_value, item, replacement_memory_id=replacement_memory_id, reason=reason)
            )

    def supersede(self, *, agent_key: str, memory_id: str, content: str, reason: str | None = None) -> dict:
        with self.lock:
            original = next(
                (
                    item for item in self._load()
                    if item["id"] == memory_id and item["agent_key"] == agent_key and item["state"] == "ACTIVE"
                ),
                None,
            )
        if original is None:
            raise ValueError(f"active memory does not exist: {memory_id}")
        evidence = list(original.get("evidence_memory_ids") or [])
        if memory_id not in evidence:
            evidence.append(memory_id)
        replacement = self.append(
            agent_key=agent_key,
            content=content,
            kind=original["kind"],
            poignancy=original["poignancy"],
            expires_at=datetime.fromisoformat(original["expires_at"]) if original.get("expires_at") else None,
            subject=original.get("subject"),
            predicate=original.get("predicate"),
            object=original.get("object"),
            address=original.get("address") or (),
            evidence_memory_ids=evidence,
            emit_event=False,
            _supersedes_memory_id=memory_id,
        )
        self.remove(
            memory_id,
            state="SUPERSEDED",
            agent_key=agent_key,
            replacement_memory_id=replacement["id"],
            reason=reason,
            emit_event=False,
        )
        self._pending_events.append(
            self._event("SUPERSEDED", original, replacement_memory_id=replacement["id"], reason=reason)
        )
        replacement_item = dict(replacement)
        self._pending_events.append(self._event("CREATED", replacement_item))
        return {"superseded_memory_id": memory_id, "replacement": replacement}

    def invalidate(self, *, agent_key: str, memory_id: str, reason: str) -> dict:
        self.remove(memory_id, state="INVALIDATED", agent_key=agent_key, reason=reason)
        return {"memory_id": memory_id, "state": "INVALIDATED", "reason": reason.strip()}

    def drain_result_events(self) -> tuple[dict, ...]:
        events, self._pending_events = tuple(self._pending_events), []
        return events

    def export_storage(self, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        with self.lock:
            shutil.copyfile(self.path, target / "memories.json")

    @staticmethod
    def _public(item: dict) -> dict:
        return {
            key: copy.deepcopy(value)
            for key, value in item.items()
            if not key.startswith("_") and key not in {"removed_step", "removed_at", "invalidated_reason"}
        }

    @staticmethod
    def _event(kind: str, item: dict, *, replacement_memory_id=None, reason=None) -> dict:
        return {
            "kind": "memory",
            "memory_kind": kind,
            "agent_key": item["agent_key"],
            "memory_id": item["id"],
            "memory_type": str(item["kind"]).upper(),
            "description": item["content"],
            "poignancy": item.get("poignancy"),
            "created_at": item.get("created_at"),
            "expires_at": item.get("expires_at"),
            "replacement_memory_id": replacement_memory_id,
            "reason": str(reason or "").strip() or None,
        }

    @staticmethod
    def _tokens(value: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", str(value)).casefold()
        tokens = set(re.findall(r"[a-z0-9]+", normalized))
        for segment in re.findall(r"[\u3400-\u9fff]+", normalized):
            tokens.update(segment[index : index + 2] for index in range(max(1, len(segment) - 1)))
            tokens.update(segment)
        return {token for token in tokens if token}

