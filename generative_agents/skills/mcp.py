"""Small MCP surface for persistent services shared by Skills."""

from __future__ import annotations

import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import NAMESPACE_URL, UUID, uuid5


class MemoryStream:
    """Run-owned memory state backed by the same checkpoint boundary as a Run."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        run_id: str | UUID = "skill-workspace",
        attempt_id: str | UUID = "skill-workspace",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.run_id = str(run_id)
        self.attempt_id = str(attempt_id)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._step_no = 0
        self._virtual_time: datetime | None = None
        self._pending_events: list[dict[str, Any]] = []
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS run_memories (
                    run_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    agent_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    poignancy INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_step INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    last_accessed_step INTEGER NOT NULL,
                    last_accessed_at TEXT NOT NULL,
                    removed_step INTEGER,
                    removed_at TEXT,
                    subject TEXT,
                    predicate TEXT,
                    object_value TEXT,
                    address_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_attempt_id TEXT NOT NULL,
                    PRIMARY KEY (run_id, id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_run_memories_agent_time "
                "ON run_memories(run_id, agent_key, state, created_at DESC)"
            )

    def begin_step(self, step_no: int, virtual_time: datetime) -> None:
        if step_no < 1:
            raise ValueError("step_no must be positive")
        if virtual_time.tzinfo is None:
            raise ValueError("virtual_time must be timezone-aware")
        self._step_no = int(step_no)
        self._virtual_time = virtual_time

    def _scope(self) -> tuple[int, datetime]:
        if self._step_no < 1 and self.run_id == "skill-workspace":
            current = self._clock()
            if current.tzinfo is None:
                current = current.replace(tzinfo=UTC)
            self.begin_step(1, current)
        if self._step_no < 1 or self._virtual_time is None:
            raise RuntimeError("memory stream must be bound to a simulation step")
        return self._step_no, self._virtual_time

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
        address: list[str] | tuple[str, ...] = (),
        evidence_memory_ids: list[str] | tuple[str, ...] = (),
        emit_event: bool = True,
    ) -> dict[str, Any]:
        import json

        content = content.strip()
        if not agent_key.strip() or not content:
            raise ValueError("agent_key and content are required")
        if not 1 <= int(poignancy) <= 10:
            raise ValueError("poignancy must be between 1 and 10")
        step_no, virtual_time = self._scope()
        namespace = self._namespace()
        sequence = self._next_sequence(step_no, agent_key.strip())
        stable_key = (
            f"{step_no}:{agent_key.strip()}:{sequence}:{kind}:"
            f"{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
        )
        item = {
            "run_id": self.run_id,
            "id": str(memory_id or uuid5(namespace, stable_key)),
            "agent_key": agent_key.strip(),
            "content": content,
            "kind": kind.strip() or "event",
            "poignancy": int(poignancy),
            "state": "ACTIVE",
            "created_step": step_no,
            "created_at": virtual_time.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "last_accessed_step": step_no,
            "last_accessed_at": virtual_time.isoformat(),
            "removed_step": None,
            "removed_at": None,
            "subject": subject,
            "predicate": predicate,
            "object_value": object,
            "address_json": json.dumps(list(address), ensure_ascii=False),
            "evidence_json": json.dumps(list(evidence_memory_ids), ensure_ascii=False),
            "created_attempt_id": self.attempt_id,
        }
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT content, kind FROM run_memories WHERE run_id = ? AND id = ?",
                (self.run_id, item["id"]),
            ).fetchone()
            if existing is not None:
                if existing != (item["content"], item["kind"]):
                    raise ValueError("memory_id already exists with different content")
            else:
                connection.execute(
                    """
                    INSERT INTO run_memories(
                        run_id, id, agent_key, content, kind, poignancy, state,
                        created_step, created_at, expires_at, last_accessed_step,
                        last_accessed_at, removed_step, removed_at, subject,
                        predicate, object_value, address_json, evidence_json,
                        created_attempt_id
                    ) VALUES (
                        :run_id, :id, :agent_key, :content, :kind, :poignancy, :state,
                        :created_step, :created_at, :expires_at, :last_accessed_step,
                        :last_accessed_at, :removed_step, :removed_at, :subject,
                        :predicate, :object_value, :address_json, :evidence_json,
                        :created_attempt_id
                    )
                    """,
                    item,
                )
        public = self._public_item(item)
        if emit_event:
            self._pending_events.append(
                {
                    "kind": "memory",
                    "memory_kind": "CREATED",
                    "agent_key": item["agent_key"],
                    "memory_id": item["id"],
                    "memory_type": item["kind"].upper(),
                    "description": item["content"],
                    "poignancy": item["poignancy"],
                    "event": {
                        "subject": item["subject"],
                        "predicate": item["predicate"],
                        "object": item["object_value"],
                        "describe": item["content"],
                        "address": list(address),
                    },
                    "created_at": item["created_at"],
                    "expires_at": item["expires_at"],
                    "evidence_memory_ids": list(evidence_memory_ids),
                }
            )
        return public

    def search(
        self,
        *,
        agent_key: str,
        query: str = "",
        limit: int = 8,
        emit_event: bool = True,
    ) -> list[dict[str, Any]]:
        import json

        step_no, virtual_time = self._scope()
        limit = max(1, min(int(limit), 100))
        terms = [term.casefold() for term in query.split() if term.strip()]
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, agent_key, content, kind, poignancy, created_at,
                       expires_at, subject, predicate, object_value,
                       address_json, evidence_json
                FROM run_memories
                WHERE run_id = ? AND agent_key = ? AND state = 'ACTIVE'
                ORDER BY created_at DESC, id DESC LIMIT 500
                """,
                (self.run_id, agent_key.strip()),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["address"] = json.loads(item.pop("address_json"))
            item["evidence_memory_ids"] = json.loads(item.pop("evidence_json"))
            item["object"] = item.pop("object_value")
            items.append(item)
        if terms:
            items.sort(
                key=lambda item: (
                    sum(term in str(item["content"]).casefold() for term in terms),
                    int(item["poignancy"]),
                    str(item["created_at"]),
                ),
                reverse=True,
            )
            items = [
                item for item in items
                if any(term in str(item["content"]).casefold() for term in terms)
            ]
        selected = items[:limit]
        if selected:
            with self._connect() as connection:
                connection.executemany(
                    """
                    UPDATE run_memories
                    SET last_accessed_step = ?, last_accessed_at = ?
                    WHERE run_id = ? AND id = ? AND state = 'ACTIVE'
                    """,
                    (
                        (step_no, virtual_time.isoformat(), self.run_id, item["id"])
                        for item in selected
                    ),
                )
            if emit_event:
                for item in selected:
                    self._pending_events.append(
                        {
                            "kind": "memory",
                            "memory_kind": "ACCESSED",
                            "agent_key": item["agent_key"],
                            "memory_id": item["id"],
                            "memory_type": item["kind"].upper(),
                            "description": item["content"],
                        }
                    )
        return selected

    def remove(self, memory_id: str, *, state: str, emit_event: bool = True) -> None:
        if state not in {"EXPIRED", "EVICTED"}:
            raise ValueError("memory state must be EXPIRED or EVICTED")
        step_no, virtual_time = self._scope()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT agent_key, kind, content FROM run_memories
                WHERE run_id = ? AND id = ? AND state = 'ACTIVE'
                """,
                (self.run_id, memory_id),
            ).fetchone()
            cursor = connection.execute(
                """
                UPDATE run_memories
                SET state = ?, removed_step = ?, removed_at = ?
                WHERE run_id = ? AND id = ? AND state = 'ACTIVE'
                """,
                (state, step_no, virtual_time.isoformat(), self.run_id, memory_id),
            )
        if cursor.rowcount == 0:
            raise ValueError(f"active memory does not exist: {memory_id}")
        if emit_event and existing is not None:
            self._pending_events.append(
                {
                    "kind": "memory",
                    "memory_kind": state,
                    "agent_key": existing[0],
                    "memory_id": memory_id,
                    "memory_type": existing[1].upper(),
                    "description": existing[2],
                }
            )

    def access(self, memory_id: str, *, emit_event: bool = True) -> None:
        step_no, virtual_time = self._scope()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT agent_key, kind, content FROM run_memories
                WHERE run_id = ? AND id = ? AND state = 'ACTIVE'
                """,
                (self.run_id, memory_id),
            ).fetchone()
            cursor = connection.execute(
                """
                UPDATE run_memories
                SET last_accessed_step = ?, last_accessed_at = ?
                WHERE run_id = ? AND id = ? AND state = 'ACTIVE'
                """,
                (step_no, virtual_time.isoformat(), self.run_id, memory_id),
            )
        if cursor.rowcount == 0:
            raise ValueError(f"active memory does not exist: {memory_id}")
        if emit_event and existing is not None:
            self._pending_events.append(
                {
                    "kind": "memory",
                    "memory_kind": "ACCESSED",
                    "agent_key": existing[0],
                    "memory_id": memory_id,
                    "memory_type": existing[1].upper(),
                    "description": existing[2],
                }
            )

    def drain_result_events(self) -> tuple[dict[str, Any], ...]:
        events, self._pending_events = tuple(self._pending_events), []
        return events

    def export_storage(self, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        destination = target / "memory.sqlite"
        with self._connect() as source:
            output = sqlite3.connect(destination, timeout=30)
            try:
                source.backup(output)
                output.commit()
            finally:
                # A sqlite Connection context manager commits or rolls back but
                # does not close the file.  Keeping this handle alive prevents
                # the surrounding checkpoint directory from being atomically
                # renamed on Windows.
                output.close()

    def _next_sequence(self, step_no: int, agent_key: str) -> int:
        with self._connect() as connection:
            value = connection.execute(
                """
                SELECT COUNT(*) FROM run_memories
                WHERE run_id = ? AND created_step = ? AND agent_key = ?
                """,
                (self.run_id, step_no, agent_key),
            ).fetchone()[0]
        return int(value) + 1

    def _namespace(self) -> UUID:
        try:
            return UUID(self.run_id)
        except ValueError:
            return uuid5(NAMESPACE_URL, self.run_id)

    @staticmethod
    def _public_item(item: dict[str, Any]) -> dict[str, Any]:
        import json

        return {
            "id": item["id"],
            "agent_key": item["agent_key"],
            "content": item["content"],
            "kind": item["kind"],
            "poignancy": item["poignancy"],
            "created_at": item["created_at"],
            "expires_at": item["expires_at"],
            "subject": item["subject"],
            "predicate": item["predicate"],
            "object": item["object_value"],
            "address": json.loads(item["address_json"]),
            "evidence_memory_ids": json.loads(item["evidence_json"]),
        }

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            # sqlite3.Connection.__exit__ deliberately leaves the connection
            # open.  Run-owned memory is checkpointed as a directory tree, so
            # every short-lived operation must release its Windows file handle
            # before CheckpointBundleWriter publishes that tree.
            connection.close()


class SkillMCPServer:
    """Handle the MCP JSON-RPC methods needed by local Skill clients."""

    def __init__(self, memory: MemoryStream) -> None:
        self.memory = memory

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        method = request.get("method")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "generative-agents-skills", "version": "1.0.0"},
                }
            elif method == "notifications/initialized":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tools()}
            elif method == "tools/call":
                params = request.get("params") or {}
                result = self.call(str(params.get("name") or ""), dict(params.get("arguments") or {}))
            else:
                return self._error(request_id, -32601, f"Method not found: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (TypeError, ValueError) as exc:
            return self._error(request_id, -32602, str(exc))

    @staticmethod
    def tools() -> list[dict[str, Any]]:
        return [
            {
                "name": "memory-stream-append",
                "description": "Append a natural-language memory to one agent's persistent memory stream.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_key": {"type": "string"},
                        "content": {"type": "string"},
                        "kind": {"type": "string", "default": "event"},
                        "poignancy": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["agent_key", "content"],
                },
            },
            {
                "name": "memory-stream-search",
                "description": "Search one agent's persistent memories and return relevant natural-language entries.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_key": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "required": ["agent_key"],
                },
            },
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "memory-stream-append":
            value = self.memory.append(**arguments)
            text = f"已写入 {value['agent_key']} 的记忆流：{value['content']}"
        elif name == "memory-stream-search":
            value = self.memory.search(**arguments)
            if value:
                entries = "\n".join(
                    f"- {item['created_at']}｜{item['kind']}｜重要度 {item['poignancy']}：{item['content']}"
                    for item in value
                )
                text = f"找到 {len(value)} 条相关记忆：\n{entries}"
            else:
                text = "没有找到相关记忆。"
        else:
            raise ValueError(f"Unknown MCP tool: {name}")
        return {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
