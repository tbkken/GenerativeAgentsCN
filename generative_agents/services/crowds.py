"""Public Agent templates, versioned crowds, and experiment materialization."""

from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from math import ceil
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from generative_agents.config import AgentTemplateDefinition, canonical_json_bytes, make_builtin_definition
from generative_agents.config.schema import AgentDefinition, WorldConfig
from generative_agents.config.spatial import validate_agent_spatial
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    AgentTemplate,
    AgentTemplateRevision,
    CrowdRevision,
    CrowdRevisionMember,
    CrowdTemplate,
    ExperimentRevision,
)

from .errors import ServiceError, not_found


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_agent_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name.strip()).casefold()


def _make_key(name: str, prefix: str) -> str:
    ascii_key = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    stem = ascii_key[:48].strip("-") or prefix
    return f"{stem}-{uuid4().hex[:8]}"


def _document_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes({"value": payload})).hexdigest()


def _validate_agent_template_for_publish(definition: AgentTemplateDefinition) -> None:
    if any(value < 0 for value in definition.coord):
        raise ServiceError(
            "AGENT_INITIAL_POSITION_INVALID",
            "Agent 初始位置不能为负数",
            status_code=422,
        )
    issues = validate_agent_spatial(
        definition.spatial.address,
        definition.spatial.tree,
    )
    if issues:
        issue = issues[0]
        raise ServiceError(
            issue.code,
            issue.message,
            status_code=422,
            details=issue.details or None,
        )


def _template_from_agent(agent: AgentDefinition) -> AgentTemplateDefinition:
    payload = agent.model_dump(mode="json", exclude_none=False)
    return AgentTemplateDefinition.model_validate(payload)


class CrowdService:
    """Own public Agent identities and crowd membership snapshots."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_builtin_resources(self) -> dict[str, Any]:
        """Seed the bundled residents and version-up legacy spatial-less snapshots."""

        with self.database.session_factory.begin() as session:
            definition = make_builtin_definition(
                key="builtin-agent-catalog", name="斯坦福小镇居民"
            )
            existing_crowd = session.scalar(
                select(CrowdTemplate).where(CrowdTemplate.crowd_key == "stanford-town-residents")
            )
            if existing_crowd is not None:
                return self._upgrade_builtin_agent_spatial(
                    session, existing_crowd, definition.agents
                )
            now = _utc_now()
            agent_revisions: list[AgentTemplateRevision] = []
            for source_agent in definition.agents:
                reusable = _template_from_agent(source_agent)
                normalized_name = normalize_agent_name(reusable.name)
                existing_agent = session.scalar(
                    select(AgentTemplate).where(
                        AgentTemplate.normalized_name == normalized_name
                    )
                )
                if existing_agent is not None:
                    if not existing_agent.current_published_revision_id:
                        raise ServiceError(
                            "BUILTIN_AGENT_CONFLICT",
                            f"系统 Agent“{reusable.name}”存在同名未发布模板",
                            status_code=409,
                        )
                    revision = session.get(
                        AgentTemplateRevision,
                        existing_agent.current_published_revision_id,
                    )
                    if revision is None:
                        raise ServiceError(
                            "BUILTIN_AGENT_CONFLICT",
                            f"系统 Agent“{reusable.name}”缺少已发布版本",
                            status_code=409,
                        )
                    agent_revisions.append(revision)
                    continue
                payload = reusable.model_dump(mode="json", exclude_none=False)
                agent = AgentTemplate(
                    id=str(uuid4()),
                    agent_key=reusable.agent_key,
                    name=reusable.name,
                    normalized_name=normalized_name,
                    description=f"系统内置 Agent：{reusable.name}",
                    status="PUBLISHED",
                    is_builtin=True,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(agent)
                session.flush()
                revision = AgentTemplateRevision(
                    id=str(uuid4()),
                    agent_id=agent.id,
                    revision_no=1,
                    state="PUBLISHED",
                    schema_version=1,
                    definition_json=payload,
                    definition_hash=_document_hash(payload),
                    validation_json={"valid": True, "errors": [], "warnings": []},
                    lock_version=1,
                    created_at=now,
                    updated_at=now,
                    published_at=now,
                )
                session.add(revision)
                session.flush()
                agent.current_published_revision_id = revision.id
                agent_revisions.append(revision)
            crowd = CrowdTemplate(
                id=str(uuid4()),
                crowd_key="stanford-town-residents",
                name="斯坦福小镇居民",
                description="系统内置的 25 位斯坦福小镇居民。",
                status="PUBLISHED",
                is_builtin=True,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(crowd)
            session.flush()
            member_ids = [item.id for item in agent_revisions]
            revision = CrowdRevision(
                id=str(uuid4()),
                crowd_id=crowd.id,
                revision_no=1,
                state="PUBLISHED",
                membership_hash=_document_hash(member_ids),
                validation_json={"valid": True, "errors": [], "warnings": []},
                lock_version=1,
                created_at=now,
                updated_at=now,
                published_at=now,
            )
            session.add(revision)
            session.flush()
            self._replace_members(session, crowd, revision, agent_revisions, now)
            crowd.current_published_revision_id = revision.id
            return self._crowd_detail(session, crowd)

    def _upgrade_builtin_agent_spatial(
        self,
        session: Session,
        crowd: CrowdTemplate,
        source_agents: list[AgentDefinition],
    ) -> dict[str, Any]:
        """Create immutable replacement revisions for legacy built-ins missing spatial data."""

        now = _utc_now()
        changed = False
        agent_revisions: list[AgentTemplateRevision] = []
        for source_agent in source_agents:
            reusable = _template_from_agent(source_agent)
            agent = session.scalar(
                select(AgentTemplate).where(
                    AgentTemplate.normalized_name == normalize_agent_name(reusable.name)
                )
            )
            if agent is None or not agent.is_builtin or not agent.current_published_revision_id:
                raise ServiceError(
                    "BUILTIN_AGENT_CONFLICT",
                    f"系统 Agent“{reusable.name}”缺少可升级的已发布模板",
                    status_code=409,
                )
            current = session.get(AgentTemplateRevision, agent.current_published_revision_id)
            if current is None:
                raise ServiceError(
                    "BUILTIN_AGENT_CONFLICT",
                    f"系统 Agent“{reusable.name}”缺少已发布版本",
                    status_code=409,
                )
            if "coord" not in current.definition_json or "spatial" not in current.definition_json:
                payload = reusable.model_dump(mode="json", exclude_none=False)
                revision_no = int(
                    session.scalar(
                        select(func.max(AgentTemplateRevision.revision_no)).where(
                            AgentTemplateRevision.agent_id == agent.id
                        )
                    )
                    or 0
                ) + 1
                upgraded = AgentTemplateRevision(
                    id=str(uuid4()),
                    agent_id=agent.id,
                    revision_no=revision_no,
                    state="PUBLISHED",
                    base_revision_id=current.id,
                    schema_version=current.schema_version,
                    definition_json=payload,
                    definition_hash=_document_hash(payload),
                    validation_json={"valid": True, "errors": [], "warnings": []},
                    lock_version=1,
                    created_at=now,
                    updated_at=now,
                    published_at=now,
                )
                session.add(upgraded)
                session.flush()
                agent.current_published_revision_id = upgraded.id
                agent.row_version += 1
                agent.updated_at = now
                current = upgraded
                changed = True
            agent_revisions.append(current)

        if changed:
            current_crowd_revision_id = crowd.current_published_revision_id
            revision_no = int(
                session.scalar(
                    select(func.max(CrowdRevision.revision_no)).where(
                        CrowdRevision.crowd_id == crowd.id
                    )
                )
                or 0
            ) + 1
            member_ids = [item.id for item in agent_revisions]
            upgraded_crowd = CrowdRevision(
                id=str(uuid4()),
                crowd_id=crowd.id,
                revision_no=revision_no,
                state="PUBLISHED",
                base_revision_id=current_crowd_revision_id,
                membership_hash=_document_hash(member_ids),
                validation_json={"valid": True, "errors": [], "warnings": []},
                lock_version=1,
                created_at=now,
                updated_at=now,
                published_at=now,
            )
            session.add(upgraded_crowd)
            session.flush()
            self._replace_members(session, crowd, upgraded_crowd, agent_revisions, now)
            crowd.current_published_revision_id = upgraded_crowd.id
            crowd.row_version += 1
            crowd.updated_at = now
        return self._crowd_detail(session, crowd)

    def list_agents(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 500:
            raise ServiceError("INVALID_PAGINATION", "Agent 分页参数无效", status_code=422)
        normalized_status = status.upper() if status else None
        if normalized_status not in {None, "DRAFT", "PUBLISHED"}:
            raise ServiceError("INVALID_AGENT_STATUS", "Agent 状态筛选无效", status_code=422)
        with self.database.session_factory() as session:
            statement = select(AgentTemplate)
            count_statement = select(func.count()).select_from(AgentTemplate)
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                predicate = or_(
                    AgentTemplate.name.ilike(pattern),
                    AgentTemplate.agent_key.ilike(pattern),
                )
                statement = statement.where(predicate)
                count_statement = count_statement.where(predicate)
            if normalized_status:
                statement = statement.where(AgentTemplate.status == normalized_status)
                count_statement = count_statement.where(AgentTemplate.status == normalized_status)
            total = int(session.scalar(count_statement) or 0)
            rows = list(
                session.scalars(
                    statement.order_by(
                        AgentTemplate.is_builtin.desc(),
                        AgentTemplate.name,
                        AgentTemplate.id,
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return {
                "items": [self._agent_detail(session, item) for item in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, ceil(total / page_size)),
            }

    def create_agent(
        self,
        *,
        definition: AgentTemplateDefinition | dict[str, Any],
        description: str = "",
        agent_key: str | None = None,
    ) -> dict[str, Any]:
        definition = AgentTemplateDefinition.model_validate(definition)
        normalized_name = normalize_agent_name(definition.name)
        stable_key = agent_key.strip() if agent_key else _make_key(definition.name, "agent")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", stable_key):
            raise ServiceError(
                "INVALID_AGENT_KEY",
                "Agent 稳定键必须由小写字母、数字和连字符组成",
                status_code=422,
            )
        payload = definition.model_dump(mode="json", exclude_none=False)
        with self.database.session_factory.begin() as session:
            if session.scalar(
                select(AgentTemplate.id).where(
                    or_(
                        AgentTemplate.agent_key == stable_key,
                        AgentTemplate.normalized_name == normalized_name,
                    )
                )
            ):
                raise ServiceError(
                    "AGENT_NAME_CONFLICT",
                    "Agent 名称或稳定键已被使用",
                    status_code=409,
                )
            now = _utc_now()
            agent = AgentTemplate(
                id=str(uuid4()),
                agent_key=stable_key,
                name=definition.name,
                normalized_name=normalized_name,
                description=description.strip()[:10_000],
                status="DRAFT",
                is_builtin=False,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(agent)
            session.flush()
            revision = AgentTemplateRevision(
                id=str(uuid4()),
                agent_id=agent.id,
                revision_no=1,
                state="DRAFT",
                schema_version=1,
                definition_json=payload,
                definition_hash=_document_hash(payload),
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(revision)
            session.flush()
            agent.current_draft_revision_id = revision.id
            return self._agent_detail(session, agent)

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            agent = session.get(AgentTemplate, agent_id)
            if agent is None:
                raise not_found("agent_template", agent_id)
            return self._agent_detail(session, agent)

    def get_agent_draft(self, agent_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            agent, revision = self._require_agent_draft(session, agent_id)
            return self._agent_revision_detail(agent, revision)

    def get_agent_revision(self, agent_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            agent, revision = self._require_agent_revision(session, agent_id, revision_id)
            return self._agent_revision_detail(agent, revision)

    def list_agent_revisions(self, agent_id: str) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            if session.get(AgentTemplate, agent_id) is None:
                raise not_found("agent_template", agent_id)
            rows = list(
                session.scalars(
                    select(AgentTemplateRevision)
                    .where(AgentTemplateRevision.agent_id == agent_id)
                    .order_by(AgentTemplateRevision.revision_no.desc())
                )
            )
            return [self._agent_revision_summary(item) for item in rows]

    def update_agent_draft(
        self,
        agent_id: str,
        *,
        expected_lock_version: int,
        definition: AgentTemplateDefinition | dict[str, Any],
        description: str | None = None,
    ) -> dict[str, Any]:
        definition = AgentTemplateDefinition.model_validate(definition)
        payload = definition.model_dump(mode="json", exclude_none=False)
        normalized_name = normalize_agent_name(definition.name)
        with self.database.session_factory.begin() as session:
            agent, revision = self._require_agent_draft(session, agent_id)
            if agent.is_builtin:
                raise ServiceError(
                    "BUILTIN_AGENT_IMMUTABLE",
                    "系统 Agent 模板不可直接修改",
                    status_code=409,
                )
            if revision.lock_version != expected_lock_version:
                raise ServiceError(
                    "AGENT_REVISION_CONFLICT",
                    "Agent 草稿已被其他请求修改，请重新载入",
                    status_code=409,
                )
            conflict = session.scalar(
                select(AgentTemplate.id).where(
                    AgentTemplate.normalized_name == normalized_name,
                    AgentTemplate.id != agent.id,
                )
            )
            if conflict:
                raise ServiceError("AGENT_NAME_CONFLICT", "Agent 名称已被使用", status_code=409)
            now = _utc_now()
            revision.definition_json = payload
            revision.definition_hash = _document_hash(payload)
            revision.validation_json = None
            revision.lock_version += 1
            revision.updated_at = now
            if description is not None:
                agent.description = description.strip()[:10_000]
            agent.updated_at = now
            return self._agent_revision_detail(agent, revision)

    def publish_agent_draft(
        self, agent_id: str, *, draft_revision_id: str, expected_lock_version: int
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            agent, revision = self._require_agent_draft(session, agent_id)
            if revision.id != draft_revision_id or revision.lock_version != expected_lock_version:
                raise ServiceError(
                    "AGENT_REVISION_CONFLICT",
                    "Agent 草稿版本已变化，请重新载入",
                    status_code=409,
                )
            definition = AgentTemplateDefinition.model_validate(revision.definition_json)
            _validate_agent_template_for_publish(definition)
            normalized_name = normalize_agent_name(definition.name)
            conflict = session.scalar(
                select(AgentTemplate.id).where(
                    AgentTemplate.normalized_name == normalized_name,
                    AgentTemplate.id != agent.id,
                )
            )
            if conflict:
                raise ServiceError("AGENT_NAME_CONFLICT", "Agent 名称已被使用", status_code=409)
            now = _utc_now()
            revision.state = "PUBLISHED"
            revision.validation_json = {"valid": True, "errors": [], "warnings": []}
            revision.published_at = now
            revision.updated_at = now
            agent.name = definition.name
            agent.normalized_name = normalized_name
            agent.current_draft_revision_id = None
            agent.current_published_revision_id = revision.id
            agent.status = "PUBLISHED"
            agent.row_version += 1
            agent.updated_at = now
            return self._agent_revision_detail(agent, revision)

    def fork_agent_revision(self, agent_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            agent, source = self._require_agent_revision(session, agent_id, revision_id)
            if source.state != "PUBLISHED":
                raise not_found("agent_revision", revision_id)
            if agent.is_builtin:
                raise ServiceError(
                    "BUILTIN_AGENT_IMMUTABLE",
                    "系统 Agent 模板不可直接修改；请新建自定义 Agent",
                    status_code=409,
                )
            if agent.current_draft_revision_id:
                raise ServiceError("AGENT_DRAFT_EXISTS", "该 Agent 已有编辑中的草稿", status_code=409)
            number = int(
                session.scalar(
                    select(func.max(AgentTemplateRevision.revision_no)).where(
                        AgentTemplateRevision.agent_id == agent.id
                    )
                )
                or 0
            ) + 1
            now = _utc_now()
            draft = AgentTemplateRevision(
                id=str(uuid4()),
                agent_id=agent.id,
                revision_no=number,
                state="DRAFT",
                base_revision_id=source.id,
                schema_version=source.schema_version,
                definition_json=copy.deepcopy(source.definition_json),
                definition_hash=source.definition_hash,
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            agent.current_draft_revision_id = draft.id
            agent.status = "DRAFT"
            agent.row_version += 1
            agent.updated_at = now
            return self._agent_revision_detail(agent, draft)

    def create_crowd(
        self,
        *,
        name: str,
        description: str = "",
        agent_revision_ids: list[str] | None = None,
        source_revision_id: str | None = None,
        crowd_key: str | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ServiceError("INVALID_CROWD_NAME", "人群名称不能为空", status_code=422)
        stable_key = crowd_key.strip() if crowd_key else _make_key(name, "crowd")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", stable_key):
            raise ServiceError(
                "INVALID_CROWD_KEY",
                "人群稳定键必须由小写字母、数字和连字符组成",
                status_code=422,
            )
        with self.database.session_factory.begin() as session:
            if session.scalar(select(CrowdTemplate.id).where(CrowdTemplate.crowd_key == stable_key)):
                raise ServiceError("CROWD_KEY_CONFLICT", "人群稳定键已被使用", status_code=409)
            base_revision: CrowdRevision | None = None
            if source_revision_id:
                base_revision = session.get(CrowdRevision, source_revision_id)
                if base_revision is None or base_revision.state != "PUBLISHED":
                    raise not_found("crowd_revision", source_revision_id)
                members = self._member_revisions(session, base_revision.id)
            else:
                members = self._resolve_agent_revisions(session, agent_revision_ids or [])
            now = _utc_now()
            crowd = CrowdTemplate(
                id=str(uuid4()),
                crowd_key=stable_key,
                name=name,
                description=description.strip()[:10_000],
                status="DRAFT",
                is_builtin=False,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(crowd)
            session.flush()
            ids = [item.id for item in members]
            revision = CrowdRevision(
                id=str(uuid4()),
                crowd_id=crowd.id,
                revision_no=1,
                state="DRAFT",
                base_revision_id=base_revision.id if base_revision else None,
                membership_hash=_document_hash(ids),
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(revision)
            session.flush()
            self._replace_members(session, crowd, revision, members, now)
            crowd.current_draft_revision_id = revision.id
            return self._crowd_detail(session, crowd)

    def list_crowds(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 5,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ServiceError("INVALID_PAGINATION", "人群分页参数无效", status_code=422)
        normalized_status = status.upper() if status else None
        if normalized_status not in {None, "DRAFT", "PUBLISHED"}:
            raise ServiceError("INVALID_CROWD_STATUS", "人群状态筛选无效", status_code=422)
        with self.database.session_factory() as session:
            statement = select(CrowdTemplate)
            count_statement = select(func.count()).select_from(CrowdTemplate)
            status_statement = select(CrowdTemplate.status, func.count()).group_by(CrowdTemplate.status)
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                predicate = or_(CrowdTemplate.name.ilike(pattern), CrowdTemplate.crowd_key.ilike(pattern))
                statement = statement.where(predicate)
                count_statement = count_statement.where(predicate)
                status_statement = status_statement.where(predicate)
            counts = {"DRAFT": 0, "PUBLISHED": 0}
            for item_status, item_count in session.execute(status_statement):
                counts[item_status] = int(item_count)
            counts["ALL"] = sum(counts.values())
            if normalized_status:
                statement = statement.where(CrowdTemplate.status == normalized_status)
                count_statement = count_statement.where(CrowdTemplate.status == normalized_status)
            total = int(session.scalar(count_statement) or 0)
            rows = list(
                session.scalars(
                    statement.order_by(
                        CrowdTemplate.is_builtin.desc(),
                        CrowdTemplate.updated_at.desc(),
                        CrowdTemplate.id.desc(),
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return {
                "items": [self._crowd_detail(session, item) for item in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, ceil(total / page_size)),
                "status_counts": counts,
            }

    def get_crowd(self, crowd_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            crowd = session.get(CrowdTemplate, crowd_id)
            if crowd is None:
                raise not_found("crowd", crowd_id)
            return self._crowd_detail(session, crowd)

    def get_crowd_draft(self, crowd_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            crowd, revision = self._require_crowd_draft(session, crowd_id)
            return self._crowd_revision_detail(session, crowd, revision)

    def get_crowd_revision(self, crowd_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            crowd, revision = self._require_crowd_revision(session, crowd_id, revision_id)
            return self._crowd_revision_detail(session, crowd, revision)

    def list_crowd_revisions(self, crowd_id: str) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            if session.get(CrowdTemplate, crowd_id) is None:
                raise not_found("crowd", crowd_id)
            rows = list(
                session.scalars(
                    select(CrowdRevision)
                    .where(CrowdRevision.crowd_id == crowd_id)
                    .order_by(CrowdRevision.revision_no.desc())
                )
            )
            return [self._crowd_revision_summary(session, item) for item in rows]

    def update_crowd_draft(
        self,
        crowd_id: str,
        *,
        expected_lock_version: int,
        agent_revision_ids: list[str],
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            crowd, revision = self._require_crowd_draft(session, crowd_id)
            if crowd.is_builtin:
                raise ServiceError("BUILTIN_CROWD_IMMUTABLE", "系统人群不可直接修改", status_code=409)
            if revision.lock_version != expected_lock_version:
                raise ServiceError(
                    "CROWD_REVISION_CONFLICT",
                    "人群草稿已被其他请求修改，请重新载入",
                    status_code=409,
                )
            members = self._resolve_agent_revisions(session, agent_revision_ids)
            now = _utc_now()
            self._replace_members(session, crowd, revision, members, now)
            revision.membership_hash = _document_hash([item.id for item in members])
            revision.validation_json = None
            revision.lock_version += 1
            revision.updated_at = now
            if name is not None and name.strip():
                crowd.name = name.strip()[:120]
            if description is not None:
                crowd.description = description.strip()[:10_000]
            crowd.updated_at = now
            return self._crowd_revision_detail(session, crowd, revision)

    def publish_crowd_draft(
        self, crowd_id: str, *, draft_revision_id: str, expected_lock_version: int
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            crowd, revision = self._require_crowd_draft(session, crowd_id)
            if revision.id != draft_revision_id or revision.lock_version != expected_lock_version:
                raise ServiceError(
                    "CROWD_REVISION_CONFLICT",
                    "人群草稿版本已变化，请重新载入",
                    status_code=409,
                )
            members = self._member_revisions(session, revision.id)
            if not members:
                raise ServiceError(
                    "CROWD_EMPTY",
                    "人群至少需要一个已发布 Agent",
                    status_code=422,
                )
            now = _utc_now()
            revision.membership_hash = _document_hash([item.id for item in members])
            revision.validation_json = {"valid": True, "errors": [], "warnings": []}
            revision.state = "PUBLISHED"
            revision.published_at = now
            revision.updated_at = now
            crowd.current_draft_revision_id = None
            crowd.current_published_revision_id = revision.id
            crowd.status = "PUBLISHED"
            crowd.row_version += 1
            crowd.updated_at = now
            return self._crowd_revision_detail(session, crowd, revision)

    def fork_crowd_revision(self, crowd_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            crowd, source = self._require_crowd_revision(session, crowd_id, revision_id)
            if source.state != "PUBLISHED":
                raise not_found("crowd_revision", revision_id)
            if crowd.is_builtin:
                raise ServiceError(
                    "BUILTIN_CROWD_IMMUTABLE",
                    "系统人群不可直接修改；请新建人群并选择系统 Agent",
                    status_code=409,
                )
            if crowd.current_draft_revision_id:
                raise ServiceError("CROWD_DRAFT_EXISTS", "该人群已有编辑中的草稿", status_code=409)
            number = int(
                session.scalar(
                    select(func.max(CrowdRevision.revision_no)).where(
                        CrowdRevision.crowd_id == crowd.id
                    )
                )
                or 0
            ) + 1
            members = self._member_revisions(session, source.id)
            now = _utc_now()
            draft = CrowdRevision(
                id=str(uuid4()),
                crowd_id=crowd.id,
                revision_no=number,
                state="DRAFT",
                base_revision_id=source.id,
                membership_hash=source.membership_hash,
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            self._replace_members(session, crowd, draft, members, now)
            crowd.current_draft_revision_id = draft.id
            crowd.status = "DRAFT"
            crowd.row_version += 1
            crowd.updated_at = now
            return self._crowd_revision_detail(session, crowd, draft)

    @classmethod
    def materialize_agents_in_session(
        cls,
        session: Session,
        *,
        crowd_revision_ids: list[str],
        world: WorldConfig,
    ) -> tuple[list[AgentDefinition], dict[str, Any]]:
        """Union selected crowds by normalized Agent name and place them on the map."""

        if not crowd_revision_ids:
            raise ServiceError("CROWD_REQUIRED", "请至少选择一个已发布人群", status_code=422)
        walkable: list[tuple[int, int]] = []
        for tile in world.definition.get("tiles", []):
            if not isinstance(tile, dict) or tile.get("collision") is True:
                continue
            coord = tile.get("coord")
            if (
                isinstance(coord, list)
                and len(coord) == 2
                and all(isinstance(value, int) and value >= 0 for value in coord)
            ):
                walkable.append((coord[0], coord[1]))
        walkable = sorted(set(walkable), key=lambda item: (item[1], item[0]))
        if not walkable:
            raise ServiceError(
                "MAP_HAS_NO_WALKABLE_TILE",
                "所选地图没有可用于放置 Agent 的可通行位置",
                status_code=422,
            )
        selected: list[tuple[AgentTemplate, AgentTemplateRevision]] = []
        seen_names: set[str] = set()
        duplicate_names: list[str] = []
        input_count = 0
        crowd_ids: list[str] = []
        for revision_id in crowd_revision_ids:
            crowd_revision = session.get(CrowdRevision, revision_id)
            if crowd_revision is None or crowd_revision.state != "PUBLISHED":
                raise not_found("crowd_revision", revision_id)
            crowd_ids.append(crowd_revision.crowd_id)
            rows = list(
                session.scalars(
                    select(CrowdRevisionMember)
                    .where(CrowdRevisionMember.crowd_revision_id == revision_id)
                    .order_by(CrowdRevisionMember.position)
                )
            )
            for member in rows:
                input_count += 1
                agent = session.get(AgentTemplate, member.agent_id)
                agent_revision = session.get(AgentTemplateRevision, member.agent_revision_id)
                if (
                    agent is None
                    or agent_revision is None
                    or agent_revision.agent_id != agent.id
                    or agent_revision.state != "PUBLISHED"
                ):
                    raise ServiceError(
                        "CROWD_AGENT_REVISION_UNAVAILABLE",
                        "人群引用的 Agent 版本不可用",
                        status_code=409,
                        details={"crowd_revision_id": revision_id, "agent_id": member.agent_id},
                    )
                template = AgentTemplateDefinition.model_validate(agent_revision.definition_json)
                normalized = normalize_agent_name(template.name)
                if normalized in seen_names:
                    if template.name not in duplicate_names:
                        duplicate_names.append(template.name)
                    continue
                seen_names.add(normalized)
                selected.append((agent, agent_revision))
        if len(selected) > len(walkable):
            raise ServiceError(
                "MAP_AGENT_CAPACITY_EXCEEDED",
                "所选人群的去重 Agent 数超过地图可通行位置数量",
                status_code=422,
                details={"agent_count": len(selected), "walkable_tiles": len(walkable)},
            )
        result: list[AgentDefinition] = []
        imported_revision_ids: list[str] = []
        occupied: set[tuple[int, int]] = set()
        for _agent, revision in selected:
            template = AgentTemplateDefinition.model_validate(revision.definition_json)
            payload = template.model_dump(mode="json", exclude_none=False)
            preferred = tuple(template.coord)
            if preferred not in walkable or preferred in occupied:
                preferred = next(coord for coord in walkable if coord not in occupied)
            occupied.add(preferred)
            payload["coord"] = list(preferred)
            result.append(AgentDefinition.model_validate(payload))
            imported_revision_ids.append(revision.id)
        return result, {
            "crowd_revision_ids": list(crowd_revision_ids),
            "crowd_ids": crowd_ids,
            "crowd_agent_input_count": input_count,
            "crowd_agent_count": len(result),
            "crowd_agent_duplicate_names": duplicate_names,
            "agent_template_revision_ids": imported_revision_ids,
        }

    @staticmethod
    def _resolve_agent_revisions(
        session: Session, revision_ids: list[str]
    ) -> list[AgentTemplateRevision]:
        resolved: list[AgentTemplateRevision] = []
        seen_agents: set[str] = set()
        for revision_id in revision_ids:
            revision = session.get(AgentTemplateRevision, revision_id)
            if revision is None or revision.state != "PUBLISHED":
                raise not_found("agent_revision", revision_id)
            if revision.agent_id in seen_agents:
                continue
            seen_agents.add(revision.agent_id)
            resolved.append(revision)
        return resolved

    @staticmethod
    def _replace_members(
        session: Session,
        crowd: CrowdTemplate,
        revision: CrowdRevision,
        agents: list[AgentTemplateRevision],
        now: datetime,
    ) -> None:
        session.execute(
            delete(CrowdRevisionMember).where(
                CrowdRevisionMember.crowd_revision_id == revision.id
            )
        )
        for position, agent_revision in enumerate(agents):
            session.add(
                CrowdRevisionMember(
                    id=str(uuid4()),
                    crowd_id=crowd.id,
                    crowd_revision_id=revision.id,
                    agent_id=agent_revision.agent_id,
                    agent_revision_id=agent_revision.id,
                    position=position,
                    created_at=now,
                )
            )
        session.flush()

    @staticmethod
    def _member_revisions(session: Session, revision_id: str) -> list[AgentTemplateRevision]:
        members = list(
            session.scalars(
                select(CrowdRevisionMember)
                .where(CrowdRevisionMember.crowd_revision_id == revision_id)
                .order_by(CrowdRevisionMember.position)
            )
        )
        result: list[AgentTemplateRevision] = []
        for member in members:
            revision = session.get(AgentTemplateRevision, member.agent_revision_id)
            if revision is None or revision.state != "PUBLISHED":
                raise ServiceError(
                    "CROWD_AGENT_REVISION_UNAVAILABLE",
                    "人群引用的 Agent 版本不可用",
                    status_code=409,
                )
            result.append(revision)
        return result

    @staticmethod
    def _require_agent_draft(
        session: Session, agent_id: str
    ) -> tuple[AgentTemplate, AgentTemplateRevision]:
        agent = session.get(AgentTemplate, agent_id)
        if agent is None:
            raise not_found("agent_template", agent_id)
        revision = (
            session.get(AgentTemplateRevision, agent.current_draft_revision_id)
            if agent.current_draft_revision_id
            else None
        )
        if revision is None or revision.state != "DRAFT":
            raise ServiceError("AGENT_DRAFT_UNAVAILABLE", "该 Agent 当前没有可编辑草稿", status_code=409)
        return agent, revision

    @staticmethod
    def _require_agent_revision(
        session: Session, agent_id: str, revision_id: str
    ) -> tuple[AgentTemplate, AgentTemplateRevision]:
        agent = session.get(AgentTemplate, agent_id)
        revision = session.get(AgentTemplateRevision, revision_id)
        if agent is None:
            raise not_found("agent_template", agent_id)
        if revision is None or revision.agent_id != agent.id:
            raise not_found("agent_revision", revision_id)
        return agent, revision

    @staticmethod
    def _require_crowd_draft(
        session: Session, crowd_id: str
    ) -> tuple[CrowdTemplate, CrowdRevision]:
        crowd = session.get(CrowdTemplate, crowd_id)
        if crowd is None:
            raise not_found("crowd", crowd_id)
        revision = (
            session.get(CrowdRevision, crowd.current_draft_revision_id)
            if crowd.current_draft_revision_id
            else None
        )
        if revision is None or revision.state != "DRAFT":
            raise ServiceError("CROWD_DRAFT_UNAVAILABLE", "该人群当前没有可编辑草稿", status_code=409)
        return crowd, revision

    @staticmethod
    def _require_crowd_revision(
        session: Session, crowd_id: str, revision_id: str
    ) -> tuple[CrowdTemplate, CrowdRevision]:
        crowd = session.get(CrowdTemplate, crowd_id)
        revision = session.get(CrowdRevision, revision_id)
        if crowd is None:
            raise not_found("crowd", crowd_id)
        if revision is None or revision.crowd_id != crowd.id:
            raise not_found("crowd_revision", revision_id)
        return crowd, revision

    def _agent_detail(self, session: Session, agent: AgentTemplate) -> dict[str, Any]:
        draft = (
            session.get(AgentTemplateRevision, agent.current_draft_revision_id)
            if agent.current_draft_revision_id
            else None
        )
        published = (
            session.get(AgentTemplateRevision, agent.current_published_revision_id)
            if agent.current_published_revision_id
            else None
        )
        crowd_count = int(
            session.scalar(
                select(func.count(func.distinct(CrowdRevisionMember.crowd_id))).where(
                    CrowdRevisionMember.agent_id == agent.id
                )
            )
            or 0
        )
        return {
            "id": agent.id,
            "agent_key": agent.agent_key,
            "name": agent.name,
            "description": agent.description,
            "status": agent.status,
            "is_builtin": agent.is_builtin,
            "row_version": agent.row_version,
            "current_draft": self._agent_revision_summary(draft),
            "current_published": self._agent_revision_summary(published),
            "crowd_count": crowd_count,
            "created_at": agent.created_at.isoformat(),
            "updated_at": agent.updated_at.isoformat(),
        }

    @staticmethod
    def _agent_revision_summary(revision: AgentTemplateRevision | None) -> dict[str, Any] | None:
        if revision is None:
            return None
        definition = AgentTemplateDefinition.model_validate(revision.definition_json)
        return {
            "id": revision.id,
            "agent_id": revision.agent_id,
            "revision_no": revision.revision_no,
            "state": revision.state,
            "definition_hash": revision.definition_hash,
            "lock_version": revision.lock_version,
            "name": definition.name,
            "published_at": revision.published_at.isoformat() if revision.published_at else None,
            "updated_at": revision.updated_at.isoformat(),
        }

    def _agent_revision_detail(
        self, agent: AgentTemplate, revision: AgentTemplateRevision
    ) -> dict[str, Any]:
        return {
            **self._agent_revision_summary(revision),
            "agent_key": agent.agent_key,
            "description": agent.description,
            "is_builtin": agent.is_builtin,
            "definition": copy.deepcopy(revision.definition_json),
            "validation": copy.deepcopy(revision.validation_json),
        }

    def _crowd_detail(self, session: Session, crowd: CrowdTemplate) -> dict[str, Any]:
        draft = (
            session.get(CrowdRevision, crowd.current_draft_revision_id)
            if crowd.current_draft_revision_id
            else None
        )
        published = (
            session.get(CrowdRevision, crowd.current_published_revision_id)
            if crowd.current_published_revision_id
            else None
        )
        selected = draft or published
        return {
            "id": crowd.id,
            "crowd_key": crowd.crowd_key,
            "name": crowd.name,
            "description": crowd.description,
            "status": crowd.status,
            "is_builtin": crowd.is_builtin,
            "row_version": crowd.row_version,
            "current_draft": self._crowd_revision_summary(session, draft),
            "current_published": self._crowd_revision_summary(session, published),
            "agent_count": self._crowd_member_count(session, selected.id) if selected else 0,
            "usage_count": self._crowd_usage_count(session, crowd.id),
            "created_at": crowd.created_at.isoformat(),
            "updated_at": crowd.updated_at.isoformat(),
        }

    @staticmethod
    def _crowd_member_count(session: Session, revision_id: str) -> int:
        return int(
            session.scalar(
                select(func.count()).select_from(CrowdRevisionMember).where(
                    CrowdRevisionMember.crowd_revision_id == revision_id
                )
            )
            or 0
        )

    @staticmethod
    def _crowd_usage_count(session: Session, crowd_id: str) -> int:
        count = 0
        for provenance in session.scalars(select(ExperimentRevision.provenance_json)):
            if crowd_id in (provenance or {}).get("crowd_ids", []):
                count += 1
        return count

    def _crowd_revision_summary(
        self, session: Session, revision: CrowdRevision | None
    ) -> dict[str, Any] | None:
        if revision is None:
            return None
        return {
            "id": revision.id,
            "crowd_id": revision.crowd_id,
            "revision_no": revision.revision_no,
            "state": revision.state,
            "membership_hash": revision.membership_hash,
            "lock_version": revision.lock_version,
            "agent_count": self._crowd_member_count(session, revision.id),
            "published_at": revision.published_at.isoformat() if revision.published_at else None,
            "updated_at": revision.updated_at.isoformat(),
        }

    def _crowd_revision_detail(
        self, session: Session, crowd: CrowdTemplate, revision: CrowdRevision
    ) -> dict[str, Any]:
        rows = list(
            session.scalars(
                select(CrowdRevisionMember)
                .where(CrowdRevisionMember.crowd_revision_id == revision.id)
                .order_by(CrowdRevisionMember.position)
            )
        )
        members: list[dict[str, Any]] = []
        for row in rows:
            agent = session.get(AgentTemplate, row.agent_id)
            agent_revision = session.get(AgentTemplateRevision, row.agent_revision_id)
            if agent is None or agent_revision is None:
                continue
            members.append(
                {
                    "position": row.position,
                    "agent_id": agent.id,
                    "agent_key": agent.agent_key,
                    "name": AgentTemplateDefinition.model_validate(agent_revision.definition_json).name,
                    "is_builtin": agent.is_builtin,
                    "agent_revision": self._agent_revision_summary(agent_revision),
                }
            )
        return {
            **self._crowd_revision_summary(session, revision),
            "crowd_key": crowd.crowd_key,
            "name": crowd.name,
            "description": crowd.description,
            "is_builtin": crowd.is_builtin,
            "members": members,
            "validation": copy.deepcopy(revision.validation_json),
        }
