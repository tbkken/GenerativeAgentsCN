"""Experiment draft, validation and immutable publication service."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from math import ceil
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import Text, cast, exists, func, or_, select, update
from sqlalchemy.orm import Session

from generative_agents.config import (
    ExperimentDefinition,
    WorkflowDefinition,
    ValidationIssue,
    definition_hash,
    make_builtin_definition,
    validate_for_publish,
)
from generative_agents.config.schema import WorldOverlayConfig, make_blank_definition
from generative_agents.config.prompt_variables import canonicalize_prompt_payload
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    BrainRevision,
    BrainWorkflow,
    BuiltinCatalogSnapshot,
    Experiment,
    ExperimentComparisonGroup,
    ExperimentRevision,
    ExperimentRevisionCapability,
    ExperimentSavedView,
    Run,
    RunArtifact,
    RunResultSummary,
    Secret,
    WorldMap,
    WorldMapRevision,
)

from .errors import ServiceError, not_found

SourceType = Literal[
    "BUILTIN_DEFAULT",
    "BLANK",
    "REVISION",
]
DefinitionFactory = Callable[[str, str, str], ExperimentDefinition]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_key(name: str) -> str:
    ascii_key = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    prefix = ascii_key[:48].strip("-") or "experiment"
    return f"{prefix}-{uuid4().hex[:8]}"


def _default_definition_factory(key: str, name: str, goal: str) -> ExperimentDefinition:
    return make_builtin_definition(key=key, name=name, goal=goal)


def _normalize_tags(tags: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for raw in tags or []:
        value = raw.strip()
        if value and value.casefold() not in {item.casefold() for item in normalized}:
            normalized.append(value[:48])
    return normalized[:20]


def _flatten_document(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else key
            result.update(_flatten_document(value[key], path))
        return result
    if isinstance(value, list):
        return {prefix: value}
    return {prefix: value}


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
            definition = self._builtin_factory(key, name, goal)
            provenance = {"source_type": "BUILTIN_DEFAULT"}
        else:
            snapshot = session.scalar(
                select(BuiltinCatalogSnapshot)
                .order_by(
                    BuiltinCatalogSnapshot.created_at.desc(),
                    BuiltinCatalogSnapshot.id.desc(),
                )
                .limit(1)
            )
            if snapshot is None:
                definition = _default_definition_factory(key, name, goal)
                provenance = {
                    "source_type": "BUILTIN_DEFAULT",
                    "catalog_mode": "PACKAGE_FALLBACK",
                }
            else:
                definition = ExperimentDefinition.model_validate(snapshot.definition_json)
                provenance = {
                    "source_type": "BUILTIN_DEFAULT",
                    "catalog_snapshot_id": snapshot.id,
                    "catalog_definition_hash": snapshot.definition_hash,
                    "catalog_source_fingerprint": snapshot.source_fingerprint,
                }
        public_map = session.scalar(
            select(WorldMap).where(WorldMap.map_key == definition.world.world_key)
        )
        map_revision = (
            session.get(WorldMapRevision, public_map.current_published_revision_id)
            if public_map and public_map.current_published_revision_id
            else None
        )
        if map_revision is not None and map_revision.state == "PUBLISHED":
            from .maps import WorldMapService

            payload = definition.model_dump(mode="json", exclude_none=False)
            payload["world"] = WorldMapService.materialize_world(
                map_revision, definition.world.overlay
            ).model_dump(mode="json", exclude_none=False)
            definition = ExperimentDefinition.model_validate(payload)
            provenance = {
                **provenance,
                "world_map_id": public_map.id,
                "world_map_revision_id": map_revision.id,
            }
        return definition, provenance

    @staticmethod
    def _definition_with_map(
        session: Session,
        definition: ExperimentDefinition,
        *,
        map_revision_id: str,
    ) -> tuple[ExperimentDefinition, dict[str, str]]:
        map_revision = session.get(WorldMapRevision, map_revision_id)
        if map_revision is None or map_revision.state != "PUBLISHED":
            raise not_found("map_revision", map_revision_id)
        from .maps import WorldMapService

        payload = definition.model_dump(mode="json", exclude_none=False)
        payload["world"] = WorldMapService.materialize_world(
            map_revision, WorldOverlayConfig()
        ).model_dump(mode="json", exclude_none=False)
        return ExperimentDefinition.model_validate(payload), {
            "world_map_id": map_revision.map_id,
            "world_map_revision_id": map_revision.id,
        }

    @staticmethod
    def _definition_for_new_owner(
        source: ExperimentDefinition, *, key: str, name: str, goal: str
    ) -> ExperimentDefinition:
        payload = source.model_dump(mode="json", exclude_none=False)
        payload["experiment"].update({"key": key, "name": name, "goal": goal})
        payload["prompts"] = canonicalize_prompt_payload(payload.get("prompts", {}))
        return ExperimentDefinition.model_validate(payload)

    def create_experiment(
        self,
        *,
        name: str,
        goal: str = "",
        source_type: SourceType = "BUILTIN_DEFAULT",
        source_revision_id: str | None = None,
        owner: str = "",
        tags: list[str] | None = None,
        brain_revision_id: str | None = None,
        map_revision_id: str | None = None,
        crowd_revision_ids: list[str] | None = None,
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
                    "INVALID_SOURCE_TYPE", "不支持的实验初始化方式", status_code=422
                )

            definition = self._definition_for_new_owner(
                source_definition, key=key, name=name, goal=goal
            )
            if map_revision_id:
                definition, map_provenance = self._definition_with_map(
                    session, definition, map_revision_id=map_revision_id
                )
                provenance = {**provenance, **map_provenance}
            if crowd_revision_ids:
                from .crowds import CrowdService

                crowd_agents, crowd_provenance = CrowdService.materialize_agents_in_session(
                    session,
                    crowd_revision_ids=crowd_revision_ids,
                    world=definition.world,
                )
                payload = definition.model_dump(mode="json", exclude_none=False)
                payload["agents"] = [
                    item.model_dump(mode="json", exclude_none=False)
                    for item in crowd_agents
                ]
                definition = ExperimentDefinition.model_validate(payload)
                provenance = {**provenance, **crowd_provenance}
            brain_workflows = None
            if brain_revision_id:
                brain_revision = session.get(BrainRevision, brain_revision_id)
                if brain_revision is None or brain_revision.state != "PUBLISHED":
                    raise not_found("brain_revision", brain_revision_id)
                brain_rows = list(session.scalars(
                    select(BrainWorkflow)
                    .where(BrainWorkflow.revision_id == brain_revision.id)
                    .order_by(BrainWorkflow.workflow_key)
                ))
                brain_workflows = {
                    row.workflow_key: WorkflowDefinition.model_validate(row.definition_json)
                    for row in brain_rows
                }
                from generative_agents.config import DEFAULT_WORKFLOW_KEYS

                missing = [item for item in DEFAULT_WORKFLOW_KEYS if item not in brain_workflows]
                if missing:
                    raise ServiceError(
                        "WORKFLOWS_MISSING",
                        "所选大脑模板的流程快照不完整",
                        status_code=409,
                        details={"workflow_keys": missing},
                    )
                payload = definition.model_dump(mode="json", exclude_none=False)
                payload["prompts"] = canonicalize_prompt_payload(brain_revision.prompts_json)
                definition = ExperimentDefinition.model_validate(payload)
                provenance = {
                    **provenance,
                    "brain_id": brain_revision.brain_id,
                    "brain_revision_id": brain_revision.id,
                    "brain_bundle_hash": brain_revision.bundle_hash,
                }
            digest = definition_hash(definition)
            now = _utc_now()
            experiment = Experiment(
                id=str(uuid4()),
                experiment_key=key,
                name=name,
                goal=goal,
                owner=owner.strip()[:120],
                tags=_normalize_tags(tags),
                template_key="CROWD_COMPOSITION" if crowd_revision_ids else source_type,
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
            from .workflows import WorkflowService

            WorkflowService.seed_revision_in_session(
                session,
                experiment_id=experiment.id,
                revision=revision,
                definition=definition,
                source_revision_id=base_revision_id,
                create_default_versions=True,
                workflow_bundle=brain_workflows,
            )
            if base_revision_id:
                from .scenarios import ScenarioAssemblyService

                ScenarioAssemblyService(self.database).copy_extension(
                    session,
                    source_revision_id=base_revision_id,
                    target_revision_id=revision.id,
                    experiment_id=experiment.id,
                    now=now,
                )
            experiment.current_draft_revision_id = revision.id
            return self._experiment_detail(session, experiment)

    def duplicate_experiment(
        self,
        experiment_id: str,
        *,
        revision_id: str | None = None,
        name: str | None = None,
        goal: str | None = None,
        copy_metadata: bool = True,
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
                owner=source_experiment.owner if copy_metadata else "",
                tags=list(source_experiment.tags or []) if copy_metadata else [],
                template_key="DUPLICATE",
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
            from .workflows import WorkflowService

            WorkflowService.seed_revision_in_session(
                session,
                experiment_id=experiment.id,
                revision=revision,
                definition=definition,
                source_revision_id=source_revision.id,
                create_default_versions=True,
            )
            from .scenarios import ScenarioAssemblyService

            ScenarioAssemblyService(self.database).copy_extension(
                session,
                source_revision_id=source_revision.id,
                target_revision_id=revision.id,
                experiment_id=experiment.id,
                now=now,
            )
            experiment.current_draft_revision_id = revision.id
            return self._experiment_detail(session, experiment)

    def list_experiments(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        owner: str | None = None,
        tag: str | None = None,
        model: str | None = None,
        map_key: str | None = None,
        archived: str = "active",
        page: int = 1,
        page_size: int = 5,
        sort: str = "-updated_at",
    ) -> dict[str, Any]:
        if page < 1 or page_size not in {5, 10, 20, 25, 50}:
            raise ServiceError("INVALID_PAGINATION", "分页参数无效", status_code=422)
        sort_field = sort.removeprefix("-")
        if sort_field not in {"updated_at", "created_at", "name", "status", "run_count"}:
            raise ServiceError("INVALID_SORT", "排序字段无效", status_code=422)
        if archived not in {"active", "archived", "all"}:
            raise ServiceError("INVALID_ARCHIVE_FILTER", "归档筛选无效", status_code=422)
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
            if archived == "active":
                filters.append(Experiment.archived_at.is_(None))
            elif archived == "archived":
                filters.append(Experiment.archived_at.is_not(None))
            definition_text = cast(ExperimentRevision.definition_json, Text)
            if query and query.strip():
                term = f"%{query.strip()}%"
                tag_search_values = func.json_each(Experiment.tags).table_valued(
                    "key", "value"
                )
                filters.append(
                    or_(
                        Experiment.name.ilike(term),
                        Experiment.experiment_key.ilike(term),
                        Experiment.goal.ilike(term),
                        Experiment.owner.ilike(term),
                        exists(
                            select(1)
                            .select_from(tag_search_values)
                            .where(tag_search_values.c.value.ilike(term))
                        ),
                        ExperimentRevision.definition_json["models"]["chat"]["model"].as_string().ilike(term),
                        ExperimentRevision.definition_json["models"]["chat"]["resolved_model"].as_string().ilike(term),
                        ExperimentRevision.definition_json["models"]["embedding"]["model"].as_string().ilike(term),
                        ExperimentRevision.definition_json["models"]["embedding"]["resolved_model"].as_string().ilike(term),
                        ExperimentRevision.definition_json["world"]["world_name"].as_string().ilike(term),
                    )
                )
            if owner and owner.strip():
                filters.append(Experiment.owner == owner.strip())
            if tag and tag.strip():
                tag_values = func.json_each(Experiment.tags).table_valued("key", "value")
                filters.append(
                    exists(
                        select(1)
                        .select_from(tag_values)
                        .where(tag_values.c.value == tag.strip())
                    )
                )
            if model and model.strip():
                filters.append(definition_text.ilike(f"%{model.strip()}%"))
            if map_key and map_key.strip():
                filters.append(definition_text.ilike(f"%{map_key.strip()}%"))

            revision_join = ExperimentRevision.id == func.coalesce(
                Experiment.current_draft_revision_id,
                Experiment.current_published_revision_id,
            )

            counts_stmt = (
                select(Experiment.status, func.count())
                .select_from(Experiment)
                .outerjoin(ExperimentRevision, revision_join)
                .group_by(Experiment.status)
            )
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
                select(func.count())
                .select_from(Experiment)
                .outerjoin(ExperimentRevision, revision_join)
                .where(*page_filters)
            ) or 0
            order_column = (
                select(func.count(Run.id))
                .where(Run.experiment_id == Experiment.id)
                .correlate(Experiment)
                .scalar_subquery()
                if sort_field == "run_count"
                else getattr(Experiment, sort_field)
            )
            order_by = order_column.desc() if sort.startswith("-") else order_column.asc()
            experiments = session.scalars(
                select(Experiment)
                .outerjoin(ExperimentRevision, revision_join)
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
            capability_extensions = {
                extension.revision_id: extension
                for extension in session.scalars(
                    select(ExperimentRevisionCapability).where(
                        ExperimentRevisionCapability.revision_id.in_(revision_ids)
                    )
                ).all()
            } if revision_ids else {}
            map_revision_ids = {
                extension.extension_json.get("map_revision_id")
                for extension in capability_extensions.values()
                if extension.extension_json.get("mode") == "CAPABILITY_COMPOSED"
                and extension.extension_json.get("map_revision_id")
            }
            map_revisions = {
                revision.id: revision
                for revision in session.scalars(
                    select(WorldMapRevision).where(
                        WorldMapRevision.id.in_(map_revision_ids)
                    )
                ).all()
            } if map_revision_ids else {}
            run_ids = {item.latest_run_id for item in experiments if item.latest_run_id}
            runs = {
                run.id: run
                for run in session.scalars(select(Run).where(Run.id.in_(run_ids))).all()
            } if run_ids else {}
            experiment_ids = [item.id for item in experiments]
            run_counts = (
                {
                    experiment_id: int(count)
                    for experiment_id, count in session.execute(
                        select(Run.experiment_id, func.count())
                        .where(Run.experiment_id.in_(experiment_ids))
                        .group_by(Run.experiment_id)
                    )
                }
                if experiment_ids
                else {}
            )

            items = [
                self._list_item(
                    session,
                    item,
                    revisions,
                    capability_extensions,
                    map_revisions,
                    runs,
                    run_counts,
                )
                for item in experiments
            ]
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
        session: Session,
        experiment: Experiment,
        revisions: dict[str, ExperimentRevision],
        capability_extensions: dict[str, ExperimentRevisionCapability],
        map_revisions: dict[str, WorldMapRevision],
        runs: dict[str, Run],
        run_counts: dict[str, int],
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
            extension_row = capability_extensions.get(revision.id)
            if (
                extension_row is not None
                and extension_row.extension_json.get("mode")
                == "CAPABILITY_COMPOSED"
            ):
                from generative_agents.config.scenarios import (
                    ExperimentCapabilityExtension,
                )
                from .scenarios import composed_scenario_requires_models_in_session

                extension = ExperimentCapabilityExtension.model_validate(
                    extension_row.extension_json
                )
                map_revision = map_revisions.get(extension.map_revision_id)
                core_parameters.update(
                    {
                        "execution_mode": "CAPABILITY_COMPOSED",
                        "agent_count": len(extension.actors),
                        "tool_count": len(extension.tool_instances),
                        "duration_ms": extension.clock.duration_ms,
                        "base_tick_ms": extension.clock.base_tick_ms,
                        "snapshot_interval_ms": extension.clock.snapshot_interval_ms,
                        "max_steps": max(
                            1,
                            ceil(
                                extension.clock.duration_ms
                                / extension.clock.snapshot_interval_ms
                            ),
                        ),
                        "requires_models": composed_scenario_requires_models_in_session(
                            session, revision
                        ),
                        "world_name": (
                            map_revision.world_json.get("world_name")
                            if map_revision is not None
                            else "能力场景地图"
                        ),
                    }
                )
            revision_no = revision.revision_no
        return {
            "id": experiment.id,
            "experiment_key": experiment.experiment_key,
            "name": experiment.name,
            "goal": experiment.goal,
            "owner": experiment.owner,
            "tags": list(experiment.tags or []),
            "template_key": experiment.template_key,
            "archived_at": experiment.archived_at,
            "status": experiment.status,
            "run_count": run_counts.get(experiment.id, 0),
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
            "created_at": experiment.created_at,
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
            "owner": experiment.owner,
            "tags": list(experiment.tags or []),
            "template_key": experiment.template_key,
            "archived_at": experiment.archived_at,
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
        owner: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Rename an experiment and its editable Draft in one transaction."""

        name = name.strip()
        if not name:
            raise ServiceError("INVALID_EXPERIMENT_NAME", "实验名称不能为空", status_code=422)
        owner = owner.strip()
        normalized_tags = _normalize_tags(tags)
        if len(name) > 120 or len(goal) > 10_000 or len(owner) > 120:
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
            experiment.owner = owner
            experiment.tags = normalized_tags
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

    def batch_update_agents(
        self,
        experiment_id: str,
        *,
        expected_lock_version: int,
        agent_keys: list[str],
        changes: dict[str, Any],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        keys = set(agent_keys)
        if not keys or len(keys) > 500:
            raise ServiceError("INVALID_AGENT_BATCH", "请选择 1–500 个 Agent", status_code=422)
        allowed = {"enabled", "model_override", "coord", "append_goal", "add_tags"}
        unknown = set(changes) - allowed
        if unknown:
            raise ServiceError(
                "INVALID_AGENT_BATCH_FIELD",
                "批量修改包含不支持的字段",
                status_code=422,
                details={"fields": sorted(unknown)},
            )
        draft = self.get_draft(experiment_id)
        if draft["lock_version"] != expected_lock_version:
            raise ServiceError("REVISION_CONFLICT", "草稿已变化，请重新载入", status_code=409)
        payload = draft["definition"]
        affected = []
        for agent in payload["agents"]:
            if agent["agent_key"] not in keys:
                continue
            before = {
                field: agent.get(field)
                for field in ("enabled", "model_override", "coord", "goals", "tags")
            }
            if "enabled" in changes:
                agent["enabled"] = bool(changes["enabled"])
            if "model_override" in changes:
                agent["model_override"] = changes["model_override"] or None
            if "coord" in changes:
                coord = changes["coord"]
                if not isinstance(coord, list) or len(coord) != 2:
                    raise ServiceError("INVALID_AGENT_COORD", "批量位置必须是 [x, y]", status_code=422)
                agent["coord"] = [int(coord[0]), int(coord[1])]
            if changes.get("append_goal"):
                agent["goals"] = [*(agent.get("goals") or []), str(changes["append_goal"]).strip()]
            if changes.get("add_tags"):
                agent["tags"] = _normalize_tags([*(agent.get("tags") or []), *changes["add_tags"]])
            affected.append(
                {
                    "agent_key": agent["agent_key"],
                    "name": agent["name"],
                    "before": before,
                    "after": {
                        field: agent.get(field)
                        for field in ("enabled", "model_override", "coord", "goals", "tags")
                    },
                }
            )
        if len(affected) != len(keys):
            missing = sorted(keys - {item["agent_key"] for item in affected})
            raise ServiceError("AGENT_NOT_FOUND", "部分 Agent 不存在", status_code=404, details={"agent_keys": missing})
        preview = {"affected": len(affected), "changes": affected}
        if dry_run:
            return {**preview, "dry_run": True, "lock_version": draft["lock_version"]}
        saved = self.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=expected_lock_version,
            definition=ExperimentDefinition.model_validate(payload),
        )
        return {**preview, "dry_run": False, "draft": saved}

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
            if definition.world.map_revision_id:
                from .maps import WorldMapService

                payload = definition.model_dump(mode="json", exclude_none=False)
                payload["world"] = WorldMapService(
                    self.database
                ).materialize_for_publish_in_session(
                    session, definition.world
                ).model_dump(mode="json", exclude_none=False)
                definition = ExperimentDefinition.model_validate(payload)
            refs = set(session.scalars(select(Secret.id)).all())
            report = validate_for_publish(definition, existing_secret_refs=refs)
            from .workflows import (
                WorkflowService,
                workflow_execution_issues,
                workflow_function_sources_in_session,
                workflow_validation_issues,
            )

            WorkflowService.ensure_revision_in_session(
                session,
                experiment_id=revision.experiment_id,
                revision=revision,
                definition=definition,
            )
            workflows = WorkflowService.load_revision_bundle_in_session(
                session, revision.id
            )
            workflow_issues = workflow_validation_issues(
                workflows,
                definition,
                allowed_script_operations=WorkflowService.allowed_script_operations_in_session(session),
            )
            if not workflow_issues:
                workflow_issues.extend(
                    workflow_execution_issues(
                        workflows,
                        function_sources=workflow_function_sources_in_session(
                            session, workflows
                        ),
                    )
                )
            report.errors.extend(
                ValidationIssue.model_validate(issue) for issue in workflow_issues
            )
            from .scenarios import ScenarioAssemblyService

            for issue in ScenarioAssemblyService.validate_for_publish(
                session, revision
            ):
                report.errors.append(
                    ValidationIssue(
                        code=issue["code"],
                        path=f"capability_assembly.{issue['path']}",
                        message=issue["message"],
                        severity="ERROR",
                        fix_page="overview",
                        fix_control="scenarioAssemblyMode",
                    )
                )
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
        if definition.world.map_revision_id:
            from .maps import WorldMapService

            materialized_world = WorldMapService(
                self.database
            ).materialize_for_publish_in_session(session, definition.world)
            normalized_payload = definition.model_dump(mode="json", exclude_none=False)
            normalized_payload["world"] = materialized_world.model_dump(
                mode="json", exclude_none=False
            )
            definition = ExperimentDefinition.model_validate(normalized_payload)
        refs = set(session.scalars(select(Secret.id)).all())
        report = validate_for_publish(definition, existing_secret_refs=refs)
        from .scenarios import composed_scenario_requires_models_in_session

        if not composed_scenario_requires_models_in_session(session, revision):
            report.errors = [
                issue for issue in report.errors if issue.code != "MODEL_NOT_RESOLVED"
            ]
        from .workflows import (
            WorkflowService,
            workflow_execution_issues,
            workflow_function_sources_in_session,
            workflow_validation_issues,
        )

        WorkflowService.ensure_revision_in_session(
            session,
            experiment_id=experiment_id,
            revision=revision,
            definition=definition,
        )
        workflows = WorkflowService.load_revision_bundle_in_session(session, revision.id)
        workflow_issues = workflow_validation_issues(
            workflows,
            definition,
            allowed_script_operations=WorkflowService.allowed_script_operations_in_session(session),
        )
        if not workflow_issues:
            workflow_issues.extend(
                workflow_execution_issues(
                    workflows,
                    function_sources=workflow_function_sources_in_session(
                        session, workflows
                    ),
                )
            )
        report.errors.extend(
            ValidationIssue.model_validate(issue) for issue in workflow_issues
        )
        from .scenarios import ScenarioAssemblyService

        for issue in ScenarioAssemblyService.validate_for_publish(session, revision):
            report.errors.append(
                ValidationIssue(
                    code=issue["code"],
                    path=f"capability_assembly.{issue['path']}",
                    message=issue["message"],
                    severity="ERROR",
                    fix_page="overview",
                    fix_control="scenarioAssemblyMode",
                )
            )
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
            draft_payload = ExperimentDefinition.model_validate(source.definition_json).model_dump(
                mode="json", exclude_none=False
            )
            draft_payload["prompts"] = canonicalize_prompt_payload(
                draft_payload.get("prompts", {})
            )
            draft_definition = ExperimentDefinition.model_validate(draft_payload)
            draft = ExperimentRevision(
                id=str(uuid4()),
                experiment_id=experiment_id,
                revision_no=next_no,
                state="DRAFT",
                base_revision_id=source.id,
                schema_version=source.schema_version,
                definition_json=draft_definition.model_dump(mode="json", exclude_none=False),
                definition_hash=definition_hash(draft_definition),
                validation_json=None,
                validated_hash=None,
                provenance_json={"source_type": "FORK", "source_revision_id": source.id},
                snapshot_complete=False,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            from .workflows import WorkflowService

            WorkflowService.seed_revision_in_session(
                session,
                experiment_id=experiment_id,
                revision=draft,
                definition=ExperimentDefinition.model_validate(draft.definition_json),
                source_revision_id=source.id,
                create_default_versions=False,
            )
            from .scenarios import ScenarioAssemblyService

            ScenarioAssemblyService(self.database).copy_extension(
                session,
                source_revision_id=source.id,
                target_revision_id=draft.id,
                experiment_id=experiment_id,
                now=now,
            )
            experiment.current_draft_revision_id = draft.id
            experiment.status = "DRAFT"
            experiment.updated_at = now
            return self._revision_detail(draft)

    def set_archived(
        self,
        experiment_id: str,
        *,
        archived: bool,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None:
                raise not_found("experiment", experiment_id)
            if expected_row_version is not None and experiment.row_version != expected_row_version:
                raise ServiceError(
                    "EXPERIMENT_CONFLICT",
                    "实验已被其他请求修改，请重新载入",
                    status_code=409,
                )
            if archived and experiment.status in {"QUEUED", "RUNNING", "PAUSED"}:
                raise ServiceError(
                    "EXPERIMENT_ACTIVE",
                    "运行中、排队中或暂停的实验不能归档",
                    status_code=409,
                    details={"status": experiment.status},
                )
            experiment.archived_at = now if archived else None
            experiment.row_version += 1
            experiment.updated_at = now
            session.flush()
            return self._experiment_detail(session, experiment)

    def batch_manage(
        self,
        experiment_ids: list[str],
        *,
        action: Literal["ARCHIVE", "RESTORE", "ADD_TAGS", "SET_OWNER"],
        owner: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        ids = list(dict.fromkeys(experiment_ids))
        if not ids or len(ids) > 200:
            raise ServiceError("INVALID_BATCH", "请选择 1–200 个实验", status_code=422)
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            experiments = list(
                session.scalars(select(Experiment).where(Experiment.id.in_(ids)))
            )
            if len(experiments) != len(ids):
                raise ServiceError("BATCH_TARGET_MISSING", "部分实验不存在", status_code=404)
            if action == "ARCHIVE" and any(
                item.status in {"QUEUED", "RUNNING", "PAUSED"} for item in experiments
            ):
                raise ServiceError(
                    "EXPERIMENT_ACTIVE",
                    "所选实验中包含正在运行、排队或暂停的实验",
                    status_code=409,
                )
            for experiment in experiments:
                if action == "ARCHIVE":
                    experiment.archived_at = now
                elif action == "RESTORE":
                    experiment.archived_at = None
                elif action == "ADD_TAGS":
                    experiment.tags = _normalize_tags([*(experiment.tags or []), *(tags or [])])
                elif action == "SET_OWNER":
                    experiment.owner = (owner or "").strip()[:120]
                else:
                    raise ServiceError("INVALID_BATCH_ACTION", "批量操作无效", status_code=422)
                experiment.row_version += 1
                experiment.updated_at = now
            return {"action": action, "affected": len(experiments), "ids": ids}

    def estimate_run(self, experiment_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None:
                raise not_found("experiment", experiment_id)
            revision_id = experiment.current_draft_revision_id or experiment.current_published_revision_id
            revision = session.get(ExperimentRevision, revision_id) if revision_id else None
            if revision is None:
                raise ServiceError("REVISION_NOT_FOUND", "实验没有可估算的配置版本", status_code=409)
            definition = ExperimentDefinition.model_validate(revision.definition_json)
            extension_row = session.get(ExperimentRevisionCapability, revision.id)
            if extension_row is not None:
                from generative_agents.config.scenarios import (
                    ExperimentCapabilityExtension,
                )

                extension = ExperimentCapabilityExtension.model_validate(
                    extension_row.extension_json
                )
            else:
                extension = None
            if extension is not None and extension.mode == "CAPABILITY_COMPOSED":
                from .scenarios import ScenarioAssemblyService

                schedule = ScenarioAssemblyService(self.database)._compile_schedule(
                    session, extension
                )
                agents = len(extension.actors)
                steps = max(
                    1,
                    ceil(
                        extension.clock.duration_ms
                        / extension.clock.snapshot_interval_ms
                    ),
                )
                physical_ticks = ceil(
                    extension.clock.duration_ms / extension.clock.base_tick_ms
                )
                llm_calls = schedule["estimated_llm_decisions"]
                calls_low = llm_calls
                calls_high = llm_calls
                token_low = calls_low * 300
                token_high = calls_high * 1400
                capability_executions = schedule["total_executions"]
                wall_low = max(
                    1,
                    ceil(physical_ticks * 0.0002 + capability_executions * 0.0004),
                )
                wall_high = max(
                    wall_low,
                    ceil(
                        physical_ticks * 0.002
                        + capability_executions * 0.004
                        + calls_high * 15
                    ),
                )
                storage_low = steps * max(1, agents) * 700 + capability_executions * 60
                storage_high = steps * max(1, agents) * 4_000 + capability_executions * 400
                thresholds = []
                if physical_ticks >= 100_000:
                    thresholds.append("物理 tick 达到高频长时实验规模")
                if calls_high >= 500:
                    thresholds.append("LLM 决策次数达到高成本规模")
                if definition.results.capture_model_payloads and calls_high:
                    thresholds.append("记录模型输入输出会增加存储与隐私风险")
                map_revision = session.get(
                    WorldMapRevision, extension.map_revision_id
                )
                world_map = (
                    session.get(WorldMap, map_revision.map_id)
                    if map_revision is not None
                    else None
                )
                world_size = (
                    ((map_revision.world_json or {}).get("definition") or {}).get(
                        "size"
                    )
                    if map_revision is not None
                    else None
                )
                actual = self._latest_run_actual(session, experiment)
                return {
                    "experiment_id": experiment_id,
                    "revision_id": revision.id,
                    "basis": (
                        "按物理 tick、能力调度和结果快照估算；仅 LLM/工作流能力计入模型调用"
                    ),
                    "scale": {
                        "execution_mode": "CAPABILITY_COMPOSED",
                        "agents": agents,
                        "tool_instances": len(extension.tool_instances),
                        "steps": steps,
                        "duration_ms": extension.clock.duration_ms,
                        "base_tick_ms": extension.clock.base_tick_ms,
                        "snapshot_interval_ms": extension.clock.snapshot_interval_ms,
                        "physical_ticks": physical_ticks,
                        "capability_tasks": len(schedule["tasks"]),
                        "capability_executions": capability_executions,
                        "virtual_minutes": extension.clock.duration_ms / 60_000,
                        "world_name": world_map.name if world_map else "能力场景地图",
                        "world_size": world_size
                        if isinstance(world_size, list) and len(world_size) >= 2
                        else None,
                        "projection_interval_steps": 1,
                        "capture_model_payloads": definition.results.capture_model_payloads,
                    },
                    "estimate": {
                        "model_calls": {"low": calls_low, "high": calls_high},
                        "tokens": {"low": token_low, "high": token_high},
                        "wall_seconds": {"low": wall_low, "high": wall_high},
                        "storage_bytes": {
                            "low": storage_low,
                            "high": storage_high,
                        },
                    },
                    "high_scale": bool(thresholds),
                    "threshold_reasons": thresholds,
                    "actual": actual,
                }
            agents = sum(agent.enabled for agent in definition.agents)
            steps = definition.simulation.max_steps
            agent_steps = agents * steps
            calls_low = max(0, round(agent_steps * 0.35))
            calls_high = max(calls_low, round(agent_steps * 0.9))
            token_low = calls_low * 300
            token_high = calls_high * 1400
            wall_low = calls_low * 2
            wall_high = calls_high * 15
            storage_low = agent_steps * 500 + token_low * 2
            storage_high = agent_steps * 2_000 + token_high * 4
            thresholds = []
            if agents >= 20:
                thresholds.append("Agent 数达到整镇规模")
            if steps >= 500:
                thresholds.append("运行步数达到长时实验规模")
            if definition.results.agent_step_projection_interval_steps == 1:
                thresholds.append("每步写入 Agent 状态投影")
            if definition.results.capture_model_payloads:
                thresholds.append("记录模型输入输出会增加存储与隐私风险")
            actual = self._latest_run_actual(session, experiment)
            return {
                "experiment_id": experiment_id,
                "revision_id": revision.id,
                "basis": "按 Agent×步数及 UX-03 实测调用密度估算，模型和 Prompt 复杂度会造成约 2–3 倍误差",
                "scale": {
                    "agents": agents,
                    "steps": steps,
                    "virtual_minutes": steps * definition.simulation.stride_minutes,
                    "projection_interval_steps": definition.results.agent_step_projection_interval_steps,
                    "capture_model_payloads": definition.results.capture_model_payloads,
                },
                "estimate": {
                    "model_calls": {"low": calls_low, "high": calls_high},
                    "tokens": {"low": token_low, "high": token_high},
                    "wall_seconds": {"low": wall_low, "high": wall_high},
                    "storage_bytes": {"low": storage_low, "high": storage_high},
                },
                "high_scale": bool(thresholds),
                "threshold_reasons": thresholds,
                "actual": actual,
            }

    @staticmethod
    def _latest_run_actual(session: Session, experiment: Experiment) -> dict[str, Any] | None:
        if not experiment.latest_run_id:
            return None
        run = session.get(Run, experiment.latest_run_id)
        summary = session.get(RunResultSummary, experiment.latest_run_id)
        storage = session.scalar(
            select(func.sum(RunArtifact.size_bytes)).where(
                RunArtifact.run_id == experiment.latest_run_id,
                RunArtifact.state == "READY",
            )
        ) or 0
        duration = None
        if run and run.started_at and run.finished_at:
            duration = max(
                0, int((run.finished_at - run.started_at).total_seconds())
            )
        return {
            "run_id": experiment.latest_run_id,
            "model_calls": summary.model_call_count if summary else None,
            "wall_seconds": duration,
            "storage_bytes": int(storage),
            "completed_steps": run.completed_steps if run else None,
        }

    def compare_experiments(self, experiment_ids: list[str]) -> dict[str, Any]:
        ids = list(dict.fromkeys(experiment_ids))
        if len(ids) < 2 or len(ids) > 12:
            raise ServiceError("INVALID_COMPARISON", "请选择 2–12 个实验进行比较", status_code=422)
        with self.database.session_factory() as session:
            experiments = list(session.scalars(select(Experiment).where(Experiment.id.in_(ids))))
            by_id = {item.id: item for item in experiments}
            if len(by_id) != len(ids):
                raise ServiceError("COMPARISON_TARGET_MISSING", "部分实验不存在", status_code=404)
            documents = []
            flattened = []
            for experiment_id in ids:
                experiment = by_id[experiment_id]
                revision_id = experiment.current_draft_revision_id or experiment.current_published_revision_id
                revision = session.get(ExperimentRevision, revision_id) if revision_id else None
                if revision is None:
                    raise ServiceError("REVISION_NOT_FOUND", "实验缺少可比较版本", status_code=409)
                definition = ExperimentDefinition.model_validate(revision.definition_json).model_dump(mode="json", exclude_none=False)
                documents.append(
                    {
                        "experiment_id": experiment.id,
                        "name": experiment.name,
                        "revision_id": revision.id,
                        "revision_no": revision.revision_no,
                        "state": revision.state,
                    }
                )
                flattened.append(_flatten_document(definition))
            paths = sorted(set().union(*(item.keys() for item in flattened)))
            groups = {name: [] for name in ("experiment", "agents", "models", "prompts", "world", "behavior", "simulation", "results", "other")}
            same_count = 0
            for path in paths:
                values = [item.get(path) for item in flattened]
                encoded = [repr(value) for value in values]
                if len(set(encoded)) == 1:
                    same_count += 1
                    continue
                root = path.split(".", 1)[0]
                group = root if root in groups else "other"
                groups[group].append({"path": path, "values": values})
            return {
                "experiments": documents,
                "groups": [
                    {"key": key, "differences": value}
                    for key, value in groups.items()
                    if value
                ],
                "same_field_count": same_count,
                "difference_count": sum(len(value) for value in groups.values()),
            }

    def save_comparison_group(self, name: str, experiment_ids: list[str]) -> dict[str, Any]:
        comparison = self.compare_experiments(experiment_ids)
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            row = ExperimentComparisonGroup(
                name=name.strip()[:120] or "未命名对照组",
                experiment_ids_json=list(dict.fromkeys(experiment_ids)),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return {"id": row.id, "name": row.name, "experiment_ids": row.experiment_ids_json, "comparison": comparison}

    def list_comparison_groups(self) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            rows = list(session.scalars(select(ExperimentComparisonGroup).order_by(ExperimentComparisonGroup.updated_at.desc())))
            return [{"id": row.id, "name": row.name, "experiment_ids": row.experiment_ids_json, "created_at": row.created_at, "updated_at": row.updated_at} for row in rows]

    def save_view(self, name: str, query: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            row = ExperimentSavedView(name=name.strip()[:120] or "未命名视图", query_json=query, created_at=now, updated_at=now)
            session.add(row)
            session.flush()
            return {"id": row.id, "name": row.name, "share_key": row.share_key, "query": row.query_json, "created_at": row.created_at, "updated_at": row.updated_at}

    def list_views(self) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            rows = list(session.scalars(select(ExperimentSavedView).order_by(ExperimentSavedView.updated_at.desc())))
            return [{"id": row.id, "name": row.name, "share_key": row.share_key, "query": row.query_json, "created_at": row.created_at, "updated_at": row.updated_at} for row in rows]

    def get_view_by_share_key(self, share_key: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            row = session.scalar(select(ExperimentSavedView).where(ExperimentSavedView.share_key == share_key))
            if row is None:
                raise not_found("saved_view", share_key)
            return {"id": row.id, "name": row.name, "share_key": row.share_key, "query": row.query_json, "created_at": row.created_at, "updated_at": row.updated_at}

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
