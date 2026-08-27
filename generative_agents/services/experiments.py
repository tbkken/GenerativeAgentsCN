"""实验草稿、发布校验和不可变 Revision 的事务服务。"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from math import ceil
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import Text, cast, exists, func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from generative_agents.config import (
    ExperimentDefinition,
    ValidationIssue,
    definition_hash,
    make_builtin_definition,
    validate_for_publish,
)
from generative_agents.config.schema import WorldOverlayConfig, make_blank_definition
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    BuiltinCatalogSnapshot,
    Experiment,
    ExperimentComparisonGroup,
    ExperimentRevision,
    ExperimentSavedView,
    Run,
    RunArtifact,
    RunResultSummary,
    Secret,
    WorldMap,
    WorldMapRevision,
)
from generative_agents.status import (
    ArtifactState,
    ExperimentStatus,
    ExperimentStatusFilter,
    RevisionState,
)

from .errors import ServiceError, not_found

SourceType = Literal[
    "BUILTIN_DEFAULT",
    "BLANK",
    "REVISION",
]
DefinitionFactory = Callable[[str, str, str], ExperimentDefinition]


def _utc_now() -> datetime:
    """执行`utc``now`的内部处理，供当前模块或类复用。

    返回:
        返回 `datetime` 类型的处理结果。
    """
    return datetime.now(timezone.utc)


def _make_key(name: str) -> str:
    """执行`make``key`的内部处理，供当前模块或类复用。

    参数:
        name: 目标对象的人类可读名称。 类型：`str`。

    返回:
        返回处理后的文本或稳定标识。
    """
    ascii_key = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    prefix = ascii_key[:48].strip("-") or "experiment"
    return f"{prefix}-{uuid4().hex[:8]}"


def _default_definition_factory(key: str, name: str, goal: str) -> ExperimentDefinition:
    """执行`default`仿真定义`factory`的内部处理，供当前模块或类复用。

    参数:
        key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。
        name: 目标对象的人类可读名称。 类型：`str`。
        goal: 路径搜索、计划或推理任务需要达到的目标。 类型：`str`。

    返回:
        返回 `ExperimentDefinition` 类型的处理结果。
    """
    return make_builtin_definition(key=key, name=name, goal=goal)


def _normalize_tags(tags: list[str] | None) -> list[str]:
    """规范化`tags`。

    参数:
        tags: 用于分类、检索或展示目标对象的去重标签集合。 类型：`list[str] | None`。

    返回:
        返回按接口约定组织的结果集合。
    """
    normalized: list[str] = []
    for raw in tags or []:
        value = raw.strip()
        if value and value.casefold() not in {item.casefold() for item in normalized}:
            normalized.append(value[:48])
    return normalized[:20]


def _flatten_document(value: Any, prefix: str = "") -> dict[str, Any]:
    """执行`flatten``document`的内部处理，供当前模块或类复用。

    参数:
        value: 当前操作使用的`value`。 类型：`Any`。
        prefix: 生成稳定键、日志名或路径名时使用的前缀。 类型：`str`。 默认值：`''`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
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
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。
            builtin_definition_factory: 传入当前算法的`builtin`定义`factory`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`DefinitionFactory | None`。 默认值：`None`。

        返回:
            无返回值。
        """
        self.database = database
        self._builtin_factory = builtin_definition_factory

    def _builtin_definition(
        self, session: Session, *, key: str, name: str, goal: str
    ) -> tuple[ExperimentDefinition, dict[str, Any]]:
        """执行`builtin`仿真定义的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。
            name: 目标对象的人类可读名称。 类型：`str`。
            goal: 路径搜索、计划或推理任务需要达到的目标。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
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
                definition = ExperimentDefinition.model_validate(
                    snapshot.definition_json
                )
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
        if map_revision is not None and map_revision.state == RevisionState.PUBLISHED:
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
        """执行仿真定义`with`地图的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。 类型：`ExperimentDefinition`。
            map_revision_id: 地图修订版本的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        map_revision = session.get(WorldMapRevision, map_revision_id)
        if map_revision is None or map_revision.state != RevisionState.PUBLISHED:
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
        """执行仿真定义`for``new``owner`的内部处理，供当前模块或类复用。

        参数:
            source: 当前操作使用的`source`。 类型：`ExperimentDefinition`。
            key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。
            name: 目标对象的人类可读名称。 类型：`str`。
            goal: 路径搜索、计划或推理任务需要达到的目标。 类型：`str`。

        返回:
            返回 `ExperimentDefinition` 类型的处理结果。
        """
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
        owner: str = "",
        tags: list[str] | None = None,
        map_revision_id: str | None = None,
        crowd_revision_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """创建实验。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。
            goal: 路径搜索、计划或推理任务需要达到的目标。 类型：`str`。 默认值：`''`。
            source_type: `source`的类型判别值。 类型：`SourceType`。 默认值：`'BUILTIN_DEFAULT'`。
            source_revision_id: `source`修订版本的唯一标识。 类型：`str | None`。 默认值：`None`。
            owner: 所有者名称筛选值；为空时不限制所有者。 类型：`str`。 默认值：`''`。
            tags: 用于分类、检索或展示目标对象的去重标签集合。 类型：`list[str] | None`。 默认值：`None`。
            map_revision_id: 地图修订版本的唯一标识。 类型：`str | None`。 默认值：`None`。
            crowd_revision_ids: 需要批量处理的人群修订版本唯一标识集合。 类型：`list[str] | None`。 默认值：`None`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        name = name.strip()
        if not name:
            raise ServiceError(
                "INVALID_EXPERIMENT_NAME", "实验名称不能为空", status_code=422
            )
        key = _make_key(name)
        with self.database.session_factory.begin() as session:
            if source_type == "REVISION":
                if not source_revision_id:
                    raise ServiceError(
                        "SOURCE_REVISION_REQUIRED",
                        "REVISION 来源必须提供 revision_id",
                        status_code=422,
                    )
                source_revision = session.get(ExperimentRevision, source_revision_id)
                if (
                    source_revision is None
                    or source_revision.state != RevisionState.PUBLISHED
                ):
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

                crowd_agents, crowd_provenance = (
                    CrowdService.materialize_agents_in_session(
                        session,
                        crowd_revision_ids=crowd_revision_ids,
                        world=definition.world,
                    )
                )
                payload = definition.model_dump(mode="json", exclude_none=False)
                payload["agents"] = [
                    item.model_dump(mode="json", exclude_none=False)
                    for item in crowd_agents
                ]
                definition = ExperimentDefinition.model_validate(payload)
                provenance = {**provenance, **crowd_provenance}
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
                status=ExperimentStatus.DRAFT.value,
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
                state=RevisionState.DRAFT.value,
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
        copy_metadata: bool = True,
    ) -> dict[str, Any]:
        """执行 `ExperimentService` 的`duplicate`实验操作。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            revision_id: 实验修订版本的唯一标识。 类型：`str | None`。 默认值：`None`。
            name: 目标对象的人类可读名称。 类型：`str | None`。 默认值：`None`。
            goal: 路径搜索、计划或推理任务需要达到的目标。 类型：`str | None`。 默认值：`None`。
            copy_metadata: 传入当前算法的`copy``metadata`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`bool`。 默认值：`True`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """

        with self.database.session_factory.begin() as session:
            source_experiment = session.get(Experiment, experiment_id)
            if source_experiment is None:
                raise not_found("experiment", experiment_id)
            selected_id = (
                revision_id
                or source_experiment.current_draft_revision_id
                or source_experiment.current_published_revision_id
            )
            source_revision = (
                session.get(ExperimentRevision, selected_id) if selected_id else None
            )
            if (
                source_revision is None
                or source_revision.experiment_id != experiment_id
            ):
                raise not_found("revision", selected_id or "")
            if revision_id and source_revision.state != RevisionState.PUBLISHED:
                raise ServiceError(
                    "SOURCE_REVISION_NOT_PUBLISHED",
                    "显式复制来源必须是已发布 Revision",
                    status_code=409,
                )

            duplicate_name = (name or f"{source_experiment.name} · 副本").strip()
            duplicate_goal = source_experiment.goal if goal is None else goal
            if not duplicate_name:
                raise ServiceError(
                    "INVALID_EXPERIMENT_NAME", "实验名称不能为空", status_code=422
                )
            key = _make_key(duplicate_name)
            source_definition = ExperimentDefinition.model_validate(
                source_revision.definition_json
            )
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
                status=ExperimentStatus.DRAFT.value,
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
                state=RevisionState.DRAFT.value,
                base_revision_id=(
                    source_revision.id
                    if source_revision.state == RevisionState.PUBLISHED
                    else None
                ),
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
        owner: str | None = None,
        tag: str | None = None,
        model: str | None = None,
        map_key: str | None = None,
        archived: str = "active",
        page: int = 1,
        page_size: int = 5,
        sort: str = "-updated_at",
    ) -> dict[str, Any]:
        """查询`experiments`。

        参数:
            status: 实验或修订版本状态。允许值：`DRAFT`、`QUEUED`、`RUNNING`、`PAUSED`、`COMPLETED`、`CANCELLED`、`FAILED`；聚合查询还可使用 `ABNORMAL`（失败或取消）。 类型：`str | None`。 默认值：`None`。
            query: 用于名称、正文或标识模糊匹配的搜索文本。 类型：`str | None`。 默认值：`None`。
            owner: 所有者名称筛选值；为空时不限制所有者。 类型：`str | None`。 默认值：`None`。
            tag: 标签筛选值；为空时不限制标签。 类型：`str | None`。 默认值：`None`。
            model: 当前调用、筛选或序列化的模型配置或模型实例。 类型：`str | None`。 默认值：`None`。
            map_key: 用于稳定定位地图的键。 类型：`str | None`。 默认值：`None`。
            archived: 归档范围筛选值：`active`、`archived` 或 `all`。 类型：`str`。 默认值：`'active'`。
            page: 从 1 开始的分页页码。 类型：`int`。 默认值：`1`。
            page_size: 每页最多返回的记录数量。 类型：`int`。 默认值：`5`。
            sort: 列表排序表达式；前缀 `-` 表示降序。 类型：`str`。 默认值：`'-updated_at'`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        if page < 1 or page_size not in {5, 10, 20, 25, 50}:
            raise ServiceError("INVALID_PAGINATION", "分页参数无效", status_code=422)
        sort_field = sort.removeprefix("-")
        if sort_field not in {
            "updated_at",
            "created_at",
            "name",
            "status",
            "run_count",
        }:
            raise ServiceError("INVALID_SORT", "排序字段无效", status_code=422)
        if archived not in {"active", "archived", "all"}:
            raise ServiceError(
                "INVALID_ARCHIVE_FILTER", "归档筛选无效", status_code=422
            )
        try:
            status_filter = ExperimentStatusFilter(status) if status else None
        except ValueError as exc:
            raise ServiceError("INVALID_STATUS", "??????", status_code=422) from exc

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
                        ExperimentRevision.definition_json["models"]["chat"]["model"]
                        .as_string()
                        .ilike(term),
                        ExperimentRevision.definition_json["models"]["chat"][
                            "resolved_model"
                        ]
                        .as_string()
                        .ilike(term),
                        ExperimentRevision.definition_json["models"]["embedding"][
                            "model"
                        ]
                        .as_string()
                        .ilike(term),
                        ExperimentRevision.definition_json["models"]["embedding"][
                            "resolved_model"
                        ]
                        .as_string()
                        .ilike(term),
                        ExperimentRevision.definition_json["world"]["world_name"]
                        .as_string()
                        .ilike(term),
                    )
                )
            if owner and owner.strip():
                filters.append(Experiment.owner == owner.strip())
            if tag and tag.strip():
                tag_values = func.json_each(Experiment.tags).table_valued(
                    "key", "value"
                )
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
                state.value: int(grouped.get(state.value, 0))
                for state in ExperimentStatus
            }
            status_counts["ALL"] = sum(status_counts.values())

            page_filters = [*filters]
            if status_filter:
                page_filters.append(
                    Experiment.status.in_(
                        {ExperimentStatus.FAILED, ExperimentStatus.CANCELLED}
                    )
                    if status_filter == ExperimentStatusFilter.ABNORMAL
                    else Experiment.status == status_filter.value
                )
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(Experiment)
                    .outerjoin(ExperimentRevision, revision_join)
                    .where(*page_filters)
                )
                or 0
            )
            order_column = (
                select(func.count(Run.id))
                .where(Run.experiment_id == Experiment.id)
                .correlate(Experiment)
                .scalar_subquery()
                if sort_field == "run_count"
                else getattr(Experiment, sort_field)
            )
            order_by = (
                order_column.desc() if sort.startswith("-") else order_column.asc()
            )
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
            revisions = (
                {
                    revision.id: revision
                    for revision in session.scalars(
                        select(ExperimentRevision).where(
                            ExperimentRevision.id.in_(revision_ids)
                        )
                    ).all()
                }
                if revision_ids
                else {}
            )
            run_ids = {item.latest_run_id for item in experiments if item.latest_run_id}
            runs = (
                {
                    run.id: run
                    for run in session.scalars(
                        select(Run).where(Run.id.in_(run_ids))
                    ).all()
                }
                if run_ids
                else {}
            )
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
        runs: dict[str, Run],
        run_counts: dict[str, int],
    ) -> dict[str, Any]:
        """查询`item`。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            experiment: 传入当前算法的实验；其结构与有效范围由类型注解和调用协议共同限定。 类型：`Experiment`。
            revisions: 传入当前算法的`revisions`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict[str, ExperimentRevision]`。
            runs: 传入当前算法的`runs`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict[str, Run]`。
            run_counts: 传入当前算法的运行`counts`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict[str, int]`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        revision_id = (
            experiment.current_draft_revision_id
            or experiment.current_published_revision_id
        )
        revision = revisions.get(revision_id) if revision_id else None
        definition = (
            ExperimentDefinition.model_validate(revision.definition_json)
            if revision
            else None
        )
        latest_run = (
            runs.get(experiment.latest_run_id) if experiment.latest_run_id else None
        )
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
                "execution_mode": "SKILL_BRAIN",
                "brain_skill": definition.engine.brain_skill,
            }
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
        """获取实验。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None:
                raise not_found("experiment", experiment_id)
            return self._experiment_detail(session, experiment)

    @staticmethod
    def _experiment_detail(session: Session, experiment: Experiment) -> dict[str, Any]:
        """执行实验`detail`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            experiment: 传入当前算法的实验；其结构与有效范围由类型注解和调用协议共同限定。 类型：`Experiment`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
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
        latest_run = (
            session.get(Run, experiment.latest_run_id)
            if experiment.latest_run_id
            else None
        )
        run_count = (
            session.scalar(
                select(func.count())
                .select_from(Run)
                .where(Run.experiment_id == experiment.id)
            )
            or 0
        )
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
        """执行修订版本摘要的内部处理，供当前模块或类复用。

        参数:
            revision: 当前读取、发布、克隆或校验的修订版本记录。 类型：`ExperimentRevision | None`。

        返回:
            返回以字段名或业务键组织的结构化映射。 没有可用结果时返回 `None`。
        """
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
        """获取`draft`。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
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
        """更新`metadata`。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            expected_row_version: 调用方读取记录时看到的行版本；不一致时拒绝覆盖他人更新。 类型：`int`。
            name: 目标对象的人类可读名称。 类型：`str`。
            goal: 路径搜索、计划或推理任务需要达到的目标。 类型：`str`。
            owner: 所有者名称筛选值；为空时不限制所有者。 类型：`str`。 默认值：`''`。
            tags: 用于分类、检索或展示目标对象的去重标签集合。 类型：`list[str] | None`。 默认值：`None`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """

        name = name.strip()
        if not name:
            raise ServiceError(
                "INVALID_EXPERIMENT_NAME", "实验名称不能为空", status_code=422
            )
        owner = owner.strip()
        normalized_tags = _normalize_tags(tags)
        if len(name) > 120 or len(goal) > 10_000 or len(owner) > 120:
            raise ServiceError(
                "INVALID_EXPERIMENT_METADATA", "实验元数据过长", status_code=422
            )
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
                if revision is None or revision.state != RevisionState.DRAFT:
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
        """更新`draft`。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。
            definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。 类型：`ExperimentDefinition`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        with self.database.session_factory.begin() as session:
            experiment, current = self._require_draft(session, experiment_id)
            if definition.experiment.key != experiment.experiment_key:
                raise ServiceError(
                    "EXPERIMENT_KEY_IMMUTABLE",
                    "实验 key 不允许通过草稿修改",
                    status_code=422,
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
                    ExperimentRevision.state == RevisionState.DRAFT,
                    ExperimentRevision.lock_version == expected_lock_version,
                )
                .values(
                    definition_json=definition.model_dump(
                        mode="json", exclude_none=False
                    ),
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
        """执行 `ExperimentService` 的`patch``draft``section`操作。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            section: 需要读取或修改的草稿配置区域名称。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。
            data: 待编码、解码、校验或持久化的原始数据。 类型：`dict[str, Any]`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
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
        """执行 `ExperimentService` 的`put``draft`智能体操作。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            agent_key: 智能体在当前实验或运行中的稳定唯一键。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。
            data: 待编码、解码、校验或持久化的原始数据。 类型：`dict[str, Any]`。
            partial: 结果是否只覆盖当前已提交边界而尚未达到请求的最终步骤。 类型：`bool`。 默认值：`False`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        draft = self.get_draft(experiment_id)
        payload = draft["definition"]
        agents = list(payload["agents"])
        existing_index = next(
            (
                index
                for index, item in enumerate(agents)
                if item["agent_key"] == agent_key
            ),
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
        """删除`draft`智能体。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            agent_key: 智能体在当前实验或运行中的稳定唯一键。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
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
        """执行 `ExperimentService` 的`batch``update`智能体集合操作。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。
            agent_keys: 需要查询、关联或提交结果的智能体稳定键集合。 类型：`list[str]`。
            changes: 传入当前算法的`changes`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict[str, Any]`。
            dry_run: 传入当前算法的`dry`运行；其结构与有效范围由类型注解和调用协议共同限定。 类型：`bool`。 默认值：`False`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        keys = set(agent_keys)
        if not keys or len(keys) > 500:
            raise ServiceError(
                "INVALID_AGENT_BATCH", "请选择 1–500 个 Agent", status_code=422
            )
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
            raise ServiceError(
                "REVISION_CONFLICT", "草稿已变化，请重新载入", status_code=409
            )
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
                    raise ServiceError(
                        "INVALID_AGENT_COORD", "批量位置必须是 [x, y]", status_code=422
                    )
                agent["coord"] = [int(coord[0]), int(coord[1])]
            if changes.get("append_goal"):
                agent["goals"] = [
                    *(agent.get("goals") or []),
                    str(changes["append_goal"]).strip(),
                ]
            if changes.get("add_tags"):
                agent["tags"] = _normalize_tags(
                    [*(agent.get("tags") or []), *changes["add_tags"]]
                )
            affected.append(
                {
                    "agent_key": agent["agent_key"],
                    "name": agent["name"],
                    "before": before,
                    "after": {
                        field: agent.get(field)
                        for field in (
                            "enabled",
                            "model_override",
                            "coord",
                            "goals",
                            "tags",
                        )
                    },
                }
            )
        if len(affected) != len(keys):
            missing = sorted(keys - {item["agent_key"] for item in affected})
            raise ServiceError(
                "AGENT_NOT_FOUND",
                "部分 Agent 不存在",
                status_code=404,
                details={"agent_keys": missing},
            )
        preview = {"affected": len(affected), "changes": affected}
        if dry_run:
            return {**preview, "dry_run": True, "lock_version": draft["lock_version"]}
        saved = self.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=expected_lock_version,
            definition=ExperimentDefinition.model_validate(payload),
        )
        return {**preview, "dry_run": False, "draft": saved}

    def validate_draft(self, experiment_id: str) -> dict[str, Any]:
        """校验`draft`。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory.begin() as session:
            _, revision = self._require_draft(session, experiment_id)
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
        """发布`draft`。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            draft_revision_id: 当前正在编辑且受乐观锁保护的草稿修订版本标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """

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
        """发布`draft``in``session`。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            draft_revision_id: 当前正在编辑且受乐观锁保护的草稿修订版本标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。

        返回:
            返回 `ExperimentRevision` 类型的处理结果。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """

        experiment, revision = self._require_draft(session, experiment_id)
        if (
            revision.id != draft_revision_id
            or revision.lock_version != expected_lock_version
        ):
            raise ServiceError(
                "REVISION_CONFLICT",
                "Draft changed; reload it before publishing",
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
        if not report.valid:
            raise ServiceError(
                "CONFIG_VALIDATION_FAILED",
                "Experiment configuration did not pass publication validation",
                status_code=422,
                details=report.model_dump(mode="json"),
            )
        now = _utc_now()
        revision.definition_json = definition.model_dump(
            mode="json", exclude_none=False
        )
        flag_modified(revision, "definition_json")
        revision.definition_hash = report.definition_hash
        revision.validation_json = report.model_dump(mode="json")
        revision.validated_hash = report.definition_hash
        revision.state = RevisionState.PUBLISHED.value
        revision.snapshot_complete = True
        revision.published_at = now
        revision.updated_at = now
        experiment.current_draft_revision_id = None
        experiment.current_published_revision_id = revision.id
        experiment.updated_at = now
        session.flush()
        return revision

    def list_revisions(self, experiment_id: str) -> list[dict[str, Any]]:
        """查询`revisions`。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
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
        """获取修订版本。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            revision_id: 实验修订版本的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            revision = session.get(ExperimentRevision, revision_id)
            if revision is None or revision.experiment_id != experiment_id:
                raise not_found("revision", revision_id)
            return self._revision_detail(revision)

    def fork_revision(self, experiment_id: str, revision_id: str) -> dict[str, Any]:
        """执行 `ExperimentService` 的`fork`修订版本操作。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            revision_id: 实验修订版本的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
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
            if (
                source is None
                or source.experiment_id != experiment_id
                or source.state != RevisionState.PUBLISHED
            ):
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
            draft_payload = ExperimentDefinition.model_validate(
                source.definition_json
            ).model_dump(mode="json", exclude_none=False)
            draft_definition = ExperimentDefinition.model_validate(draft_payload)
            draft = ExperimentRevision(
                id=str(uuid4()),
                experiment_id=experiment_id,
                revision_no=next_no,
                state=RevisionState.DRAFT.value,
                base_revision_id=source.id,
                schema_version=source.schema_version,
                definition_json=draft_definition.model_dump(
                    mode="json", exclude_none=False
                ),
                definition_hash=definition_hash(draft_definition),
                validation_json=None,
                validated_hash=None,
                provenance_json={
                    **(source.provenance_json or {}),
                    "source_type": "FORK",
                    "source_revision_id": source.id,
                },
                snapshot_complete=False,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            experiment.current_draft_revision_id = draft.id
            experiment.status = ExperimentStatus.DRAFT.value
            experiment.updated_at = now
            return self._revision_detail(draft)

    def set_archived(
        self,
        experiment_id: str,
        *,
        archived: bool,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        """执行 `ExperimentService` 的`set``archived`操作。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            archived: 归档范围筛选值：`active`、`archived` 或 `all`。 类型：`bool`。
            expected_row_version: 调用方读取记录时看到的行版本；不一致时拒绝覆盖他人更新。 类型：`int | None`。 默认值：`None`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None:
                raise not_found("experiment", experiment_id)
            if (
                expected_row_version is not None
                and experiment.row_version != expected_row_version
            ):
                raise ServiceError(
                    "EXPERIMENT_CONFLICT",
                    "实验已被其他请求修改，请重新载入",
                    status_code=409,
                )
            if archived and experiment.status in {
                ExperimentStatus.QUEUED,
                ExperimentStatus.RUNNING,
                ExperimentStatus.PAUSED,
            }:
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
        """执行 `ExperimentService` 的`batch``manage`操作。

        参数:
            experiment_ids: 需要批量处理的实验唯一标识集合。 类型：`list[str]`。
            action: 智能体当前选择或已经执行的行为记录。 类型：`Literal['ARCHIVE', 'RESTORE', 'ADD_TAGS', 'SET_OWNER']`。
            owner: 所有者名称筛选值；为空时不限制所有者。 类型：`str | None`。 默认值：`None`。
            tags: 用于分类、检索或展示目标对象的去重标签集合。 类型：`list[str] | None`。 默认值：`None`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        ids = list(dict.fromkeys(experiment_ids))
        if not ids or len(ids) > 200:
            raise ServiceError("INVALID_BATCH", "请选择 1–200 个实验", status_code=422)
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            experiments = list(
                session.scalars(select(Experiment).where(Experiment.id.in_(ids)))
            )
            if len(experiments) != len(ids):
                raise ServiceError(
                    "BATCH_TARGET_MISSING", "部分实验不存在", status_code=404
                )
            if action == "ARCHIVE" and any(
                item.status
                in {
                    ExperimentStatus.QUEUED,
                    ExperimentStatus.RUNNING,
                    ExperimentStatus.PAUSED,
                }
                for item in experiments
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
                    experiment.tags = _normalize_tags(
                        [*(experiment.tags or []), *(tags or [])]
                    )
                elif action == "SET_OWNER":
                    experiment.owner = (owner or "").strip()[:120]
                else:
                    raise ServiceError(
                        "INVALID_BATCH_ACTION", "批量操作无效", status_code=422
                    )
                experiment.row_version += 1
                experiment.updated_at = now
            return {"action": action, "affected": len(experiments), "ids": ids}

    def estimate_run(self, experiment_id: str) -> dict[str, Any]:
        """执行 `ExperimentService` 的`estimate`运行操作。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        with self.database.session_factory() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None:
                raise not_found("experiment", experiment_id)
            revision_id = (
                experiment.current_draft_revision_id
                or experiment.current_published_revision_id
            )
            revision = (
                session.get(ExperimentRevision, revision_id) if revision_id else None
            )
            if revision is None:
                raise ServiceError(
                    "REVISION_NOT_FOUND",
                    "Experiment has no version to estimate",
                    status_code=409,
                )
            definition = ExperimentDefinition.model_validate(revision.definition_json)
            agents = sum(agent.enabled for agent in definition.agents)
            steps = definition.simulation.max_steps
            agent_steps = agents * steps
            calls_low = max(0, round(agent_steps * 0.35))
            calls_high = max(calls_low, round(agent_steps * 0.9))
            token_low = calls_low * 300
            token_high = calls_high * 1400
            thresholds = []
            if agents >= 20:
                thresholds.append("Agent count is at town scale")
            if steps >= 500:
                thresholds.append("Run length is at long-simulation scale")
            if definition.results.capture_model_payloads:
                thresholds.append(
                    "Model payload capture increases storage and privacy exposure"
                )
            return {
                "experiment_id": experiment_id,
                "revision_id": revision.id,
                "basis": "Skill-brain estimate based on enabled Agents and simulation steps",
                "scale": {
                    "execution_mode": "SKILL_BRAIN",
                    "brain_skill": definition.engine.brain_skill,
                    "agents": agents,
                    "steps": steps,
                    "virtual_minutes": steps * definition.simulation.stride_minutes,
                    "projection_interval_steps": definition.results.agent_step_projection_interval_steps,
                    "capture_model_payloads": definition.results.capture_model_payloads,
                },
                "estimate": {
                    "model_calls": {"low": calls_low, "high": calls_high},
                    "tokens": {"low": token_low, "high": token_high},
                    "wall_seconds": {"low": calls_low * 2, "high": calls_high * 15},
                    "storage_bytes": {
                        "low": agent_steps * 500 + token_low * 2,
                        "high": agent_steps * 2_000 + token_high * 4,
                    },
                },
                "high_scale": bool(thresholds),
                "threshold_reasons": thresholds,
                "actual": self._latest_run_actual(session, experiment),
            }

    @staticmethod
    def _latest_run_actual(
        session: Session, experiment: Experiment
    ) -> dict[str, Any] | None:
        """执行`latest`运行`actual`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            experiment: 传入当前算法的实验；其结构与有效范围由类型注解和调用协议共同限定。 类型：`Experiment`。

        返回:
            返回以字段名或业务键组织的结构化映射。 没有可用结果时返回 `None`。
        """
        if not experiment.latest_run_id:
            return None
        run = session.get(Run, experiment.latest_run_id)
        summary = session.get(RunResultSummary, experiment.latest_run_id)
        storage = (
            session.scalar(
                select(func.sum(RunArtifact.size_bytes)).where(
                    RunArtifact.run_id == experiment.latest_run_id,
                    RunArtifact.state == ArtifactState.READY,
                )
            )
            or 0
        )
        duration = None
        if run and run.started_at and run.finished_at:
            duration = max(0, int((run.finished_at - run.started_at).total_seconds()))
        return {
            "run_id": experiment.latest_run_id,
            "model_calls": summary.model_call_count if summary else None,
            "wall_seconds": duration,
            "storage_bytes": int(storage),
            "completed_steps": run.completed_steps if run else None,
        }

    def compare_experiments(self, experiment_ids: list[str]) -> dict[str, Any]:
        """执行 `ExperimentService` 的`compare``experiments`操作。

        参数:
            experiment_ids: 需要批量处理的实验唯一标识集合。 类型：`list[str]`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        ids = list(dict.fromkeys(experiment_ids))
        if len(ids) < 2 or len(ids) > 12:
            raise ServiceError(
                "INVALID_COMPARISON", "请选择 2–12 个实验进行比较", status_code=422
            )
        with self.database.session_factory() as session:
            experiments = list(
                session.scalars(select(Experiment).where(Experiment.id.in_(ids)))
            )
            by_id = {item.id: item for item in experiments}
            if len(by_id) != len(ids):
                raise ServiceError(
                    "COMPARISON_TARGET_MISSING", "部分实验不存在", status_code=404
                )
            documents = []
            flattened = []
            for experiment_id in ids:
                experiment = by_id[experiment_id]
                revision_id = (
                    experiment.current_draft_revision_id
                    or experiment.current_published_revision_id
                )
                revision = (
                    session.get(ExperimentRevision, revision_id)
                    if revision_id
                    else None
                )
                if revision is None:
                    raise ServiceError(
                        "REVISION_NOT_FOUND", "实验缺少可比较版本", status_code=409
                    )
                definition = ExperimentDefinition.model_validate(
                    revision.definition_json
                ).model_dump(mode="json", exclude_none=False)
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
            groups = {
                name: []
                for name in (
                    "experiment",
                    "agents",
                    "models",
                    "world",
                    "behavior",
                    "simulation",
                    "results",
                    "other",
                )
            }
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

    def save_comparison_group(
        self, name: str, experiment_ids: list[str]
    ) -> dict[str, Any]:
        """保存`comparison``group`。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。
            experiment_ids: 需要批量处理的实验唯一标识集合。 类型：`list[str]`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
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
            return {
                "id": row.id,
                "name": row.name,
                "experiment_ids": row.experiment_ids_json,
                "comparison": comparison,
            }

    def list_comparison_groups(self) -> list[dict[str, Any]]:
        """查询`comparison``groups`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            rows = list(
                session.scalars(
                    select(ExperimentComparisonGroup).order_by(
                        ExperimentComparisonGroup.updated_at.desc()
                    )
                )
            )
            return [
                {
                    "id": row.id,
                    "name": row.name,
                    "experiment_ids": row.experiment_ids_json,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in rows
            ]

    def save_view(self, name: str, query: dict[str, Any]) -> dict[str, Any]:
        """保存`view`。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。
            query: 用于名称、正文或标识模糊匹配的搜索文本。 类型：`dict[str, Any]`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            row = ExperimentSavedView(
                name=name.strip()[:120] or "未命名视图",
                query_json=query,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return {
                "id": row.id,
                "name": row.name,
                "share_key": row.share_key,
                "query": row.query_json,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }

    def list_views(self) -> list[dict[str, Any]]:
        """查询`views`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            rows = list(
                session.scalars(
                    select(ExperimentSavedView).order_by(
                        ExperimentSavedView.updated_at.desc()
                    )
                )
            )
            return [
                {
                    "id": row.id,
                    "name": row.name,
                    "share_key": row.share_key,
                    "query": row.query_json,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in rows
            ]

    def get_view_by_share_key(self, share_key: str) -> dict[str, Any]:
        """获取`view``by``share``key`。

        参数:
            share_key: 用于稳定定位`share`的键。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            row = session.scalar(
                select(ExperimentSavedView).where(
                    ExperimentSavedView.share_key == share_key
                )
            )
            if row is None:
                raise not_found("saved_view", share_key)
            return {
                "id": row.id,
                "name": row.name,
                "share_key": row.share_key,
                "query": row.query_json,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }

    @staticmethod
    def _require_draft(
        session: Session, experiment_id: str
    ) -> tuple[Experiment, ExperimentRevision]:
        """执行`require``draft`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            experiment_id: 实验记录的唯一标识。 类型：`str`。

        返回:
            返回按接口约定组织的结果集合。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        experiment = session.get(Experiment, experiment_id)
        if experiment is None:
            raise not_found("experiment", experiment_id)
        if not experiment.current_draft_revision_id:
            raise ServiceError(
                "DRAFT_BASE_UNAVAILABLE", "该实验当前没有可编辑草稿", status_code=409
            )
        revision = session.get(ExperimentRevision, experiment.current_draft_revision_id)
        if revision is None or revision.state != RevisionState.DRAFT:
            raise ServiceError(
                "DRAFT_BASE_UNAVAILABLE",
                "草稿引用无效，需要执行数据库对账",
                status_code=409,
            )
        return experiment, revision

    @staticmethod
    def _revision_detail(revision: ExperimentRevision | None) -> dict[str, Any]:
        """执行修订版本`detail`的内部处理，供当前模块或类复用。

        参数:
            revision: 当前读取、发布、克隆或校验的修订版本记录。 类型：`ExperimentRevision | None`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
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
