"""Lifecycle and validation for reusable map blocks and spatial objects."""

from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime, timezone
from math import ceil
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from generative_agents.config.hashing import canonical_json_bytes
from generative_agents.config.spatial_assets import SpatialAssetContract
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    SpatialAssetDefinition,
    SpatialAssetRevision,
)
from generative_agents.status import RevisionState

from .errors import ServiceError, not_found


def _now() -> datetime:
    """执行`now`的内部处理，供当前模块或类复用。

    返回:
        返回 `datetime` 类型的处理结果。
    """
    return datetime.now(timezone.utc)


def _digest(document: dict[str, Any]) -> str:
    """执行`digest`的内部处理，供当前模块或类复用。

    参数:
        document: 待校验、转换或持久化的结构化文档。 类型：`dict[str, Any]`。

    返回:
        返回处理后的文本或稳定标识。
    """
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _generated_key(name: str) -> str:
    """执行`generated``key`的内部处理，供当前模块或类复用。

    参数:
        name: 目标对象的人类可读名称。 类型：`str`。

    返回:
        返回处理后的文本或稳定标识。
    """
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:48]
    return f"{base or 'spatial-asset'}-{uuid4().hex[:8]}"


def _status_counts(session: Session) -> dict[str, int]:
    """执行`status``counts`的内部处理，供当前模块或类复用。

    参数:
        session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    counts = {RevisionState.DRAFT.value: 0, RevisionState.PUBLISHED.value: 0}
    for status, count in session.execute(
        select(SpatialAssetDefinition.status, func.count()).group_by(
            SpatialAssetDefinition.status
        )
    ):
        counts[status] = int(count)
    counts["ALL"] = sum(counts.values())
    return counts


def _builtin_contracts() -> dict[str, SpatialAssetContract]:
    """执行`builtin``contracts`的内部处理，供当前模块或类复用。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """

    def contract(
        name: str,
        kind: str,
        mode: str,
        value: str,
        *,
        surface: str = "GENERIC",
        collision: bool = False,
        traversal: list[str] | None = None,
        speed_limit_mps: float | None = None,
        tags: list[str] | None = None,
        initial_state: dict[str, Any] | None = None,
        state_variants: dict[str, dict[str, str]] | None = None,
        skill_bindings: list[dict[str, Any]] | None = None,
    ) -> SpatialAssetContract:
        """执行 的`contract`操作。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。
            kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`str`。
            mode: 选择当前操作行为的模式判别值；允许值由类型注解或调用协议限定。 类型：`str`。
            value: 当前操作使用的`value`。 类型：`str`。
            surface: 传入当前算法的`surface`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`str`。 默认值：`'GENERIC'`。
            collision: 路径或移动过程中检测到的碰撞信息。 类型：`bool`。 默认值：`False`。
            traversal: 传入当前算法的`traversal`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`list[str] | None`。 默认值：`None`。
            speed_limit_mps: 传入当前算法的`speed``limit``mps`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`float | None`。 默认值：`None`。
            tags: 用于分类、检索或展示目标对象的去重标签集合。 类型：`list[str] | None`。 默认值：`None`。
            initial_state: 传入当前算法的`initial``state`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict[str, Any] | None`。 默认值：`None`。
            state_variants: 传入当前算法的`state``variants`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict[str, dict[str, str]] | None`。 默认值：`None`。
            skill_bindings: 传入当前算法的技能`bindings`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`list[dict[str, Any]] | None`。 默认值：`None`。

        返回:
            返回按接口约定组织的结果集合。
        """
        appearance: dict[str, Any] = {
            "mode": mode,
            "scale": 1,
            "rotation_degrees": 0,
            "state_variants": state_variants or {},
        }
        appearance[{"COLOR": "color", "EMOJI": "emoji"}[mode]] = value
        return SpatialAssetContract.model_validate(
            {
                "name": name,
                "kind": kind,
                "appearance": appearance,
                "physics": {
                    "collision": collision,
                    "width_m": 1,
                    "height_m": 1,
                    "z_index": 0 if kind == "TILE" else 10,
                    "traversable_by": traversal or ["ALL"],
                    "speed_limit_mps": speed_limit_mps,
                },
                "semantics": {
                    "tags": tags or [],
                    "address_role": "OBJECT" if kind == "OBJECT" else "NONE",
                    "surface": surface,
                    "emits_presence_events": kind == "ZONE",
                },
                "initial_state": initial_state or {},
                "skill_bindings": skill_bindings or [],
            }
        )

    return {
        "tile-ground": contract("基础地面", "TILE", "COLOR", "#dce9df"),
        "tile-road-asphalt": contract(
            "沥青车道",
            "TILE",
            "COLOR",
            "#4b5563",
            surface="ROAD",
            traversal=["CAR", "BICYCLE", "MOTORCYCLE"],
            speed_limit_mps=13.9,
            tags=["road", "vehicle-lane"],
        ),
        "tile-sidewalk": contract(
            "人行道",
            "TILE",
            "COLOR",
            "#c9d3c2",
            surface="SIDEWALK",
            traversal=["PEDESTRIAN", "BICYCLE"],
            tags=["sidewalk"],
        ),
        "marking-crosswalk": contract(
            "斑马线",
            "MARKING",
            "COLOR",
            "#f8fafc",
            surface="CROSSWALK",
            traversal=["PEDESTRIAN", "CAR", "BICYCLE", "MOTORCYCLE"],
            tags=["crosswalk", "conflict-zone"],
        ),
        "object-traffic-light": contract(
            "交通信号灯",
            "OBJECT",
            "EMOJI",
            "🚦",
            traversal=["ALL"],
            tags=["traffic-light", "signal-controller"],
            initial_state={
                "state": "VEHICLE_GREEN",
                "phase": "VEHICLE_GREEN",
            },
            state_variants={
                "vehicle-green": {"emoji": "🟢"},
                "vehicle-yellow": {"emoji": "🟡"},
                "vehicle-red": {"emoji": "🔴"},
            },
            skill_bindings=[
                {
                    "interaction_key": "query-pedestrian-signal",
                    "skill_name": "traffic-signal-state",
                    "description": "查询当前行人是否可以安全通过斑马线",
                    "interaction_radius_m": 2.5,
                    "default_request": "请告诉我当前行人信号，以及现在是否可以过马路。",
                }
            ],
        ),
        "zone-pedestrian-wait": contract(
            "行人等待区",
            "ZONE",
            "COLOR",
            "#8bd3c7",
            traversal=["PEDESTRIAN"],
            tags=["pedestrian-wait-zone", "presence-sensing"],
        ),
        "marking-vehicle-stop-line": contract(
            "车辆停止线",
            "MARKING",
            "COLOR",
            "#ffffff",
            surface="ROAD",
            traversal=["CAR", "BICYCLE", "MOTORCYCLE"],
            tags=["stop-line"],
        ),
        "object-vehicle-gate": contract(
            "园区车辆门禁",
            "OBJECT",
            "EMOJI",
            "🚧",
            collision=True,
            traversal=["CAR"],
            tags=["vehicle-gate", "credential-checkpoint"],
            initial_state={
                "state": "closed",
                "required_credential": "company.vehicle.enter",
            },
            state_variants={
                "open": {"emoji": "✅"},
                "closed": {"emoji": "🚧"},
            },
        ),
        "zone-parking-slot": contract(
            "停车位",
            "ZONE",
            "COLOR",
            "#b7d8cc",
            traversal=["CAR"],
            tags=["parking-slot", "occupancy-sensing"],
            initial_state={"occupied": False, "reserved_by": None},
        ),
    }


class SpatialAssetService:
    def __init__(self, database: Database) -> None:
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。

        返回:
            无返回值。
        """
        self.database = database

    def ensure_builtin_assets(self) -> None:
        """确保`builtin`资源集合。

        返回:
            无返回值。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        with self.database.session_factory.begin() as session:
            contracts = _builtin_contracts()
            for asset_key, contract in contracts.items():
                existing = session.scalar(
                    select(SpatialAssetDefinition).where(
                        SpatialAssetDefinition.asset_key == asset_key
                    )
                )
                now = _now()
                document = contract.model_dump(mode="json", exclude_none=False)
                contract_hash = _digest(document)
                if existing is not None:
                    current = session.get(
                        SpatialAssetRevision, existing.current_published_revision_id
                    )
                    if current is not None and current.contract_hash == contract_hash:
                        continue
                    revision_no = (
                        int(
                            session.scalar(
                                select(
                                    func.max(SpatialAssetRevision.revision_no)
                                ).where(
                                    SpatialAssetRevision.spatial_asset_id == existing.id
                                )
                            )
                            or 0
                        )
                        + 1
                    )
                    errors = self._publish_errors(session, contract)
                    if errors:
                        raise RuntimeError(
                            f"invalid built-in spatial asset {asset_key}: {errors}"
                        )
                    revision = SpatialAssetRevision(
                        id=str(uuid4()),
                        spatial_asset_id=existing.id,
                        revision_no=revision_no,
                        state=RevisionState.PUBLISHED.value,
                        base_revision_id=current.id if current else None,
                        schema_version=contract.schema_version,
                        contract_json=document,
                        contract_hash=contract_hash,
                        validation_json={"valid": True, "errors": [], "warnings": []},
                        lock_version=1,
                        created_at=now,
                        updated_at=now,
                        published_at=now,
                    )
                    session.add(revision)
                    session.flush()
                    existing.current_published_revision_id = revision.id
                    existing.name = contract.name
                    existing.description = contract.summary
                    existing.row_version += 1
                    existing.updated_at = now
                    continue
                asset = SpatialAssetDefinition(
                    id=str(uuid4()),
                    asset_key=asset_key,
                    name=contract.name,
                    description=contract.summary,
                    asset_kind=contract.kind,
                    status=RevisionState.PUBLISHED.value,
                    is_builtin=True,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(asset)
                session.flush()
                revision = SpatialAssetRevision(
                    id=str(uuid4()),
                    spatial_asset_id=asset.id,
                    revision_no=1,
                    state=RevisionState.PUBLISHED.value,
                    schema_version=contract.schema_version,
                    contract_json=document,
                    contract_hash=contract_hash,
                    validation_json={"valid": True, "errors": [], "warnings": []},
                    lock_version=1,
                    created_at=now,
                    updated_at=now,
                    published_at=now,
                )
                session.add(revision)
                session.flush()
                asset.current_published_revision_id = revision.id

    def create_asset(
        self,
        *,
        name: str,
        description: str = "",
        asset_key: str | None = None,
        asset_kind: str = "TILE",
        source_revision_id: str | None = None,
        contract: SpatialAssetContract | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建资源。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。
            description: 目标对象的人类可读说明；会按业务规则去除无效空白。 类型：`str`。 默认值：`''`。
            asset_key: 用于稳定定位资源的键。 类型：`str | None`。 默认值：`None`。
            asset_kind: 空间资源类型筛选值；为空时包含所有资源类型。 类型：`str`。 默认值：`'TILE'`。
            source_revision_id: `source`修订版本的唯一标识。 类型：`str | None`。 默认值：`None`。
            contract: 已经通过结构校验的领域协议对象。 类型：`SpatialAssetContract | dict[str, Any] | None`。 默认值：`None`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        name = name.strip()
        if not name:
            raise ServiceError(
                "INVALID_SPATIAL_ASSET_NAME", "空间资产名称不能为空", status_code=422
            )
        stable_key = asset_key or _generated_key(name)
        with self.database.session_factory.begin() as session:
            if session.scalar(
                select(SpatialAssetDefinition.id).where(
                    SpatialAssetDefinition.asset_key == stable_key
                )
            ):
                raise ServiceError(
                    "SPATIAL_ASSET_KEY_CONFLICT",
                    "空间资产稳定键已被使用",
                    status_code=409,
                )
            source = None
            if source_revision_id:
                source = session.get(SpatialAssetRevision, source_revision_id)
                if source is None or source.state != RevisionState.PUBLISHED.value:
                    raise not_found("spatial_asset_revision", source_revision_id)
                model = SpatialAssetContract.model_validate(source.contract_json)
            elif contract is not None:
                model = SpatialAssetContract.model_validate(contract)
            else:
                model = SpatialAssetContract.model_validate(
                    {
                        "name": name,
                        "summary": description,
                        "kind": asset_kind.upper(),
                        "appearance": {"mode": "COLOR", "color": "#dce9df"},
                    }
                )
            document = model.model_dump(mode="json", exclude_none=False)
            now = _now()
            asset = SpatialAssetDefinition(
                id=str(uuid4()),
                asset_key=stable_key,
                name=name,
                description=description.strip()[:10_000],
                asset_kind=model.kind,
                status=RevisionState.DRAFT.value,
                is_builtin=False,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(asset)
            session.flush()
            revision = SpatialAssetRevision(
                id=str(uuid4()),
                spatial_asset_id=asset.id,
                revision_no=1,
                state=RevisionState.DRAFT.value,
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
            asset.current_draft_revision_id = revision.id
            return self._asset_detail(session, asset)

    def list_assets(
        self,
        *,
        query: str | None = None,
        status: RevisionState | str | None = None,
        asset_kind: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """查询资源集合。

        参数:
            query: 用于名称、正文或标识模糊匹配的搜索文本。 类型：`str | None`。 默认值：`None`。
            status: 目录对象状态筛选值。允许值：`DRAFT`（草稿）或 `PUBLISHED`（已发布）。 类型：`RevisionState | str | None`。 默认值：`None`。
            asset_kind: 空间资源类型筛选值；为空时包含所有资源类型。 类型：`str | None`。 默认值：`None`。
            page: 从 1 开始的分页页码。 类型：`int`。 默认值：`1`。
            page_size: 每页最多返回的记录数量。 类型：`int`。 默认值：`50`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        if page < 1 or not 1 <= page_size <= 100:
            raise ServiceError(
                "INVALID_PAGINATION", "空间资产分页参数无效", status_code=422
            )
        try:
            normalized_status = (
                RevisionState(str(status).upper()).value if status else None
            )
        except ValueError as exc:
            raise ServiceError(
                "INVALID_SPATIAL_ASSET_STATUS", "空间资产状态筛选无效", status_code=422
            ) from exc
        normalized_kind = asset_kind.upper() if asset_kind else None
        if normalized_kind not in {
            None,
            "TILE",
            "OBJECT",
            "ZONE",
            "MARKING",
            "NETWORK",
        }:
            raise ServiceError(
                "INVALID_SPATIAL_ASSET_KIND", "空间资产类型筛选无效", status_code=422
            )
        with self.database.session_factory() as session:
            statement = select(SpatialAssetDefinition)
            count_statement = select(func.count()).select_from(SpatialAssetDefinition)
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                predicate = or_(
                    SpatialAssetDefinition.name.ilike(pattern),
                    SpatialAssetDefinition.asset_key.ilike(pattern),
                )
                statement = statement.where(predicate)
                count_statement = count_statement.where(predicate)
            if normalized_status:
                statement = statement.where(
                    SpatialAssetDefinition.status == normalized_status
                )
                count_statement = count_statement.where(
                    SpatialAssetDefinition.status == normalized_status
                )
            if normalized_kind:
                statement = statement.where(
                    SpatialAssetDefinition.asset_kind == normalized_kind
                )
                count_statement = count_statement.where(
                    SpatialAssetDefinition.asset_kind == normalized_kind
                )
            total = int(session.scalar(count_statement) or 0)
            rows = list(
                session.scalars(
                    statement.order_by(
                        SpatialAssetDefinition.is_builtin.desc(),
                        SpatialAssetDefinition.updated_at.desc(),
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return {
                "items": [self._asset_detail(session, item) for item in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, ceil(total / page_size)),
                "status_counts": _status_counts(session),
            }

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        """获取资源。

        参数:
            asset_id: 资源的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            asset = session.get(SpatialAssetDefinition, asset_id)
            if asset is None:
                raise not_found("spatial_asset", asset_id)
            return self._asset_detail(session, asset)

    def get_draft(self, asset_id: str) -> dict[str, Any]:
        """获取`draft`。

        参数:
            asset_id: 资源的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            asset, revision = self._require_draft(session, asset_id)
            return self._revision_detail(revision, asset)

    def update_draft(
        self,
        asset_id: str,
        *,
        expected_lock_version: int,
        contract: SpatialAssetContract | dict[str, Any],
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """更新`draft`。

        参数:
            asset_id: 资源的唯一标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。
            contract: 已经通过结构校验的领域协议对象。 类型：`SpatialAssetContract | dict[str, Any]`。
            name: 目标对象的人类可读名称。 类型：`str | None`。 默认值：`None`。
            description: 目标对象的人类可读说明；会按业务规则去除无效空白。 类型：`str | None`。 默认值：`None`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        model = SpatialAssetContract.model_validate(contract)
        document = model.model_dump(mode="json", exclude_none=False)
        now = _now()
        with self.database.session_factory.begin() as session:
            asset, revision = self._require_draft(session, asset_id)
            result = session.execute(
                update(SpatialAssetRevision)
                .where(
                    SpatialAssetRevision.id == revision.id,
                    SpatialAssetRevision.state == RevisionState.DRAFT.value,
                    SpatialAssetRevision.lock_version == expected_lock_version,
                )
                .values(
                    schema_version=model.schema_version,
                    contract_json=document,
                    contract_hash=_digest(document),
                    validation_json=None,
                    lock_version=SpatialAssetRevision.lock_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                actual = session.scalar(
                    select(SpatialAssetRevision.lock_version).where(
                        SpatialAssetRevision.id == revision.id
                    )
                )
                raise ServiceError(
                    "SPATIAL_ASSET_REVISION_CONFLICT",
                    "空间资产草稿已被其他请求修改",
                    status_code=409,
                    details={
                        "expected_lock_version": expected_lock_version,
                        "actual_lock_version": actual,
                    },
                )
            asset.asset_kind = model.kind
            if name is not None:
                asset.name = name.strip()
            if description is not None:
                asset.description = description.strip()[:10_000]
            asset.row_version += 1
            asset.updated_at = now
            session.flush()
            return self._revision_detail(
                session.get(SpatialAssetRevision, revision.id), asset
            )

    def publish_draft(
        self,
        asset_id: str,
        *,
        draft_revision_id: str,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        """发布`draft`。

        参数:
            asset_id: 资源的唯一标识。 类型：`str`。
            draft_revision_id: 当前正在编辑且受乐观锁保护的草稿修订版本标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        with self.database.session_factory.begin() as session:
            asset, revision = self._require_draft(session, asset_id)
            if asset.is_builtin:
                raise ServiceError(
                    "BUILTIN_SPATIAL_ASSET_IMMUTABLE",
                    "系统内置空间资产不可修改",
                    status_code=409,
                )
            if (
                revision.id != draft_revision_id
                or revision.lock_version != expected_lock_version
            ):
                raise ServiceError(
                    "SPATIAL_ASSET_REVISION_CONFLICT",
                    "空间资产草稿版本已经变化",
                    status_code=409,
                )
            contract = SpatialAssetContract.model_validate(revision.contract_json)
            errors = self._publish_errors(session, contract)
            if errors:
                raise ServiceError(
                    "SPATIAL_ASSET_VALIDATION_FAILED",
                    "空间资产没有通过发布校验",
                    status_code=422,
                    details={"valid": False, "errors": errors, "warnings": []},
                )
            now = _now()
            revision.validation_json = {"valid": True, "errors": [], "warnings": []}
            revision.state = RevisionState.PUBLISHED.value
            revision.published_at = now
            revision.updated_at = now
            asset.current_draft_revision_id = None
            asset.current_published_revision_id = revision.id
            asset.status = RevisionState.PUBLISHED.value
            asset.row_version += 1
            asset.updated_at = now
            session.flush()
            return self._revision_detail(revision, asset)

    def list_revisions(self, asset_id: str) -> list[dict[str, Any]]:
        """查询`revisions`。

        参数:
            asset_id: 资源的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            asset = session.get(SpatialAssetDefinition, asset_id)
            if asset is None:
                raise not_found("spatial_asset", asset_id)
            rows = list(
                session.scalars(
                    select(SpatialAssetRevision)
                    .where(SpatialAssetRevision.spatial_asset_id == asset_id)
                    .order_by(SpatialAssetRevision.revision_no.desc())
                )
            )
            return [
                self._revision_detail(item, asset, include_contract=False)
                for item in rows
            ]

    def get_revision(self, asset_id: str, revision_id: str) -> dict[str, Any]:
        """获取修订版本。

        参数:
            asset_id: 资源的唯一标识。 类型：`str`。
            revision_id: 实验修订版本的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        with self.database.session_factory() as session:
            asset = session.get(SpatialAssetDefinition, asset_id)
            revision = session.get(SpatialAssetRevision, revision_id)
            if asset is None:
                raise not_found("spatial_asset", asset_id)
            if revision is None or revision.spatial_asset_id != asset_id:
                raise not_found("spatial_asset_revision", revision_id)
            return self._revision_detail(revision, asset)

    def fork_revision(self, asset_id: str, revision_id: str) -> dict[str, Any]:
        """执行 `SpatialAssetService` 的`fork`修订版本操作。

        参数:
            asset_id: 资源的唯一标识。 类型：`str`。
            revision_id: 实验修订版本的唯一标识。 类型：`str`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        with self.database.session_factory.begin() as session:
            asset = session.get(SpatialAssetDefinition, asset_id)
            if asset is None:
                raise not_found("spatial_asset", asset_id)
            if asset.is_builtin:
                raise ServiceError(
                    "BUILTIN_SPATIAL_ASSET_IMMUTABLE",
                    "请基于系统空间资产创建新的自定义资产",
                    status_code=409,
                )
            if asset.current_draft_revision_id:
                raise ServiceError(
                    "SPATIAL_ASSET_DRAFT_EXISTS",
                    "该空间资产已有编辑中的草稿",
                    status_code=409,
                )
            source = session.get(SpatialAssetRevision, revision_id)
            if (
                source is None
                or source.spatial_asset_id != asset_id
                or source.state != RevisionState.PUBLISHED.value
            ):
                raise not_found("spatial_asset_revision", revision_id)
            number = (
                int(
                    session.scalar(
                        select(func.max(SpatialAssetRevision.revision_no)).where(
                            SpatialAssetRevision.spatial_asset_id == asset_id
                        )
                    )
                    or 0
                )
                + 1
            )
            now = _now()
            draft = SpatialAssetRevision(
                id=str(uuid4()),
                spatial_asset_id=asset_id,
                revision_no=number,
                state=RevisionState.DRAFT.value,
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
            asset.current_draft_revision_id = draft.id
            asset.status = RevisionState.DRAFT.value
            asset.row_version += 1
            asset.updated_at = now
            return self._revision_detail(draft, asset)

    @staticmethod
    def _publish_errors(
        session: Session, contract: SpatialAssetContract
    ) -> list[dict[str, Any]]:
        """发布`errors`。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            contract: 已经通过结构校验的领域协议对象。 类型：`SpatialAssetContract`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        return []

    @staticmethod
    def _require_draft(
        session: Session, asset_id: str
    ) -> tuple[SpatialAssetDefinition, SpatialAssetRevision]:
        """执行`require``draft`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            asset_id: 资源的唯一标识。 类型：`str`。

        返回:
            返回按接口约定组织的结果集合。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        asset = session.get(SpatialAssetDefinition, asset_id)
        if asset is None:
            raise not_found("spatial_asset", asset_id)
        if not asset.current_draft_revision_id:
            raise ServiceError(
                "SPATIAL_ASSET_DRAFT_UNAVAILABLE",
                "空间资产没有可编辑草稿",
                status_code=409,
            )
        revision = session.get(SpatialAssetRevision, asset.current_draft_revision_id)
        if revision is None or revision.state != RevisionState.DRAFT.value:
            raise ServiceError(
                "SPATIAL_ASSET_DRAFT_UNAVAILABLE",
                "空间资产草稿状态异常",
                status_code=409,
            )
        return asset, revision

    @staticmethod
    def _revision_summary(
        revision: SpatialAssetRevision | None,
    ) -> dict[str, Any] | None:
        """执行修订版本摘要的内部处理，供当前模块或类复用。

        参数:
            revision: 当前读取、发布、克隆或校验的修订版本记录。 类型：`SpatialAssetRevision | None`。

        返回:
            返回以字段名或业务键组织的结构化映射。 没有可用结果时返回 `None`。
        """
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
            "published_at": revision.published_at.isoformat()
            if revision.published_at
            else None,
        }

    def _asset_detail(
        self, session: Session, asset: SpatialAssetDefinition
    ) -> dict[str, Any]:
        """执行资源`detail`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`Session`。
            asset: 当前读取、校验或返回的资源持久化记录。 类型：`SpatialAssetDefinition`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        draft = (
            session.get(SpatialAssetRevision, asset.current_draft_revision_id)
            if asset.current_draft_revision_id
            else None
        )
        published = (
            session.get(SpatialAssetRevision, asset.current_published_revision_id)
            if asset.current_published_revision_id
            else None
        )
        active = draft or published
        return {
            "id": asset.id,
            "asset_key": asset.asset_key,
            "name": asset.name,
            "description": asset.description,
            "asset_kind": asset.asset_kind,
            "status": asset.status,
            "is_builtin": asset.is_builtin,
            "row_version": asset.row_version,
            "current_draft": self._revision_summary(draft),
            "current_published": self._revision_summary(published),
            "active_contract": copy.deepcopy(active.contract_json) if active else None,
            "created_at": asset.created_at.isoformat(),
            "updated_at": asset.updated_at.isoformat(),
        }

    def _revision_detail(
        self,
        revision: SpatialAssetRevision,
        asset: SpatialAssetDefinition,
        *,
        include_contract: bool = True,
    ) -> dict[str, Any]:
        """执行修订版本`detail`的内部处理，供当前模块或类复用。

        参数:
            revision: 当前读取、发布、克隆或校验的修订版本记录。 类型：`SpatialAssetRevision`。
            asset: 当前读取、校验或返回的资源持久化记录。 类型：`SpatialAssetDefinition`。
            include_contract: 是否在响应中包含完整协议定义；关闭时只返回摘要字段。 类型：`bool`。 默认值：`True`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        result = self._revision_summary(revision) or {}
        result.update(
            {
                "spatial_asset_id": asset.id,
                "asset_key": asset.asset_key,
                "asset_name": asset.name,
                "asset_kind": asset.asset_kind,
                "base_revision_id": revision.base_revision_id,
                "validation": revision.validation_json,
                "readonly": revision.state == RevisionState.PUBLISHED.value,
            }
        )
        if include_contract:
            result["contract"] = copy.deepcopy(revision.contract_json)
        return result


__all__ = ["SpatialAssetService"]
