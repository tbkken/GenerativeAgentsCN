"""Experiment draft, validation and immutable publication service."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from math import ceil
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from generative_agents.config import (
    ExperimentDefinition,
    definition_hash,
    make_builtin_definition,
    validate_for_publish,
)
from generative_agents.config.schema import make_blank_definition
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    BuiltinCatalogSnapshot,
    Experiment,
    ExperimentRevision,
    Run,
    Secret,
)

from .errors import ServiceError, not_found

SourceType = Literal["BUILTIN_DEFAULT", "BLANK", "REVISION"]
DefinitionFactory = Callable[[str, str, str], ExperimentDefinition]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_key(name: str) -> str:
    ascii_key = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    prefix = ascii_key[:48].strip("-") or "experiment"
    return f"{prefix}-{uuid4().hex[:8]}"


def _default_definition_factory(key: str, name: str, goal: str) -> ExperimentDefinition:
    return make_builtin_definition(key=key, name=name, goal=goal)


class ExperimentService:
    """Owns short, synchronous SQLAlchemy transactions for experiment definitions."""

    def __init__(
        self,
        database: Database,
        *,
        builtin_definition_factory: DefinitionFactory | None = None,
    ) -> None:
        self.database = database
        self._builtin_factory = builtin_definition_factory

    def _builtin_definition(
        self, session: Session, *, key: str, name: str, goal: str
    ) -> tuple[ExperimentDefinition, dict[str, Any]]:
        if self._builtin_factory is not None:
            return self._builtin_factory(key, name, goal), {"source_type": "BUILTIN_DEFAULT"}
        snapshot = session.scalar(
            select(BuiltinCatalogSnapshot)
            .order_by(BuiltinCatalogSnapshot.created_at.desc(), BuiltinCatalogSnapshot.id.desc())
            .limit(1)
        )
        if snapshot is None:
            return _default_definition_factory(key, name, goal), {
                "source_type": "BUILTIN_DEFAULT",
                "catalog_mode": "PACKAGE_FALLBACK",
            }
        definition = ExperimentDefinition.model_validate(snapshot.definition_json)
        return definition, {
            "source_type": "BUILTIN_DEFAULT",
            "catalog_snapshot_id": snapshot.id,
            "catalog_definition_hash": snapshot.definition_hash,
            "catalog_source_fingerprint": snapshot.source_fingerprint,
        }

    @staticmethod
    def _definition_for_new_owner(
        source: ExperimentDefinition, *, key: str, name: str, goal: str
    ) -> ExperimentDefinition:
        payload = source.model_dump(mode="json", exclude_none=False)
        payload["experiment"].update({"key": key, "name": name, "goal": goal})
        return ExperimentDefinition.model_validate(payload)

    def create_experiment(
        self,
        *,
        name: str,
        goal: str = "",
        source_type: SourceType = "BUILTIN_DEFAULT",
        source_revision_id: str | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ServiceError("INVALID_EXPERIMENT_NAME", "实验名称不能为空", status_code=422)
        key = _make_key(name)
        with self.database.session_factory.begin() as session:
            if source_type == "REVISION":
                if not source_revision_id:
                    raise ServiceError(
                        "SOURCE_REVISION_REQUIRED", "REVISION 来源必须提供 revision_id", status_code=422
                    )
                source_revision = session.get(ExperimentRevision, source_revision_id)
                if source_revision is None or source_revision.state != "PUBLISHED":
                    raise not_found("revision", source_revision_id)
                source_definition = ExperimentDefinition.model_validate(
                    source_revision.definition_json
                )
                provenance = {
                    "source_type": "REVISION",
                    "source_revision_id": source_revision_id,
                }
                base_revision_id = source_revision_id
            elif source_type == "BUILTIN_DEFAULT":
                source_definition, provenance = self._builtin_definition(
                    session, key=key, name=name, goal=goal
                )
                base_revision_id = None
            elif source_type == "BLANK":
                source_definition = make_blank_definition(key=key, name=name, goal=goal)
                provenance = {"source_type": source_type}
                base_revision_id = None
            else:
                raise ServiceError(
                    "INVALID_SOURCE_TYPE", "不支持的实验配置起点", status_code=422
                )

            definition = self._definition_for_new_owner(
                source_definition, key=key, name=name, goal=goal
            )
            digest = definition_hash(definition)
            now = _utc_now()
            experiment = Experiment(
                id=str(uuid4()),
                experiment_key=key,
                name=name,
                goal=goal,
                status="DRAFT",
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(experiment)
            session.flush()
            revision = ExperimentRevision(
                id=str(uuid4()),
                experiment_id=experiment.id,
                revision_no=1,
                state="DRAFT",
                base_revision_id=base_revision_id,
                schema_version=definition.schema_version,
                definition_json=definition.model_dump(mode="json", exclude_none=False),
                definition_hash=digest,
                validation_json=None,
                validated_hash=None,
                provenance_json=provenance,
                snapshot_complete=False,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(revision)
            session.flush()
            experiment.current_draft_revision_id = revision.id
            return self._experiment_detail(session, experiment)

    def duplicate_experiment(
        self,
        experiment_id: str,
        *,
        revision_id: str | None = None,
        name: str | None = None,
        goal: str | None = None,
    ) -> dict[str, Any]:
        """Deep-copy one Draft or explicit published Revision into a new owner."""

        with self.database.session_factory.begin() as session:
            source_experiment = session.get(Experiment, experiment_id)
            if source_experiment is None:
                raise not_found("experiment", experiment_id)
            selected_id = revision_id or source_experiment.current_draft_revision_id or source_experiment.current_published_revision_id
            source_revision = session.get(ExperimentRevision, selected_id) if selected_id else None
            if source_revision is None or source_revision.experiment_id != experiment_id:
                raise not_found("revision", selected_id or "")
            if revision_id and source_revision.state != "PUBLISHED":
                raise ServiceError(
                    "SOURCE_REVISION_NOT_PUBLISHED",
                    "显式复制来源必须是已发布 Revision",
                    status_code=409,
                )

            duplicate_name = (name or f"{source_experiment.name} · 副本").strip()
            duplicate_goal = source_experiment.goal if goal is None else goal
            if not duplicate_name:
                raise ServiceError("INVALID_EXPERIMENT_NAME", "实验名称不能为空", status_code=422)
            key = _make_key(duplicate_name)
            source_definition = ExperimentDefinition.model_validate(source_revision.definition_json)
            definition = self._definition_for_new_owner(
                source_definition,
                key=key,
                name=duplicate_name,
                goal=duplicate_goal,
            )
            digest = definition_hash(definition)
            now = _utc_now()
            experiment = Experiment(
                id=str(uuid4()),
                experiment_key=key,
                name=duplicate_name,
                goal=duplicate_goal,
                status="DRAFT",
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(experiment)
            session.flush()
            revision = ExperimentRevision(
                id=str(uuid4()),
                experiment_id=experiment.id,
                revision_no=1,
                state="DRAFT",
                base_revision_id=(source_revision.id if source_revision.state == "PUBLISHED" else None),
                schema_version=definition.schema_version,
                definition_json=definition.model_dump(mode="json", exclude_none=False),
                definition_hash=digest,
                validation_json=None,
                validated_hash=None,
                provenance_json={
                    "source_type": "DUPLICATE",
                    "source_experiment_id": source_experiment.id,
                    "source_revision_id": source_revision.id,
                    "source_revision_state": source_revision.state,
                },
                snapshot_complete=False,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(revision)
            session.flush()
            experiment.current_draft_revision_id = revision.id
            return self._experiment_detail(session, experiment)

    def list_experiments(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort: str = "-updated_at",
    ) -> dict[str, Any]:
        if page < 1 or page_size not in {10, 20, 50}:
            raise ServiceError("INVALID_PAGINATION", "分页参数无效", status_code=422)
        sort_field = sort.removeprefix("-")
        if sort_field not in {"updated_at", "created_at", "name", "status"}:
            raise ServiceError("INVALID_SORT", "排序字段无效", status_code=422)
        if status and status not in {
            "DRAFT",
            "QUEUED",
            "RUNNING",
            "PAUSED",
            "COMPLETED",
            "CANCELLED",
            "FAILED",
            "ABNORMAL",
        }:
            raise ServiceError("INVALID_STATUS", "实验状态无效", status_code=422)

        with self.database.session_factory() as session:
            filters = []
            if query and query.strip():
                term = f"%{query.strip()}%"
                filters.append(or_(Experiment.name.ilike(term), Experiment.goal.ilike(term)))

            counts_stmt = select(Experiment.status, func.count()).group_by(Experiment.status)
            if filters:
                counts_stmt = counts_stmt.where(*filters)
            grouped = dict(session.execute(counts_stmt).all())
            status_counts = {
                state: int(grouped.get(state, 0))
                for state in (
                    "QUEUED",
                    "RUNNING",
                    "DRAFT",
                    "PAUSED",
                    "COMPLETED",
                    "CANCELLED",
                    "FAILED",
                )
            }
            status_counts["ALL"] = sum(status_counts.values())

            page_filters = [*filters]
            if status:
                page_filters.append(
                    Experiment.status.in_({"FAILED", "CANCELLED"})
                    if status == "ABNORMAL"
                    else Experiment.status == status
                )
            total = session.scalar(
                select(func.count()).select_from(Experiment).where(*page_filters)
            ) or 0
            order_column = getattr(Experiment, sort_field)
            order_by = order_column.desc() if sort.startswith("-") else order_column.asc()
            experiments = session.scalars(
                select(Experiment)
                .where(*page_filters)
                .order_by(order_by, Experiment.id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()

            revision_ids = {
                revision_id
                for item in experiments
                for revision_id in (
                    item.current_draft_revision_id,
                    item.current_published_revision_id,
                )
                if revision_id
            }
            revisions = {
                revision.id: revision
                for revision in session.scalars(
                    select(ExperimentRevision).where(ExperimentRevision.id.in_(revision_ids))
                ).all()
            } if revision_ids else {}
            run_ids = {item.latest_run_id for item in experiments if item.latest_run_id}
            runs = {
                run.id: run
                for run in session.scalars(select(Run).where(Run.id.in_(run_ids))).all()
            } if run_ids else {}

            items = [self._list_item(item, revisions, runs) for item in experiments]
            return {
                "items": items,
                "status_counts": status_counts,
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": ceil(total / page_size) if total else 0,
            }

    @staticmethod
    def _list_item(
        experiment: Experiment,
        revisions: dict[str, ExperimentRevision],
        runs: dict[str, Run],
    ) -> dict[str, Any]:
        revision_id = (
            experiment.current_draft_revision_id or experiment.current_published_revision_id
        )
        revision = revisions.get(revision_id) if revision_id else None
        definition = (
            ExperimentDefinition.model_validate(revision.definition_json) if revision else None
        )
        latest_run = runs.get(experiment.latest_run_id) if experiment.latest_run_id else None
        core_parameters = None
        revision_no = None
        if definition and revision:
            core_parameters = {
                "start_time": definition.simulation.start_time,
                "stride_minutes": definition.simulation.stride_minutes,
                "max_steps": definition.simulation.max_steps,
                "agent_count": sum(agent.enabled for agent in definition.agents),
                "chat_model": definition.models.chat.resolved_model
                or definition.models.chat.model,
                "embedding_model": definition.models.embedding.resolved_model
                or definition.models.embedding.model,
                "world_name": definition.world.world_name,
                "random_seed": definition.simulation.random_seed,
            }
            revision_no = revision.revision_no
        return {
            "id": experiment.id,
            "experiment_key": experiment.experiment_key,
            "name": experiment.name,
            "goal": experiment.goal,
            "status": experiment.status,
            "revision_no": revision_no,
            "published_revision_id": experiment.current_published_revision_id,
            "core_parameters": core_parameters,
            "progress": (
                {
                    "completed_steps": latest_run.completed_steps,
                    "requested_steps": latest_run.requested_steps,
                }
                if latest_run
                else None
            ),
            "latest_run": (
                {
                    "run_id": latest_run.id,
                    "status": latest_run.status,
                    "queue_position": None,
                    "completed_steps": latest_run.completed_steps,
                    "requested_steps": latest_run.requested_steps,
                    "recoverable_step": latest_run.recoverable_step,
                }
                if latest_run
                else None
            ),
            "row_version": experiment.row_version,
            "updated_at": experiment.updated_at,
        }

    def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None:
                raise not_found("experiment", experiment_id)
            return self._experiment_detail(session, experiment)

    @staticmethod
    def _experiment_detail(session: Session, experiment: Experiment) -> dict[str, Any]:
        draft = (
            session.get(ExperimentRevision, experiment.current_draft_revision_id)
            if experiment.current_draft_revision_id
            else None
        )
        published = (
            session.get(ExperimentRevision, experiment.current_published_revision_id)
            if experiment.current_published_revision_id
            else None
        )
        latest_run = session.get(Run, experiment.latest_run_id) if experiment.latest_run_id else None
        run_count = session.scalar(
            select(func.count()).select_from(Run).where(Run.experiment_id == experiment.id)
        ) or 0
        return {
            "id": experiment.id,
            "experiment_key": experiment.experiment_key,
            "name": experiment.name,
            "goal": experiment.goal,
            "status": experiment.status,
            "row_version": experiment.row_version,
            "run_count": run_count,
            "current_draft": ExperimentService._revision_summary(draft),
            "current_published": ExperimentService._revision_summary(published),
            "latest_run": (
                {
                    "id": latest_run.id,
                    "status": latest_run.status,
                    "completed_steps": latest_run.completed_steps,
                    "requested_steps": latest_run.requested_steps,
                }
                if latest_run
                else None
            ),
            "created_at": experiment.created_at,
            "updated_at": experiment.updated_at,
        }

    @staticmethod
    def _revision_summary(revision: ExperimentRevision | None) -> dict[str, Any] | None:
        if revision is None:
            return None
        return {
            "id": revision.id,
            "revision_no": revision.revision_no,
            "state": revision.state,
            "definition_hash": revision.definition_hash,
            "lock_version": revision.lock_version,
            "published_at": revision.published_at,
        }

    def get_draft(self, experiment_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            _experiment, revision = self._require_draft(session, experiment_id)
            return self._revision_detail(revision)

    def update_metadata(
        self,
        experiment_id: str,
        *,
        expected_row_version: int,
        name: str,
        goal: str,
    ) -> dict[str, Any]:
        """Rename an experiment and its editable Draft in one transaction."""

        name = name.strip()
        if not name:
            raise ServiceError("INVALID_EXPERIMENT_NAME", "实验名称不能为空", status_code=422)
        if len(name) > 120 or len(goal) > 10_000:
            raise ServiceError("INVALID_EXPERIMENT_METADATA", "实验元数据过长", status_code=422)
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None:
                raise not_found("experiment", experiment_id)
            if experiment.row_version != expected_row_version:
                raise ServiceError(
                    "EXPERIMENT_CONFLICT",
                    "实验已被其他请求修改，请重新载入",
                    status_code=409,
                    details={
                        "expected_row_version": expected_row_version,
                        "actual_row_version": experiment.row_version,
                    },
                )
            if experiment.current_draft_revision_id:
                revision = session.get(
                    ExperimentRevision, experiment.current_draft_revision_id
                )
                if revision is None or revision.state != "DRAFT":
                    raise ServiceError(
                        "DRAFT_BASE_UNAVAILABLE", "草稿引用无效", status_code=409
                    )
                payload = ExperimentDefinition.model_validate(
                    revision.definition_json
                ).model_dump(mode="json", exclude_none=False)
                payload["experiment"]["name"] = name
                payload["experiment"]["goal"] = goal
                definition = ExperimentDefinition.model_validate(payload)
                revision.definition_json = definition.model_dump(
                    mode="json", exclude_none=False
                )
                revision.definition_hash = definition_hash(definition)
                revision.validation_json = None
                revision.validated_hash = None
                revision.lock_version += 1
                revision.updated_at = now
            experiment.name = name
            experiment.goal = goal
            experiment.row_version += 1
            experiment.updated_at = now
            session.flush()
            return self._experiment_detail(session, experiment)

    def update_draft(
        self,
        *,
        experiment_id: str,
        expected_lock_version: int,
        definition: ExperimentDefinition,
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            experiment, current = self._require_draft(session, experiment_id)
            if definition.experiment.key != experiment.experiment_key:
                raise ServiceError(
                    "EXPERIMENT_KEY_IMMUTABLE", "实验 key 不允许通过草稿修改", status_code=422
                )
            if (
                definition.experiment.name != experiment.name
                or definition.experiment.goal != experiment.goal
            ):
                raise ServiceError(
                    "EXPERIMENT_METADATA_MISMATCH",
                    "草稿中的实验名称和目标必须与实验容器一致",
                    status_code=422,
                )
            digest = definition_hash(definition)
            now = _utc_now()
            result = session.execute(
                update(ExperimentRevision)
                .where(
                    ExperimentRevision.id == current.id,
                    ExperimentRevision.state == "DRAFT",
                    ExperimentRevision.lock_version == expected_lock_version,
                )
                .values(
                    definition_json=definition.model_dump(mode="json", exclude_none=False),
                    definition_hash=digest,
                    schema_version=definition.schema_version,
                    validation_json=None,
                    validated_hash=None,
                    lock_version=ExperimentRevision.lock_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                actual = session.scalar(
                    select(ExperimentRevision.lock_version).where(
                        ExperimentRevision.id == current.id
                    )
                )
                raise ServiceError(
                    "REVISION_CONFLICT",
                    "草稿已被其他请求修改，请重新载入",
                    status_code=409,
                    details={
                        "expected_lock_version": expected_lock_version,
                        "actual_lock_version": actual,
                    },
                )
            experiment.updated_at = now
            session.flush()
            refreshed = session.get(ExperimentRevision, current.id)
            return self._revision_detail(refreshed)

    def patch_draft_section(
        self,
        *,
        experiment_id: str,
        section: str,
        expected_lock_version: int,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        if section not in {"simulation", "models", "behavior", "results", "world"}:
            raise ServiceError("INVALID_DRAFT_SECTION", "草稿区域无效", status_code=404)
        draft = self.get_draft(experiment_id)
        payload = draft["definition"]
        payload[section] = data
        definition = ExperimentDefinition.model_validate(payload)
        return self.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=expected_lock_version,
            definition=definition,
        )

    def put_draft_agent(
        self,
        experiment_id: str,
        agent_key: str,
        *,
        expected_lock_version: int,
        data: dict[str, Any],
        partial: bool = False,
    ) -> dict[str, Any]:
        draft = self.get_draft(experiment_id)
        payload = draft["definition"]
        agents = list(payload["agents"])
        existing_index = next(
            (index for index, item in enumerate(agents) if item["agent_key"] == agent_key),
            None,
        )
        if partial:
            if existing_index is None:
                raise not_found("agent", agent_key)
            replacement = {**agents[existing_index], **data, "agent_key": agent_key}
        else:
            replacement = {**data, "agent_key": agent_key}
        if existing_index is None:
            agents.append(replacement)
        else:
            agents[existing_index] = replacement
        payload["agents"] = agents
        return self.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=expected_lock_version,
            definition=ExperimentDefinition.model_validate(payload),
        )

    def delete_draft_agent(
        self,
        experiment_id: str,
        agent_key: str,
        *,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        draft = self.get_draft(experiment_id)
        payload = draft["definition"]
        agents = [item for item in payload["agents"] if item["agent_key"] != agent_key]
        if len(agents) == len(payload["agents"]):
            raise not_found("agent", agent_key)
        payload["agents"] = agents
        return self.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=expected_lock_version,
            definition=ExperimentDefinition.model_validate(payload),
        )

    def put_draft_prompt(
        self,
        experiment_id: str,
        prompt_key: str,
        *,
        expected_lock_version: int,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        draft = self.get_draft(experiment_id)
        payload = draft["definition"]
        payload["prompts"][prompt_key] = data
        return self.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=expected_lock_version,
            definition=ExperimentDefinition.model_validate(payload),
        )

    def restore_draft_prompt(
        self,
        experiment_id: str,
        prompt_key: str,
        *,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        draft = self.get_draft(experiment_id)
        if not draft["base_revision_id"]:
            raise ServiceError(
                "PROMPT_BASE_UNAVAILABLE",
                "当前草稿没有可恢复的发布版基线",
                status_code=409,
            )
        with self.database.session_factory() as session:
            base = session.get(ExperimentRevision, draft["base_revision_id"])
            if base is None or base.experiment_id != experiment_id:
                raise ServiceError(
                    "PROMPT_BASE_UNAVAILABLE", "Prompt 基线不存在", status_code=409
                )
            prompt = (base.definition_json.get("prompts") or {}).get(prompt_key)
        if prompt is None:
            raise not_found("prompt", prompt_key)
        return self.put_draft_prompt(
            experiment_id,
            prompt_key,
            expected_lock_version=expected_lock_version,
            data=prompt,
        )

    def validate_draft(self, experiment_id: str) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            _experiment, revision = self._require_draft(session, experiment_id)
            definition = ExperimentDefinition.model_validate(revision.definition_json)
            refs = set(session.scalars(select(Secret.id)).all())
            report = validate_for_publish(definition, existing_secret_refs=refs)
            revision.validation_json = report.model_dump(mode="json")
            revision.validated_hash = report.definition_hash
            revision.updated_at = _utc_now()
            session.flush()
            return report.model_dump(mode="json") | {"valid": report.valid}

    def publish_draft(
        self,
        *,
        experiment_id: str,
        draft_revision_id: str,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        """Publish for integration/tests; run orchestration should call the in-session variant."""

        with self.database.session_factory.begin() as session:
            revision = self.publish_draft_in_session(
                session,
                experiment_id=experiment_id,
                draft_revision_id=draft_revision_id,
                expected_lock_version=expected_lock_version,
            )
            return self._revision_detail(revision)

    def publish_draft_in_session(
        self,
        session: Session,
        *,
        experiment_id: str,
        draft_revision_id: str,
        expected_lock_version: int,
    ) -> ExperimentRevision:
        """Publish inside a caller transaction so publish-and-run remains atomic."""

        experiment, revision = self._require_draft(session, experiment_id)
        if revision.id != draft_revision_id or revision.lock_version != expected_lock_version:
            raise ServiceError(
                "REVISION_CONFLICT",
                "草稿已被其他请求修改，请重新载入",
                status_code=409,
                details={
                    "expected_revision_id": draft_revision_id,
                    "actual_revision_id": revision.id,
                    "expected_lock_version": expected_lock_version,
                    "actual_lock_version": revision.lock_version,
                },
            )
        payload = revision.definition_json
        for purpose in ("chat", "embedding"):
            model = payload["models"][purpose]
            if model["model"].casefold() != "auto":
                model["resolved_model"] = model["model"]
        definition = ExperimentDefinition.model_validate(payload)
        refs = set(session.scalars(select(Secret.id)).all())
        report = validate_for_publish(definition, existing_secret_refs=refs)
        if not report.valid:
            raise ServiceError(
                "CONFIG_VALIDATION_FAILED",
                "实验配置未通过发布校验",
                status_code=422,
                details=report.model_dump(mode="json"),
            )
        now = _utc_now()
        revision.definition_json = definition.model_dump(mode="json", exclude_none=False)
        revision.definition_hash = report.definition_hash
        revision.validation_json = report.model_dump(mode="json")
        revision.validated_hash = report.definition_hash
        revision.state = "PUBLISHED"
        revision.snapshot_complete = True
        revision.published_at = now
        revision.updated_at = now
        experiment.current_draft_revision_id = None
        experiment.current_published_revision_id = revision.id
        experiment.updated_at = now
        session.flush()
        return revision

    def list_revisions(self, experiment_id: str) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            if session.get(Experiment, experiment_id) is None:
                raise not_found("experiment", experiment_id)
            revisions = session.scalars(
                select(ExperimentRevision)
                .where(ExperimentRevision.experiment_id == experiment_id)
                .order_by(ExperimentRevision.revision_no.desc())
            ).all()
            return [self._revision_summary(revision) for revision in revisions]

    def get_revision(self, experiment_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            revision = session.get(ExperimentRevision, revision_id)
            if revision is None or revision.experiment_id != experiment_id:
                raise not_found("revision", revision_id)
            return self._revision_detail(revision)

    def fork_revision(self, experiment_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None:
                raise not_found("experiment", experiment_id)
            if experiment.current_draft_revision_id:
                raise ServiceError(
                    "DRAFT_ALREADY_EXISTS",
                    "该实验已经有一个草稿",
                    status_code=409,
                    details={"draft_revision_id": experiment.current_draft_revision_id},
                )
            source = session.get(ExperimentRevision, revision_id)
            if source is None or source.experiment_id != experiment_id or source.state != "PUBLISHED":
                raise not_found("revision", revision_id)
            next_no = (
                session.scalar(
                    select(func.max(ExperimentRevision.revision_no)).where(
                        ExperimentRevision.experiment_id == experiment_id
                    )
                )
                or 0
            ) + 1
            now = _utc_now()
            draft = ExperimentRevision(
                id=str(uuid4()),
                experiment_id=experiment_id,
                revision_no=next_no,
                state="DRAFT",
                base_revision_id=source.id,
                schema_version=source.schema_version,
                definition_json=ExperimentDefinition.model_validate(source.definition_json)
                .model_copy(deep=True)
                .model_dump(mode="json", exclude_none=False),
                definition_hash=source.definition_hash,
                validation_json=source.validation_json,
                validated_hash=source.validated_hash,
                provenance_json={"source_type": "FORK", "source_revision_id": source.id},
                snapshot_complete=False,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            experiment.current_draft_revision_id = draft.id
            experiment.status = "DRAFT"
            experiment.updated_at = now
            return self._revision_detail(draft)

    @staticmethod
    def _require_draft(
        session: Session, experiment_id: str
    ) -> tuple[Experiment, ExperimentRevision]:
        experiment = session.get(Experiment, experiment_id)
        if experiment is None:
            raise not_found("experiment", experiment_id)
        if not experiment.current_draft_revision_id:
            raise ServiceError(
                "DRAFT_BASE_UNAVAILABLE", "该实验当前没有可编辑草稿", status_code=409
            )
        revision = session.get(ExperimentRevision, experiment.current_draft_revision_id)
        if revision is None or revision.state != "DRAFT":
            raise ServiceError(
                "DRAFT_BASE_UNAVAILABLE", "草稿引用无效，需要执行数据库对账", status_code=409
            )
        return experiment, revision

    @staticmethod
    def _revision_detail(revision: ExperimentRevision | None) -> dict[str, Any]:
        if revision is None:
            raise RuntimeError("revision unexpectedly missing")
        return {
            "id": revision.id,
            "experiment_id": revision.experiment_id,
            "revision_no": revision.revision_no,
            "state": revision.state,
            "base_revision_id": revision.base_revision_id,
            "schema_version": revision.schema_version,
            "definition": revision.definition_json,
            "definition_hash": revision.definition_hash,
            "validation": revision.validation_json,
            "validated_hash": revision.validated_hash,
            "provenance": revision.provenance_json,
            "snapshot_complete": revision.snapshot_complete,
            "lock_version": revision.lock_version,
            "created_at": revision.created_at,
            "updated_at": revision.updated_at,
            "published_at": revision.published_at,
        }
