"""供技能共享持久化服务使用的精简 MCP 接口。"""

from __future__ import annotations

import sqlite3
import hashlib
import json
import math
import re
import unicodedata
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import NAMESPACE_URL, UUID, uuid5

from generative_agents.status import MemoryDeltaKind, MemoryState


class MemoryStream:
    """运行私有的记忆状态，与运行本身共享同一个检查点边界。"""

    def __init__(
        self,
        database_path: str | Path,
        *,
        run_id: str | UUID = "skill-workspace",
        attempt_id: str | UUID = "skill-workspace",
        clock: Callable[[], datetime] | None = None,
        embed_texts: Callable[[list[str]], list[list[float]]] | None = None,
        logger=None,
    ) -> None:
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database_path: `database`对应的文件系统路径。 类型：`str | Path`。
            run_id: 仿真运行的唯一标识。 类型：`str | UUID`。 默认值：`'skill-workspace'`。
            attempt_id: 执行尝试的唯一标识，用于区分同一运行的重试或恢复批次。 类型：`str | UUID`。 默认值：`'skill-workspace'`。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。 类型：`Callable[[], datetime] | None`。 默认值：`None`。

        返回:
            无返回值。
        """
        self.database_path = Path(database_path).resolve()
        self.run_id = str(run_id)
        self.attempt_id = str(attempt_id)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._embed_texts = embed_texts
        self._logger = logger
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
                    embedding_json TEXT,
                    created_attempt_id TEXT NOT NULL,
                    supersedes_memory_id TEXT,
                    superseded_by_memory_id TEXT,
                    invalidated_reason TEXT,
                    PRIMARY KEY (run_id, id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_run_memories_agent_time "
                "ON run_memories(run_id, agent_key, state, created_at DESC)"
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(run_memories)")
            }
            if "embedding_json" not in columns:
                connection.execute(
                    "ALTER TABLE run_memories ADD COLUMN embedding_json TEXT"
                )
            for column, column_type in (
                ("supersedes_memory_id", "TEXT"),
                ("superseded_by_memory_id", "TEXT"),
                ("invalidated_reason", "TEXT"),
            ):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE run_memories ADD COLUMN {column} {column_type}"
                    )

    def begin_step(self, step_no: int, virtual_time: datetime) -> None:
        """执行 `MemoryStream` 的`begin`仿真步操作。

        参数:
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。
            virtual_time: `virtual`对应的时间点。 类型：`datetime`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if step_no < 1:
            raise ValueError("step_no must be positive")
        if virtual_time.tzinfo is None:
            raise ValueError("virtual_time must be timezone-aware")
        self._step_no = int(step_no)
        self._virtual_time = virtual_time

    def _scope(self) -> tuple[int, datetime]:
        """执行`scope`的内部处理，供当前模块或类复用。

        返回:
            返回按接口约定组织的结果集合。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
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
        _connection: sqlite3.Connection | None = None,
        _supersedes_memory_id: str | None = None,
    ) -> dict[str, Any]:
        """执行 `MemoryStream` 的`append`操作。

        参数:
            agent_key: 智能体在当前实验或运行中的稳定唯一键。 类型：`str`。
            content: 待解析、写入、哈希或发送给下游组件的正文内容。 类型：`str`。
            kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`str`。 默认值：`'event'`。
            poignancy: 记忆重要性评分，通常取 1 到 10。 类型：`int`。 默认值：`1`。
            memory_id: 记忆的唯一标识。 类型：`str | None`。 默认值：`None`。
            expires_at: `expires`对应的时间点。 类型：`datetime | None`。 默认值：`None`。
            subject: 事件三元组中的主体，通常是智能体或世界对象标识。 类型：`str | None`。 默认值：`None`。
            predicate: 事件三元组中描述主体与宾语关系的谓词。 类型：`str | None`。 默认值：`None`。
            object: 事件三元组中的宾语或当前交互对象。 类型：`str | None`。 默认值：`None`。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。 类型：`list[str] | tuple[str, ...]`。
            evidence_memory_ids: 需要批量处理的`evidence`记忆唯一标识集合。 类型：`list[str] | tuple[str, ...]`。
            emit_event: 是否把本次状态变化同时写入步骤领域事件流。 类型：`bool`。 默认值：`True`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        content = content.strip()
        if not agent_key.strip() or not content:
            raise ValueError("agent_key and content are required")
        if not 1 <= int(poignancy) <= 10:
            raise ValueError("poignancy must be between 1 and 10")
        step_no, virtual_time = self._scope()
        namespace = self._namespace()
        sequence = self._next_sequence(
            step_no, agent_key.strip(), connection=_connection
        )
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
            "state": MemoryState.ACTIVE.value,
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
            "embedding_json": self._embedding_json(content),
            "created_attempt_id": self.attempt_id,
            "supersedes_memory_id": _supersedes_memory_id,
            "superseded_by_memory_id": None,
            "invalidated_reason": None,
        }
        connection_scope = (
            nullcontext(_connection) if _connection is not None else self._connect()
        )
        with connection_scope as connection:
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
                        embedding_json, created_attempt_id, supersedes_memory_id,
                        superseded_by_memory_id, invalidated_reason
                    ) VALUES (
                        :run_id, :id, :agent_key, :content, :kind, :poignancy, :state,
                        :created_step, :created_at, :expires_at, :last_accessed_step,
                        :last_accessed_at, :removed_step, :removed_at, :subject,
                        :predicate, :object_value, :address_json, :evidence_json,
                        :embedding_json, :created_attempt_id, :supersedes_memory_id,
                        :superseded_by_memory_id, :invalidated_reason
                    )
                    """,
                    item,
                )
        public = self._public_item(item)
        if emit_event:
            self._pending_events.append(
                {
                    "kind": "memory",
                    "memory_kind": MemoryDeltaKind.CREATED.value,
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
        """执行 `MemoryStream` 的`search`操作。

        参数:
            agent_key: 智能体在当前实验或运行中的稳定唯一键。 类型：`str`。
            query: 用于名称、正文或标识模糊匹配的搜索文本。 类型：`str`。 默认值：`''`。
            limit: 本次最多返回或处理的记录数量。 类型：`int`。 默认值：`8`。
            emit_event: 是否把本次状态变化同时写入步骤领域事件流。 类型：`bool`。 默认值：`True`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        step_no, virtual_time = self._scope()
        limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, agent_key, content, kind, poignancy, created_at,
                       expires_at, subject, predicate, object_value,
                       address_json, evidence_json, embedding_json
                FROM run_memories
                WHERE run_id = ? AND agent_key = ? AND state = ?
                ORDER BY created_at DESC, id DESC LIMIT 500
                """,
                (self.run_id, agent_key.strip(), MemoryState.ACTIVE.value),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["address"] = json.loads(item.pop("address_json"))
            item["evidence_memory_ids"] = json.loads(item.pop("evidence_json"))
            item["object"] = item.pop("object_value")
            items.append(item)
        if query.strip():
            self._rank_items(query, items)
            items = [item for item in items if item["_retrieval_score"] > 0]
        selected = items[:limit]
        if selected:
            with self._connect() as connection:
                connection.executemany(
                    """
                    UPDATE run_memories
                    SET last_accessed_step = ?, last_accessed_at = ?
                    WHERE run_id = ? AND id = ? AND state = ?
                    """,
                    (
                        (
                            step_no,
                            virtual_time.isoformat(),
                            self.run_id,
                            item["id"],
                            MemoryState.ACTIVE.value,
                        )
                        for item in selected
                    ),
                )
            if emit_event:
                for item in selected:
                    self._pending_events.append(
                        {
                            "kind": "memory",
                            "memory_kind": MemoryDeltaKind.ACCESSED.value,
                            "agent_key": item["agent_key"],
                            "memory_id": item["id"],
                            "memory_type": item["kind"].upper(),
                            "description": item["content"],
                        }
                    )
        for item in selected:
            item.pop("embedding_json", None)
            item["retrieval_score"] = round(
                float(item.pop("_retrieval_score", 0.0)), 6
            )
            item["retrieval_method"] = item.pop(
                "_retrieval_method", "hybrid_lexical"
            )
        return selected

    def _embedding_json(self, text: str) -> str | None:
        if self._embed_texts is None:
            return None
        try:
            vectors = self._embed_texts([text])
            vector = self._valid_vector(vectors[0] if vectors else None)
            return json.dumps(vector, separators=(",", ":")) if vector else None
        except Exception as exc:  # semantic service failure must not lose memory writes
            if self._logger is not None:
                self._logger.warning("memory embedding write fallback: %s", exc)
            return None

    def _rank_items(self, query: str, items: list[dict[str, Any]]) -> None:
        lexical = [self._hybrid_lexical_score(query, self._search_text(item)) for item in items]
        semantic: list[float | None] = [None] * len(items)
        if self._embed_texts is not None and items:
            try:
                query_vectors = self._embed_texts([query])
                query_vector = self._valid_vector(
                    query_vectors[0] if query_vectors else None
                )
                missing_indexes: list[int] = []
                document_vectors: list[list[float] | None] = []
                for index, item in enumerate(items):
                    try:
                        vector = self._valid_vector(
                            json.loads(item.get("embedding_json") or "null")
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        vector = None
                    document_vectors.append(vector)
                    if vector is None:
                        missing_indexes.append(index)
                if missing_indexes:
                    generated = self._embed_texts(
                        [self._search_text(items[index]) for index in missing_indexes]
                    )
                    updates = []
                    for index, raw_vector in zip(missing_indexes, generated):
                        vector = self._valid_vector(raw_vector)
                        document_vectors[index] = vector
                        if vector:
                            encoded = json.dumps(vector, separators=(",", ":"))
                            items[index]["embedding_json"] = encoded
                            updates.append((encoded, self.run_id, items[index]["id"]))
                    if updates:
                        with self._connect() as connection:
                            connection.executemany(
                                "UPDATE run_memories SET embedding_json = ? "
                                "WHERE run_id = ? AND id = ?",
                                updates,
                            )
                if query_vector:
                    semantic = [
                        self._cosine(query_vector, vector) if vector else None
                        for vector in document_vectors
                    ]
            except Exception as exc:
                if self._logger is not None:
                    self._logger.warning("memory semantic search fallback: %s", exc)

        has_semantic = any(score is not None for score in semantic)
        for index, item in enumerate(items):
            semantic_score = semantic[index]
            if semantic_score is None:
                score = lexical[index]
                method = "hybrid_lexical"
            else:
                normalized_semantic = max(0.0, min(1.0, (semantic_score + 1.0) / 2.0))
                score = normalized_semantic * 0.78 + lexical[index] * 0.22
                method = "embedding_hybrid"
            # With vectors, top-k semantic retrieval intentionally returns the
            # closest active memories.  The lexical fallback requires meaningful
            # overlap so an unrelated query does not match on punctuation/stopwords.
            if not has_semantic and lexical[index] < 0.08:
                score = 0.0
            item["_retrieval_score"] = score
            item["_retrieval_method"] = method
        items.sort(
            key=lambda item: (
                float(item["_retrieval_score"]),
                int(item["poignancy"]),
                str(item["created_at"]),
            ),
            reverse=True,
        )

    @staticmethod
    def _search_text(item: dict[str, Any]) -> str:
        return " ".join(
            str(value or "")
            for value in (
                item.get("content"),
                item.get("subject"),
                item.get("predicate"),
                item.get("object"),
                " ".join(item.get("address") or ()),
            )
        )

    @staticmethod
    def _hybrid_lexical_score(query: str, document: str) -> float:
        query_tokens = MemoryStream._semantic_tokens(query)
        document_tokens = MemoryStream._semantic_tokens(document)
        if not query_tokens or not document_tokens:
            return 0.0
        overlap = query_tokens & document_tokens
        if not overlap:
            return 0.0
        weighted_overlap = sum(1.6 if len(token) >= 2 else 0.35 for token in overlap)
        weighted_query = sum(1.6 if len(token) >= 2 else 0.35 for token in query_tokens)
        return min(1.0, weighted_overlap / max(weighted_query, 1.0))

    @staticmethod
    def _semantic_tokens(value: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        tokens = set(re.findall(r"[a-z0-9]+", normalized))
        stop_chars = set("的是了和与在有就都而及或也吗呢哪什今本此个种")
        for segment in re.findall(r"[\u3400-\u9fff]+", normalized):
            meaningful = "".join(char for char in segment if char not in stop_chars)
            tokens.update(char for char in meaningful)
            tokens.update(
                meaningful[index : index + 2]
                for index in range(max(0, len(meaningful) - 1))
            )
            tokens.update(
                meaningful[index : index + 3]
                for index in range(max(0, len(meaningful) - 2))
            )
        return {token for token in tokens if token}

    @staticmethod
    def _valid_vector(value) -> list[float] | None:
        if not isinstance(value, (list, tuple)) or not value:
            return None
        try:
            vector = [float(component) for component in value]
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(component) for component in vector):
            return None
        return vector

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right) or not left:
            return 0.0
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)

    def remove(
        self,
        memory_id: str,
        *,
        state: MemoryState | str,
        agent_key: str | None = None,
        replacement_memory_id: str | None = None,
        reason: str | None = None,
        emit_event: bool = True,
        _connection: sqlite3.Connection | None = None,
    ) -> None:
        """把目标记录迁移到指定移除状态，并按需生成领域事件。

        参数:
            memory_id: 记忆的唯一标识。 类型：`str`。
            state: 记忆移除状态。允许值：`EXPIRED`（自然过期）或 `EVICTED`（容量淘汰）。 类型：`MemoryState | str`。
            emit_event: 是否把本次状态变化同时写入步骤领域事件流。 类型：`bool`。 默认值：`True`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        try:
            memory_state = MemoryState(state)
        except ValueError as exc:
            raise ValueError(
                "memory state must be EXPIRED, EVICTED, SUPERSEDED or INVALIDATED"
            ) from exc
        if memory_state not in {
            MemoryState.EXPIRED,
            MemoryState.EVICTED,
            MemoryState.SUPERSEDED,
            MemoryState.INVALIDATED,
        }:
            raise ValueError(
                "memory state must be EXPIRED, EVICTED, SUPERSEDED or INVALIDATED"
            )
        if memory_state == MemoryState.SUPERSEDED and not replacement_memory_id:
            raise ValueError("SUPERSEDED requires replacement_memory_id")
        if memory_state == MemoryState.INVALIDATED and not str(reason or "").strip():
            raise ValueError("INVALIDATED requires a reason")
        step_no, virtual_time = self._scope()
        connection_scope = (
            nullcontext(_connection) if _connection is not None else self._connect()
        )
        with connection_scope as connection:
            existing = connection.execute(
                """
                SELECT agent_key, kind, content FROM run_memories
                WHERE run_id = ? AND id = ? AND state = ?
                  AND (? IS NULL OR agent_key = ?)
                """,
                (
                    self.run_id,
                    memory_id,
                    MemoryState.ACTIVE.value,
                    agent_key,
                    agent_key,
                ),
            ).fetchone()
            cursor = connection.execute(
                """
                UPDATE run_memories
                SET state = ?, removed_step = ?, removed_at = ?,
                    superseded_by_memory_id = ?, invalidated_reason = ?
                WHERE run_id = ? AND id = ? AND state = ?
                  AND (? IS NULL OR agent_key = ?)
                """,
                (
                    memory_state.value,
                    step_no,
                    virtual_time.isoformat(),
                    replacement_memory_id,
                    str(reason or "").strip() or None,
                    self.run_id,
                    memory_id,
                    MemoryState.ACTIVE.value,
                    agent_key,
                    agent_key,
                ),
            )
        if cursor.rowcount == 0:
            raise ValueError(f"active memory does not exist: {memory_id}")
        if emit_event and existing is not None:
            self._pending_events.append(
                {
                    "kind": "memory",
                    "memory_kind": memory_state.value,
                    "agent_key": existing[0],
                    "memory_id": memory_id,
                    "memory_type": existing[1].upper(),
                    "description": existing[2],
                    "replacement_memory_id": replacement_memory_id,
                    "reason": str(reason or "").strip() or None,
                }
            )

    def supersede(
        self,
        *,
        agent_key: str,
        memory_id: str,
        content: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Atomically replace one active memory while retaining its history."""

        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            existing = connection.execute(
                """
                SELECT * FROM run_memories
                WHERE run_id = ? AND id = ? AND agent_key = ? AND state = ?
                """,
                (
                    self.run_id,
                    memory_id,
                    agent_key.strip(),
                    MemoryState.ACTIVE.value,
                ),
            ).fetchone()
            if existing is None:
                raise ValueError(f"active memory does not exist: {memory_id}")
            evidence = list(json.loads(existing["evidence_json"] or "[]"))
            if memory_id not in evidence:
                evidence.append(memory_id)
            replacement = self.append(
                agent_key=agent_key,
                content=content,
                kind=existing["kind"],
                poignancy=int(existing["poignancy"]),
                expires_at=(
                    datetime.fromisoformat(existing["expires_at"])
                    if existing["expires_at"]
                    else None
                ),
                subject=existing["subject"],
                predicate=existing["predicate"],
                object=existing["object_value"],
                address=json.loads(existing["address_json"] or "[]"),
                evidence_memory_ids=evidence,
                emit_event=False,
                _connection=connection,
                _supersedes_memory_id=memory_id,
            )
            self.remove(
                memory_id,
                state=MemoryState.SUPERSEDED,
                agent_key=agent_key,
                replacement_memory_id=replacement["id"],
                reason=reason,
                emit_event=False,
                _connection=connection,
            )
        self._pending_events.extend(
            [
                {
                    "kind": "memory",
                    "memory_kind": MemoryDeltaKind.SUPERSEDED.value,
                    "agent_key": agent_key.strip(),
                    "memory_id": memory_id,
                    "memory_type": str(existing["kind"]).upper(),
                    "description": existing["content"],
                    "replacement_memory_id": replacement["id"],
                    "reason": str(reason or "").strip() or None,
                },
                {
                    "kind": "memory",
                    "memory_kind": MemoryDeltaKind.CREATED.value,
                    "agent_key": replacement["agent_key"],
                    "memory_id": replacement["id"],
                    "memory_type": replacement["kind"].upper(),
                    "description": replacement["content"],
                    "poignancy": replacement["poignancy"],
                    "created_at": replacement["created_at"],
                    "expires_at": replacement["expires_at"],
                    "evidence_memory_ids": replacement["evidence_memory_ids"],
                    "supersedes_memory_id": memory_id,
                },
            ]
        )
        return {"superseded_memory_id": memory_id, "replacement": replacement}

    def invalidate(
        self,
        *,
        agent_key: str,
        memory_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Explicitly invalidate one active memory without deleting history."""

        self.remove(
            memory_id,
            state=MemoryState.INVALIDATED,
            agent_key=agent_key,
            reason=reason,
        )
        return {
            "memory_id": memory_id,
            "state": MemoryState.INVALIDATED.value,
            "reason": reason.strip(),
        }

    def access(self, memory_id: str, *, emit_event: bool = True) -> None:
        """更新记忆的最近访问步骤与时间，并按需生成访问事件。

        参数:
            memory_id: 记忆的唯一标识。 类型：`str`。
            emit_event: 是否把本次状态变化同时写入步骤领域事件流。 类型：`bool`。 默认值：`True`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        step_no, virtual_time = self._scope()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT agent_key, kind, content FROM run_memories
                WHERE run_id = ? AND id = ? AND state = ?
                """,
                (self.run_id, memory_id, MemoryState.ACTIVE.value),
            ).fetchone()
            cursor = connection.execute(
                """
                UPDATE run_memories
                SET last_accessed_step = ?, last_accessed_at = ?
                WHERE run_id = ? AND id = ? AND state = ?
                """,
                (
                    step_no,
                    virtual_time.isoformat(),
                    self.run_id,
                    memory_id,
                    MemoryState.ACTIVE.value,
                ),
            )
        if cursor.rowcount == 0:
            raise ValueError(f"active memory does not exist: {memory_id}")
        if emit_event and existing is not None:
            self._pending_events.append(
                {
                    "kind": "memory",
                    "memory_kind": MemoryDeltaKind.ACCESSED.value,
                    "agent_key": existing[0],
                    "memory_id": memory_id,
                    "memory_type": existing[1].upper(),
                    "description": existing[2],
                }
            )

    def drain_result_events(self) -> tuple[dict[str, Any], ...]:
        """执行 `MemoryStream` 的`drain`结果`events`操作。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        events, self._pending_events = tuple(self._pending_events), []
        return events

    def export_storage(self, target: Path) -> None:
        """执行 `MemoryStream` 的`export`存储操作。

        参数:
            target: 当前操作使用的`target`。 类型：`Path`。

        返回:
            无返回值。
        """
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

    def _next_sequence(
        self,
        step_no: int,
        agent_key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """执行`next``sequence`的内部处理，供当前模块或类复用。

        参数:
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。
            agent_key: 智能体在当前实验或运行中的稳定唯一键。 类型：`str`。

        返回:
            返回计算得到的整数值或版本号。
        """
        connection_scope = (
            nullcontext(connection) if connection is not None else self._connect()
        )
        with connection_scope as active_connection:
            value = active_connection.execute(
                """
                SELECT COUNT(*) FROM run_memories
                WHERE run_id = ? AND created_step = ? AND agent_key = ?
                """,
                (self.run_id, step_no, agent_key),
            ).fetchone()[0]
        return int(value) + 1

    def _namespace(self) -> UUID:
        """执行`namespace`的内部处理，供当前模块或类复用。

        返回:
            返回 `UUID` 类型的处理结果。
        """
        try:
            return UUID(self.run_id)
        except ValueError:
            return uuid5(NAMESPACE_URL, self.run_id)

    @staticmethod
    def _public_item(item: dict[str, Any]) -> dict[str, Any]:
        """执行`public``item`的内部处理，供当前模块或类复用。

        参数:
            item: 当前操作使用的`item`。 类型：`dict[str, Any]`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
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
            "state": item["state"],
            "supersedes_memory_id": item.get("supersedes_memory_id"),
            "superseded_by_memory_id": item.get("superseded_by_memory_id"),
            "invalidated_reason": item.get("invalidated_reason"),
        }

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """执行`connect`的内部处理，供当前模块或类复用。

        返回:
            返回可按需迭代的结果序列。
        """
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
    """处理本地技能客户端所需的 MCP JSON-RPC 方法。"""

    def __init__(self, memory: MemoryStream) -> None:
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            memory: 当前读取、更新或转换的记忆记录。 类型：`MemoryStream`。

        返回:
            无返回值。
        """
        self.memory = memory

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """执行 `SkillMCPServer` 的`handle`操作。

        参数:
            request: 待执行、记录或发送到外部模型的请求对象。 类型：`dict[str, Any]`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        request_id = request.get("id")
        method = request.get("method")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "generative-agents-skills",
                        "version": "1.0.0",
                    },
                }
            elif method == "notifications/initialized":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tools()}
            elif method == "tools/call":
                params = request.get("params") or {}
                result = self.call(
                    str(params.get("name") or ""), dict(params.get("arguments") or {})
                )
            else:
                return self._error(request_id, -32601, f"Method not found: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (TypeError, ValueError) as exc:
            return self._error(request_id, -32602, str(exc))

    @staticmethod
    def tools() -> list[dict[str, Any]]:
        """执行 `SkillMCPServer` 的`tools`操作。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
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
            {
                "name": "memory-stream-supersede",
                "description": "Replace an active memory with a corrected version while retaining history.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_key": {"type": "string"},
                        "memory_id": {"type": "string"},
                        "content": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["agent_key", "memory_id", "content"],
                },
            },
            {
                "name": "memory-stream-invalidate",
                "description": "Invalidate an active memory without deleting its audit history.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_key": {"type": "string"},
                        "memory_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["agent_key", "memory_id", "reason"],
                },
            },
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行 `SkillMCPServer` 的`call`操作。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。
            arguments: 传给底层调用的额外位置参数，顺序和含义与被调用接口保持一致。 类型：`dict[str, Any]`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
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
        elif name == "memory-stream-supersede":
            value = self.memory.supersede(**arguments)
            text = (
                f"已用新版本 {value['replacement']['id']} 替代记忆 "
                f"{value['superseded_memory_id']}。"
            )
        elif name == "memory-stream-invalidate":
            value = self.memory.invalidate(**arguments)
            text = f"已将记忆 {value['memory_id']} 标记为 INVALIDATED。"
        else:
            raise ValueError(f"Unknown MCP tool: {name}")
        return {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        """执行`error`的内部处理，供当前模块或类复用。

        参数:
            request_id: `request`的唯一标识。 类型：`Any`。
            code: 稳定错误码、状态码或调用方可识别的协议代码。 类型：`int`。
            message: 待发送、校验、脱敏或写入会话的消息文本或对象。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
