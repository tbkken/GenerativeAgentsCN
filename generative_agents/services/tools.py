"""Lifecycle management for versioned world tools."""

from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime, timezone
from math import ceil
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from generative_agents.config.hashing import canonical_json_bytes
from generative_agents.config.tools import ToolContract
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    ToolDefinition,
    ToolRevision,
)

from .errors import ServiceError, not_found


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _generated_key(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:48]
    return f"{base or 'tool'}-{uuid4().hex[:8]}"


def _builtins() -> dict[str, ToolContract]:
    return {
        "generic-car": ToolContract.model_validate(
            {
                "name": "通用小汽车",
                "summary": "由人驾驶、沿道路网络运动的四座汽车工具。",
                "kind": "CAR",
                "appearance": {"mode": "EMOJI", "emoji": "🚙"},
                "mobility": {
                    "mode": "ROAD",
                    "max_speed_mps": 16.7,
                    "max_acceleration_mps2": 2.5,
                    "max_deceleration_mps2": 7.0,
                    "operator_required": True,
                    "capacity": 4,
                },
                "tags": ["vehicle", "road-vehicle"],
                "interfaces": ["drivable", "occupiable"],
                "initial_state": {"occupied": False, "speed_mps": 0},
            }
        ),
        "generic-bicycle": ToolContract.model_validate(
            {
                "name": "通用自行车",
                "summary": "可在人行道或自行车网络低速行驶的单人工具。",
                "kind": "BICYCLE",
                "appearance": {"mode": "EMOJI", "emoji": "🚲"},
                "mobility": {
                    "mode": "BICYCLE_NETWORK",
                    "max_speed_mps": 7,
                    "max_acceleration_mps2": 1.5,
                    "max_deceleration_mps2": 4,
                    "operator_required": True,
                    "capacity": 1,
                },
                "tags": ["vehicle", "light-vehicle"],
                "interfaces": ["rideable", "occupiable"],
            }
        ),
        "generic-motorcycle": ToolContract.model_validate(
            {
                "name": "通用摩托车",
                "summary": "由人驾驶、沿道路网络运动的双轮机动车工具。",
                "kind": "MOTORCYCLE",
                "appearance": {"mode": "EMOJI", "emoji": "🏍️"},
                "mobility": {
                    "mode": "ROAD",
                    "max_speed_mps": 22,
                    "max_acceleration_mps2": 3.5,
                    "max_deceleration_mps2": 7,
                    "operator_required": True,
                    "capacity": 2,
                },
                "tags": ["vehicle", "road-vehicle"],
                "interfaces": ["rideable", "occupiable"],
            }
        ),
        "generic-access-card": ToolContract.model_validate(
            {
                "name": "通用门禁卡",
                "summary": "可由人携带并向门禁物件产生刷卡事件。",
                "kind": "ACCESS_CARD",
                "appearance": {"mode": "EMOJI", "emoji": "💳"},
                "mobility": {"mode": "NONE"},
                "tags": ["credential"],
                "interfaces": ["access-credential"],
                "initial_state": {"active": True},
            }
        ),
    }



class ToolService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_builtin_tools(self) -> None:
        with self.database.session_factory.begin() as session:
            for tool_key, contract in _builtins().items():
                if session.scalar(
                    select(ToolDefinition).where(ToolDefinition.tool_key == tool_key)
                ):
                    continue
                now = _now()
                document = contract.model_dump(mode="json", exclude_none=False)
                tool = ToolDefinition(
                    id=str(uuid4()),
                    tool_key=tool_key,
                    name=contract.name,
                    description=contract.summary,
                    tool_kind=contract.kind,
                    status="PUBLISHED",
                    is_builtin=True,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(tool)
                session.flush()
                revision = ToolRevision(
                    id=str(uuid4()),
                    tool_id=tool.id,
                    revision_no=1,
                    state="PUBLISHED",
                    schema_version=contract.schema_version,
                    contract_json=document,
                    contract_hash=_digest(document),
                    validation_json={"valid": True, "errors": [], "warnings": []},
                    lock_version=1,
                    created_at=now,
                    updated_at=now,
                    published_at=now,
                )
                session.add(revision)
                session.flush()
                tool.current_published_revision_id = revision.id

    def create_tool(
        self,
        *,
        name: str,
        description: str = "",
        tool_key: str | None = None,
        tool_kind: str = "OTHER",
        source_revision_id: str | None = None,
        contract: ToolContract | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ServiceError("INVALID_TOOL_NAME", "工具名称不能为空", status_code=422)
        stable_key = tool_key or _generated_key(name)
        with self.database.session_factory.begin() as session:
            if session.scalar(
                select(ToolDefinition.id).where(ToolDefinition.tool_key == stable_key)
            ):
                raise ServiceError("TOOL_KEY_CONFLICT", "工具稳定键已被使用", status_code=409)
            source = None
            if source_revision_id:
                source = session.get(ToolRevision, source_revision_id)
                if source is None or source.state != "PUBLISHED":
                    raise not_found("tool_revision", source_revision_id)
                model = ToolContract.model_validate(source.contract_json)
            elif contract is not None:
                model = ToolContract.model_validate(contract)
            else:
                default_mobility = {
                    "CAR": {
                        "mode": "ROAD",
                        "max_speed_mps": 13.9,
                        "max_acceleration_mps2": 2.0,
                        "max_deceleration_mps2": 6.0,
                        "operator_required": True,
                        "capacity": 4,
                    },
                    "BICYCLE": {
                        "mode": "BICYCLE_NETWORK",
                        "max_speed_mps": 6.0,
                        "max_acceleration_mps2": 1.2,
                        "max_deceleration_mps2": 3.0,
                        "operator_required": True,
                        "capacity": 1,
                    },
                    "MOTORCYCLE": {
                        "mode": "ROAD",
                        "max_speed_mps": 16.7,
                        "max_acceleration_mps2": 2.5,
                        "max_deceleration_mps2": 6.0,
                        "operator_required": True,
                        "capacity": 2,
                    },
                }.get(tool_kind, {"mode": "NONE"})
                model = ToolContract.model_validate(
                    {
                        "name": name,
                        "summary": description,
                        "kind": tool_kind,
                        "mobility": default_mobility,
                        "appearance": {"mode": "EMOJI", "emoji": "🧰"},
                    }
                )
            now = _now()
            document = model.model_dump(mode="json", exclude_none=False)
            tool = ToolDefinition(
                id=str(uuid4()),
                tool_key=stable_key,
                name=name,
                description=description.strip()[:10_000],
                tool_kind=model.kind,
                status="DRAFT",
                is_builtin=False,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(tool)
            session.flush()
            revision = ToolRevision(
                id=str(uuid4()),
                tool_id=tool.id,
                revision_no=1,
                state="DRAFT",
                base_revision_id=source.id if source else None,
                schema_version=model.schema_version,
                contract_json=document,
                contract_hash=_digest(document),
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(revision)
            session.flush()
            tool.current_draft_revision_id = revision.id
            return self._detail(session, tool)

    def list_tools(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        if page < 1 or not 1 <= page_size <= 100:
            raise ServiceError("INVALID_PAGINATION", "工具分页参数无效", status_code=422)
        statement = select(ToolDefinition)
        count_statement = select(func.count()).select_from(ToolDefinition)
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            predicate = or_(
                ToolDefinition.name.ilike(pattern), ToolDefinition.tool_key.ilike(pattern)
            )
            statement = statement.where(predicate)
            count_statement = count_statement.where(predicate)
        if status:
            normalized = status.upper()
            if normalized not in {"DRAFT", "PUBLISHED"}:
                raise ServiceError("INVALID_TOOL_STATUS", "工具状态筛选无效", status_code=422)
            statement = statement.where(ToolDefinition.status == normalized)
            count_statement = count_statement.where(ToolDefinition.status == normalized)
        if kind:
            normalized_kind = kind.upper()
            if normalized_kind not in {
                "CAR",
                "BICYCLE",
                "MOTORCYCLE",
                "ACCESS_CARD",
                "DEVICE",
                "OTHER",
            }:
                raise ServiceError("INVALID_TOOL_KIND", "工具类型筛选无效", status_code=422)
            statement = statement.where(ToolDefinition.tool_kind == normalized_kind)
            count_statement = count_statement.where(
                ToolDefinition.tool_kind == normalized_kind
            )
        with self.database.session_factory() as session:
            total = int(session.scalar(count_statement) or 0)
            rows = list(
                session.scalars(
                    statement.order_by(
                        ToolDefinition.is_builtin.desc(), ToolDefinition.updated_at.desc()
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return {
                "items": [self._detail(session, item) for item in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, ceil(total / page_size)),
            }

    def get_tool(self, tool_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            tool = session.get(ToolDefinition, tool_id)
            if tool is None:
                raise not_found("tool", tool_id)
            return self._detail(session, tool)

    def get_draft(self, tool_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            tool, revision = self._require_draft(session, tool_id)
            return self._revision_detail(revision, tool)

    def update_draft(
        self,
        tool_id: str,
        *,
        expected_lock_version: int,
        contract: ToolContract | dict[str, Any],
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        model = ToolContract.model_validate(contract)
        document = model.model_dump(mode="json", exclude_none=False)
        with self.database.session_factory.begin() as session:
            tool, revision = self._require_draft(session, tool_id)
            if revision.lock_version != expected_lock_version:
                raise ServiceError("TOOL_REVISION_CONFLICT", "工具草稿已变化", status_code=409)
            revision.contract_json = document
            revision.contract_hash = _digest(document)
            revision.validation_json = None
            revision.lock_version += 1
            revision.updated_at = _now()
            tool.tool_kind = model.kind
            if name is not None:
                tool.name = name.strip()
            if description is not None:
                tool.description = description.strip()[:10_000]
            tool.row_version += 1
            tool.updated_at = revision.updated_at
            return self._revision_detail(revision, tool)

    def publish_draft(
        self, tool_id: str, *, draft_revision_id: str, expected_lock_version: int
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            tool, revision = self._require_draft(session, tool_id)
            if tool.is_builtin:
                raise ServiceError(
                    "BUILTIN_TOOL_IMMUTABLE", "系统内置工具不可修改", status_code=409
                )
            if revision.id != draft_revision_id or revision.lock_version != expected_lock_version:
                raise ServiceError("TOOL_REVISION_CONFLICT", "工具草稿已变化", status_code=409)
            contract = ToolContract.model_validate(revision.contract_json)
            now = _now()
            revision.state = "PUBLISHED"
            revision.published_at = now
            revision.updated_at = now
            revision.validation_json = {"valid": True, "errors": [], "warnings": []}
            tool.current_draft_revision_id = None
            tool.current_published_revision_id = revision.id
            tool.status = "PUBLISHED"
            tool.row_version += 1
            tool.updated_at = now
            return self._revision_detail(revision, tool)

    def list_revisions(self, tool_id: str) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            tool = session.get(ToolDefinition, tool_id)
            if tool is None:
                raise not_found("tool", tool_id)
            rows = list(
                session.scalars(
                    select(ToolRevision)
                    .where(ToolRevision.tool_id == tool_id)
                    .order_by(ToolRevision.revision_no.desc())
                )
            )
            return [self._revision_detail(item, tool, include_contract=False) for item in rows]

    def get_revision(self, tool_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            tool = session.get(ToolDefinition, tool_id)
            revision = session.get(ToolRevision, revision_id)
            if tool is None:
                raise not_found("tool", tool_id)
            if revision is None or revision.tool_id != tool_id:
                raise not_found("tool_revision", revision_id)
            return self._revision_detail(revision, tool)

    def fork_revision(self, tool_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            tool = session.get(ToolDefinition, tool_id)
            if tool is None:
                raise not_found("tool", tool_id)
            if tool.is_builtin:
                raise ServiceError(
                    "BUILTIN_TOOL_IMMUTABLE", "请基于系统工具创建自定义工具", status_code=409
                )
            if tool.current_draft_revision_id:
                raise ServiceError("TOOL_DRAFT_EXISTS", "工具已有编辑中的草稿", status_code=409)
            source = session.get(ToolRevision, revision_id)
            if source is None or source.tool_id != tool_id or source.state != "PUBLISHED":
                raise not_found("tool_revision", revision_id)
            number = int(
                session.scalar(
                    select(func.max(ToolRevision.revision_no)).where(
                        ToolRevision.tool_id == tool_id
                    )
                )
                or 0
            ) + 1
            now = _now()
            draft = ToolRevision(
                id=str(uuid4()),
                tool_id=tool_id,
                revision_no=number,
                state="DRAFT",
                base_revision_id=source.id,
                schema_version=source.schema_version,
                contract_json=copy.deepcopy(source.contract_json),
                contract_hash=source.contract_hash,
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            tool.current_draft_revision_id = draft.id
            tool.status = "DRAFT"
            tool.row_version += 1
            tool.updated_at = now
            return self._revision_detail(draft, tool)

    @staticmethod
    def _require_draft(
        session: Session, tool_id: str
    ) -> tuple[ToolDefinition, ToolRevision]:
        tool = session.get(ToolDefinition, tool_id)
        if tool is None:
            raise not_found("tool", tool_id)
        revision = (
            session.get(ToolRevision, tool.current_draft_revision_id)
            if tool.current_draft_revision_id
            else None
        )
        if revision is None or revision.state != "DRAFT":
            raise ServiceError("TOOL_DRAFT_UNAVAILABLE", "工具没有可编辑草稿", status_code=409)
        return tool, revision

    @staticmethod
    def _summary(revision: ToolRevision | None) -> dict[str, Any] | None:
        if revision is None:
            return None
        return {
            "id": revision.id,
            "revision_no": revision.revision_no,
            "state": revision.state,
            "schema_version": revision.schema_version,
            "contract_hash": revision.contract_hash,
            "lock_version": revision.lock_version,
            "updated_at": revision.updated_at.isoformat(),
            "published_at": revision.published_at.isoformat() if revision.published_at else None,
        }

    def _detail(self, session: Session, tool: ToolDefinition) -> dict[str, Any]:
        draft = session.get(ToolRevision, tool.current_draft_revision_id) if tool.current_draft_revision_id else None
        published = session.get(ToolRevision, tool.current_published_revision_id) if tool.current_published_revision_id else None
        active = draft or published
        return {
            "id": tool.id,
            "tool_key": tool.tool_key,
            "name": tool.name,
            "description": tool.description,
            "tool_kind": tool.tool_kind,
            "status": tool.status,
            "is_builtin": tool.is_builtin,
            "row_version": tool.row_version,
            "current_draft": self._summary(draft),
            "current_published": self._summary(published),
            "active_contract": copy.deepcopy(active.contract_json) if active else None,
            "created_at": tool.created_at.isoformat(),
            "updated_at": tool.updated_at.isoformat(),
        }

    def _revision_detail(
        self, revision: ToolRevision, tool: ToolDefinition, *, include_contract: bool = True
    ) -> dict[str, Any]:
        result = self._summary(revision) or {}
        result.update(
            {
                "tool_id": tool.id,
                "tool_key": tool.tool_key,
                "tool_name": tool.name,
                "tool_kind": tool.tool_kind,
                "base_revision_id": revision.base_revision_id,
                "validation": revision.validation_json,
                "readonly": revision.state == "PUBLISHED",
            }
        )
        if include_contract:
            result["contract"] = copy.deepcopy(revision.contract_json)
        return result



__all__ = ["ToolService"]
