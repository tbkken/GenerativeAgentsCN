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

from generative_agents.config.capabilities import (
    CapabilityBundleContract,
    CapabilityContract,
)
from generative_agents.config.hashing import canonical_json_bytes
from generative_agents.config.spatial_assets import (
    SpatialAssetContract,
    SpatialCapabilityAttachment,
)
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    CapabilityBundleRevision,
    CapabilityDefinition,
    CapabilityRevision,
    SpatialAssetDefinition,
    SpatialAssetRevision,
)
from generative_agents.runtime.json_schema import validate_json_schema

from .errors import ServiceError, not_found


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _generated_key(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:48]
    return f"{base or 'spatial-asset'}-{uuid4().hex[:8]}"


def _status_counts(session: Session) -> dict[str, int]:
    counts = {"DRAFT": 0, "PUBLISHED": 0}
    for status, count in session.execute(
        select(SpatialAssetDefinition.status, func.count()).group_by(
            SpatialAssetDefinition.status
        )
    ):
        counts[status] = int(count)
    counts["ALL"] = sum(counts.values())
    return counts


def _builtin_contracts() -> dict[str, SpatialAssetContract]:
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
    ) -> SpatialAssetContract:
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
                "capability_attachments": [],
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
            initial_state={"phase": "vehicle-green"},
            state_variants={
                "vehicle-green": {"emoji": "🟢"},
                "vehicle-yellow": {"emoji": "🟡"},
                "vehicle-red": {"emoji": "🔴"},
            },
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
    }


class SpatialAssetService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_builtin_assets(self) -> None:
        with self.database.session_factory.begin() as session:
            contracts = _builtin_contracts()
            presence_definition = session.scalar(
                select(CapabilityDefinition).where(
                    CapabilityDefinition.capability_key == "spatial-zone-presence"
                )
            )
            if presence_definition and presence_definition.current_published_revision_id:
                wait_zone = contracts["zone-pedestrian-wait"]
                contracts["zone-pedestrian-wait"] = wait_zone.model_copy(
                    update={
                        "capability_attachments": [
                            SpatialCapabilityAttachment(
                                attachment_key="presence-sensor",
                                capability_revision_id=(
                                    presence_definition.current_published_revision_id
                                ),
                                parameters={
                                    "entity_types": ["PEDESTRIAN"],
                                    "debounce_ms": 200,
                                },
                                output_bindings={
                                    "entered": "event:${target}:entered",
                                    "left": "event:${target}:left",
                                    "presence": "state:${target}:presence",
                                },
                            )
                        ]
                    }
                )
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
                    revision_no = int(
                        session.scalar(
                            select(func.max(SpatialAssetRevision.revision_no)).where(
                                SpatialAssetRevision.spatial_asset_id == existing.id
                            )
                        )
                        or 0
                    ) + 1
                    errors = self._publish_errors(session, contract)
                    if errors:
                        raise RuntimeError(
                            f"invalid built-in spatial asset {asset_key}: {errors}"
                        )
                    revision = SpatialAssetRevision(
                        id=str(uuid4()),
                        spatial_asset_id=existing.id,
                        revision_no=revision_no,
                        state="PUBLISHED",
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
                    status="PUBLISHED",
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
                    state="PUBLISHED",
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
                    "SPATIAL_ASSET_KEY_CONFLICT", "空间资产稳定键已被使用", status_code=409
                )
            source = None
            if source_revision_id:
                source = session.get(SpatialAssetRevision, source_revision_id)
                if source is None or source.state != "PUBLISHED":
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
                status="DRAFT",
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
            asset.current_draft_revision_id = revision.id
            return self._asset_detail(session, asset)

    def list_assets(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        asset_kind: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        if page < 1 or not 1 <= page_size <= 100:
            raise ServiceError("INVALID_PAGINATION", "空间资产分页参数无效", status_code=422)
        normalized_status = status.upper() if status else None
        if normalized_status not in {None, "DRAFT", "PUBLISHED"}:
            raise ServiceError(
                "INVALID_SPATIAL_ASSET_STATUS", "空间资产状态筛选无效", status_code=422
            )
        normalized_kind = asset_kind.upper() if asset_kind else None
        if normalized_kind not in {None, "TILE", "OBJECT", "ZONE", "MARKING", "NETWORK"}:
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
                statement = statement.where(SpatialAssetDefinition.status == normalized_status)
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
        with self.database.session_factory() as session:
            asset = session.get(SpatialAssetDefinition, asset_id)
            if asset is None:
                raise not_found("spatial_asset", asset_id)
            return self._asset_detail(session, asset)

    def get_draft(self, asset_id: str) -> dict[str, Any]:
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
        model = SpatialAssetContract.model_validate(contract)
        document = model.model_dump(mode="json", exclude_none=False)
        now = _now()
        with self.database.session_factory.begin() as session:
            asset, revision = self._require_draft(session, asset_id)
            result = session.execute(
                update(SpatialAssetRevision)
                .where(
                    SpatialAssetRevision.id == revision.id,
                    SpatialAssetRevision.state == "DRAFT",
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
        with self.database.session_factory.begin() as session:
            asset, revision = self._require_draft(session, asset_id)
            if asset.is_builtin:
                raise ServiceError(
                    "BUILTIN_SPATIAL_ASSET_IMMUTABLE",
                    "系统内置空间资产不可修改",
                    status_code=409,
                )
            if revision.id != draft_revision_id or revision.lock_version != expected_lock_version:
                raise ServiceError(
                    "SPATIAL_ASSET_REVISION_CONFLICT", "空间资产草稿版本已经变化", status_code=409
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
            revision.state = "PUBLISHED"
            revision.published_at = now
            revision.updated_at = now
            asset.current_draft_revision_id = None
            asset.current_published_revision_id = revision.id
            asset.status = "PUBLISHED"
            asset.row_version += 1
            asset.updated_at = now
            session.flush()
            return self._revision_detail(revision, asset)

    def list_revisions(self, asset_id: str) -> list[dict[str, Any]]:
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
            return [self._revision_detail(item, asset, include_contract=False) for item in rows]

    def get_revision(self, asset_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            asset = session.get(SpatialAssetDefinition, asset_id)
            revision = session.get(SpatialAssetRevision, revision_id)
            if asset is None:
                raise not_found("spatial_asset", asset_id)
            if revision is None or revision.spatial_asset_id != asset_id:
                raise not_found("spatial_asset_revision", revision_id)
            return self._revision_detail(revision, asset)

    def fork_revision(self, asset_id: str, revision_id: str) -> dict[str, Any]:
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
                    "SPATIAL_ASSET_DRAFT_EXISTS", "该空间资产已有编辑中的草稿", status_code=409
                )
            source = session.get(SpatialAssetRevision, revision_id)
            if source is None or source.spatial_asset_id != asset_id or source.state != "PUBLISHED":
                raise not_found("spatial_asset_revision", revision_id)
            number = int(
                session.scalar(
                    select(func.max(SpatialAssetRevision.revision_no)).where(
                        SpatialAssetRevision.spatial_asset_id == asset_id
                    )
                )
                or 0
            ) + 1
            now = _now()
            draft = SpatialAssetRevision(
                id=str(uuid4()),
                spatial_asset_id=asset_id,
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
            asset.current_draft_revision_id = draft.id
            asset.status = "DRAFT"
            asset.row_version += 1
            asset.updated_at = now
            return self._revision_detail(draft, asset)

    @staticmethod
    def _publish_errors(
        session: Session, contract: SpatialAssetContract
    ) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        expected_target = {
            "TILE": "MAP_OBJECT",
            "OBJECT": "MAP_OBJECT",
            "MARKING": "MAP_OBJECT",
            "ZONE": "ZONE",
            "NETWORK": "WORLD",
        }[contract.kind]
        for index, attachment in enumerate(contract.capability_attachments):
            schema: dict[str, Any] | None = None
            input_keys: set[str] = set()
            output_keys: set[str] = set()
            required_inputs: set[str] = set()
            if attachment.capability_revision_id:
                revision = session.get(CapabilityRevision, attachment.capability_revision_id)
                if revision is None or revision.state != "PUBLISHED":
                    errors.append(
                        {
                            "code": "SPATIAL_CAPABILITY_UNAVAILABLE",
                            "path": f"capability_attachments.{index}.capability_revision_id",
                            "message": "空间资产必须引用已发布的能力版本",
                        }
                    )
                    continue
                capability = CapabilityContract.model_validate(revision.contract_json)
                if expected_target not in capability.targets and "WORLD" not in capability.targets:
                    errors.append(
                        {
                            "code": "SPATIAL_CAPABILITY_TARGET_MISMATCH",
                            "path": f"capability_attachments.{index}",
                            "message": f"该能力不能挂载到 {contract.kind}",
                        }
                    )
                schema = capability.parameters_schema
                input_keys = {item.key for item in capability.inputs}
                output_keys = {item.key for item in capability.outputs}
                required_inputs = {item.key for item in capability.inputs if item.required}
            else:
                revision = session.get(
                    CapabilityBundleRevision, attachment.capability_bundle_revision_id
                )
                if revision is None or revision.state != "PUBLISHED":
                    errors.append(
                        {
                            "code": "SPATIAL_CAPABILITY_BUNDLE_UNAVAILABLE",
                            "path": f"capability_attachments.{index}.capability_bundle_revision_id",
                            "message": "空间资产必须引用已发布的能力包版本",
                        }
                    )
                    continue
                bundle = CapabilityBundleContract.model_validate(
                    revision.composition_json
                )
                if expected_target not in bundle.targets and "WORLD" not in bundle.targets:
                    errors.append(
                        {
                            "code": "SPATIAL_CAPABILITY_BUNDLE_TARGET_MISMATCH",
                            "path": f"capability_attachments.{index}",
                            "message": f"该能力包不能挂载到 {contract.kind}",
                        }
                    )
                schema = bundle.exposed_parameters_schema
                input_keys = {item.key for item in bundle.exposed_inputs}
                output_keys = {item.key for item in bundle.exposed_outputs}
                required_inputs = {
                    item.key for item in bundle.exposed_inputs if item.required
                }
            unknown_inputs = sorted(set(attachment.input_bindings) - input_keys)
            unknown_outputs = sorted(set(attachment.output_bindings) - output_keys)
            missing_inputs = sorted(required_inputs - set(attachment.input_bindings))
            for code, path, values, label in (
                (
                    "SPATIAL_CAPABILITY_INPUT_UNKNOWN",
                    "input_bindings",
                    unknown_inputs,
                    "未公开输入",
                ),
                (
                    "SPATIAL_CAPABILITY_OUTPUT_UNKNOWN",
                    "output_bindings",
                    unknown_outputs,
                    "未公开输出",
                ),
                (
                    "SPATIAL_CAPABILITY_INPUT_REQUIRED",
                    "input_bindings",
                    missing_inputs,
                    "缺少必填输入",
                ),
            ):
                if values:
                    errors.append(
                        {
                            "code": code,
                            "path": f"capability_attachments.{index}.{path}",
                            "message": f"{label}：{', '.join(values)}",
                        }
                    )
            try:
                validate_json_schema(
                    attachment.parameters,
                    schema,
                    f"$.capability_attachments[{index}].parameters",
                )
            except ValueError as exc:
                errors.append(
                    {
                        "code": "SPATIAL_CAPABILITY_PARAMETERS_INVALID",
                        "path": f"capability_attachments.{index}.parameters",
                        "message": str(exc),
                    }
                )
        return errors

    @staticmethod
    def _require_draft(
        session: Session, asset_id: str
    ) -> tuple[SpatialAssetDefinition, SpatialAssetRevision]:
        asset = session.get(SpatialAssetDefinition, asset_id)
        if asset is None:
            raise not_found("spatial_asset", asset_id)
        if not asset.current_draft_revision_id:
            raise ServiceError(
                "SPATIAL_ASSET_DRAFT_UNAVAILABLE", "空间资产没有可编辑草稿", status_code=409
            )
        revision = session.get(SpatialAssetRevision, asset.current_draft_revision_id)
        if revision is None or revision.state != "DRAFT":
            raise ServiceError(
                "SPATIAL_ASSET_DRAFT_UNAVAILABLE", "空间资产草稿状态异常", status_code=409
            )
        return asset, revision

    @staticmethod
    def _revision_summary(
        revision: SpatialAssetRevision | None,
    ) -> dict[str, Any] | None:
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

    def _asset_detail(
        self, session: Session, asset: SpatialAssetDefinition
    ) -> dict[str, Any]:
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
        result = self._revision_summary(revision) or {}
        result.update(
            {
                "spatial_asset_id": asset.id,
                "asset_key": asset.asset_key,
                "asset_name": asset.name,
                "asset_kind": asset.asset_kind,
                "base_revision_id": revision.base_revision_id,
                "validation": revision.validation_json,
                "readonly": revision.state == "PUBLISHED",
            }
        )
        if include_contract:
            result["contract"] = copy.deepcopy(revision.contract_json)
        return result


__all__ = ["SpatialAssetService"]
