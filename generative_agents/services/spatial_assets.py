"""Mutable reusable spatial assets.

Maps reference stable asset ids and resolve current contracts. Published
experiments own the immutable copies used by Runs.
"""

from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime, timezone
from math import ceil
from typing import Any
from uuid import uuid4

from sqlalchemy import Text, cast, func, or_, select, update

from generative_agents.config.hashing import canonical_json_bytes
from generative_agents.config.spatial_assets import SpatialAssetContract
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    SeedResourceTombstone,
    SpatialAssetDefinition,
    WorldMap,
)

from .errors import ServiceError, not_found
from .timestamps import iso_utc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _generated_key(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:48]
    return f"{base or 'spatial-asset'}-{uuid4().hex[:8]}"


def _contract(
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
                "width_tiles": 1,
                "height_tiles": 1,
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


def _builtin_contracts() -> dict[str, SpatialAssetContract]:
    return {
        "tile-ground": _contract("基础地面", "TILE", "COLOR", "#dce9df"),
        "tile-road-asphalt": _contract(
            "沥青车道", "TILE", "COLOR", "#4b5563", surface="ROAD",
            traversal=["CAR", "BICYCLE", "MOTORCYCLE"], speed_limit_mps=13.9,
            tags=["road", "vehicle-lane"],
        ),
        "tile-sidewalk": _contract(
            "人行道", "TILE", "COLOR", "#c9d3c2", surface="SIDEWALK",
            traversal=["PEDESTRIAN", "BICYCLE"], tags=["sidewalk"],
        ),
        "marking-crosswalk": _contract(
            "斑马线", "MARKING", "COLOR", "#f8fafc", surface="CROSSWALK",
            traversal=["PEDESTRIAN", "CAR", "BICYCLE", "MOTORCYCLE"],
            tags=["crosswalk", "conflict-zone"],
        ),
        "object-traffic-light": _contract(
            "交通信号灯", "OBJECT", "EMOJI", "🚦",
            tags=["traffic-light", "signal-controller"],
            initial_state={"state": "VEHICLE_GREEN", "phase": "VEHICLE_GREEN"},
            state_variants={
                "vehicle-green": {"emoji": "🟢"},
                "vehicle-yellow": {"emoji": "🟡"},
                "vehicle-red": {"emoji": "🔴"},
            },
            skill_bindings=[{
                "interaction_key": "query-pedestrian-signal",
                "skill_name": "traffic-signal-state",
                "description": "查询当前行人是否可以安全通过斑马线",
                "interaction_radius_tiles": 2.5,
                "default_request": "请告诉我当前行人信号，以及现在是否可以过马路。",
            }],
        ),
        "zone-pedestrian-wait": _contract(
            "行人等待区", "ZONE", "COLOR", "#8bd3c7",
            traversal=["PEDESTRIAN"],
            tags=["pedestrian-wait-zone", "presence-sensing"],
        ),
        "marking-vehicle-stop-line": _contract(
            "车辆停止线", "MARKING", "COLOR", "#ffffff", surface="ROAD",
            traversal=["CAR", "BICYCLE", "MOTORCYCLE"], tags=["stop-line"],
        ),
        "object-vehicle-gate": _contract(
            "园区车辆门禁", "OBJECT", "EMOJI", "🚧", collision=True,
            traversal=["CAR"], tags=["vehicle-gate", "credential-checkpoint"],
            initial_state={"state": "closed", "required_credential": "company.vehicle.enter"},
            state_variants={"open": {"emoji": "✅"}, "closed": {"emoji": "🚧"}},
        ),
        "zone-parking-slot": _contract(
            "停车位", "ZONE", "COLOR", "#b7d8cc", traversal=["CAR"],
            tags=["parking-slot", "occupancy-sensing"],
            initial_state={"occupied": False, "reserved_by": None},
        ),
    }


class SpatialAssetService:
    """CRUD for mutable spatial assets with one optimistic-lock counter."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _invalidate_referencing_maps(session, asset_id: str) -> None:
        """Discard map validation derived from an asset contract that just changed."""
        session.execute(
            update(WorldMap)
            .where(cast(WorldMap.world_json, Text).like(f"%{asset_id}%"))
            .values(validation_json=None, updated_at=_now())
        )

    def ensure_builtin_assets(self) -> None:
        """Seed missing built-ins once and never overwrite user edits."""
        contracts = _builtin_contracts()
        with self.database.session_factory.begin() as session:
            tombstones = set(session.scalars(
                select(SeedResourceTombstone.resource_key).where(
                    SeedResourceTombstone.resource_type == "spatial_asset"
                )
            ))
            existing = set(session.scalars(select(SpatialAssetDefinition.asset_key)))
            now = _now()
            for key, model in contracts.items():
                if key in existing or key in tombstones:
                    continue
                document = model.model_dump(mode="json", exclude_none=False)
                session.add(SpatialAssetDefinition(
                    id=str(uuid4()), asset_key=key, name=model.name,
                    description=model.summary, asset_kind=model.kind, is_builtin=True,
                    schema_version=model.schema_version, contract_json=document,
                    contract_hash=_digest(document),
                    validation_json={"valid": True, "errors": [], "warnings": []},
                    row_version=1, created_at=now, updated_at=now,
                ))

    def create_asset(
        self,
        *,
        name: str,
        asset_kind: str = "TILE",
        description: str = "",
        asset_key: str | None = None,
        source_asset_id: str | None = None,
        contract: SpatialAssetContract | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ServiceError(
                "INVALID_ASSET_NAME", "空间资产名称不能为空", status_code=422
            )
        key = asset_key.strip() if asset_key else _generated_key(name)
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", key):
            raise ServiceError(
                "INVALID_ASSET_KEY", "空间资产键格式无效", status_code=422
            )
        with self.database.session_factory.begin() as session:
            if session.scalar(select(SpatialAssetDefinition.id).where(
                SpatialAssetDefinition.asset_key == key
            )):
                raise ServiceError(
                    "ASSET_KEY_CONFLICT", "空间资产键已存在", status_code=409
                )
            source = session.get(SpatialAssetDefinition, source_asset_id) if source_asset_id else None
            if source_asset_id and source is None:
                raise not_found("spatial_asset", source_asset_id)
            if contract is not None:
                model = SpatialAssetContract.model_validate(contract)
            elif source is not None:
                model = SpatialAssetContract.model_validate(source.contract_json)
            else:
                model = _contract(
                    name, asset_kind, "EMOJI" if asset_kind == "OBJECT" else "COLOR",
                    "📦" if asset_kind == "OBJECT" else "#dce9df",
                )
            document = model.model_copy(update={"name": name}).model_dump(
                mode="json", exclude_none=False
            )
            now = _now()
            asset = SpatialAssetDefinition(
                id=str(uuid4()), asset_key=key, name=name,
                description=description.strip(), asset_kind=document["kind"],
                is_builtin=False, schema_version=document["schema_version"],
                contract_json=document, contract_hash=_digest(document),
                validation_json={"valid": True, "errors": [], "warnings": []},
                row_version=1, created_at=now, updated_at=now,
            )
            session.add(asset)
            session.flush()
            return self._detail(session, asset)

    def list_assets(
        self,
        *,
        query: str | None = None,
        asset_kind: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        self.ensure_builtin_assets()
        filters = []
        if query:
            like = f"%{query.strip()}%"
            filters.append(or_(
                SpatialAssetDefinition.name.ilike(like),
                SpatialAssetDefinition.asset_key.ilike(like),
                SpatialAssetDefinition.description.ilike(like),
            ))
        if asset_kind:
            filters.append(SpatialAssetDefinition.asset_kind == asset_kind)
        with self.database.session_factory() as session:
            total = int(session.scalar(
                select(func.count()).select_from(SpatialAssetDefinition).where(*filters)
            ) or 0)
            rows = list(session.scalars(
                select(SpatialAssetDefinition).where(*filters)
                .order_by(SpatialAssetDefinition.updated_at.desc(), SpatialAssetDefinition.id)
                .offset((page - 1) * page_size).limit(page_size)
            ))
            return {
                "items": [self._detail(session, item) for item in rows],
                "page": page, "page_size": page_size, "total": total,
                "total_pages": max(1, ceil(total / page_size)),
            }

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            asset = session.get(SpatialAssetDefinition, asset_id)
            if asset is None:
                raise not_found("spatial_asset", asset_id)
            return self._detail(session, asset)

    def update_asset(
        self,
        asset_id: str,
        *,
        expected_row_version: int,
        contract: SpatialAssetContract | dict[str, Any],
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        model = SpatialAssetContract.model_validate(contract)
        document = model.model_dump(mode="json", exclude_none=False)
        resolved_name = name.strip() if name is not None else str(document["name"]).strip()
        if not resolved_name:
            raise ServiceError(
                "INVALID_ASSET_NAME", "空间资产名称不能为空", status_code=422
            )
        document["name"] = resolved_name
        values: dict[str, Any] = {
            "asset_kind": model.kind, "schema_version": model.schema_version,
            "contract_json": document, "contract_hash": _digest(document),
            "validation_json": {"valid": True, "errors": [], "warnings": []},
            "name": resolved_name,
            "row_version": SpatialAssetDefinition.row_version + 1, "updated_at": _now(),
        }
        if description is not None:
            values["description"] = description.strip()
        with self.database.session_factory.begin() as session:
            self._invalidate_referencing_maps(session, asset_id)
            result = session.execute(update(SpatialAssetDefinition).where(
                SpatialAssetDefinition.id == asset_id,
                SpatialAssetDefinition.row_version == expected_row_version,
            ).values(**values))
            if result.rowcount != 1:
                if session.get(SpatialAssetDefinition, asset_id) is None:
                    raise not_found("spatial_asset", asset_id)
                actual = session.scalar(select(SpatialAssetDefinition.row_version).where(
                    SpatialAssetDefinition.id == asset_id
                ))
                raise ServiceError(
                    "ASSET_CONFLICT",
                    "空间资产已被其他请求修改，请重新载入",
                    status_code=409,
                    details={"expected_row_version": expected_row_version,
                             "actual_row_version": actual},
                )
            session.flush()
            return self._detail(session, session.get(SpatialAssetDefinition, asset_id))

    def delete_asset(self, asset_id: str) -> None:
        """Delete authoring data without touching self-contained experiment snapshots."""
        with self.database.session_factory.begin() as session:
            asset = session.get(SpatialAssetDefinition, asset_id)
            if asset is None:
                raise not_found("spatial_asset", asset_id)
            self._invalidate_referencing_maps(session, asset_id)
            if asset.is_builtin:
                session.merge(SeedResourceTombstone(
                    resource_type="spatial_asset", resource_key=asset.asset_key
                ))
            session.delete(asset)

    @staticmethod
    def contract_map(session, asset_ids: set[str]) -> dict[str, dict[str, Any]]:
        if not asset_ids:
            return {}
        rows = session.scalars(select(SpatialAssetDefinition).where(
            SpatialAssetDefinition.id.in_(asset_ids)
        ))
        return {item.id: copy.deepcopy(item.contract_json) for item in rows}

    @staticmethod
    def _usage_count(session, asset_id: str) -> int:
        return int(session.scalar(
            select(func.count()).select_from(WorldMap).where(
                cast(WorldMap.world_json, Text).contains(asset_id)
            )
        ) or 0)

    def _detail(self, session, asset: SpatialAssetDefinition) -> dict[str, Any]:
        contract = copy.deepcopy(asset.contract_json)
        return {
            "id": asset.id, "asset_key": asset.asset_key, "name": asset.name,
            "description": asset.description, "asset_kind": asset.asset_kind,
            "is_builtin": asset.is_builtin, "schema_version": asset.schema_version,
            "row_version": asset.row_version, "contract_hash": asset.contract_hash,
            "validation": copy.deepcopy(asset.validation_json), "contract": contract,
            "active_contract": copy.deepcopy(contract),
            "usage_count": self._usage_count(session, asset.id),
            "updated_at": iso_utc(asset.updated_at), "created_at": iso_utc(asset.created_at),
        }


__all__ = ["SpatialAssetService"]
