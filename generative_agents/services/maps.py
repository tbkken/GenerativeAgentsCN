"""Reusable public map lifecycle and experiment-owned map overlays."""

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

from generative_agents.config import canonical_json_bytes, make_builtin_definition
from generative_agents.config.schema import WorldConfig, WorldOverlayConfig, make_blank_definition
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    ExperimentRevision,
    WorldMap,
    WorldMapRevision,
)

from .errors import ServiceError, not_found


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_key(name: str) -> str:
    ascii_key = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return f"{(ascii_key[:48].strip('-') or 'map')}-{uuid4().hex[:8]}"


def _merge_patch(document: Any, patch: Any) -> Any:
    """Apply RFC-7396 style merge semantics without mutating either input."""

    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(document) if isinstance(document, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = _merge_patch(result.get(key), value)
    return result


def normalize_public_world(world: WorldConfig | dict[str, Any]) -> WorldConfig:
    payload = WorldConfig.model_validate(world).model_dump(mode="json", exclude_none=False)
    payload.update(
        {
            "map_id": None,
            "map_revision_id": None,
            "map_revision_hash": None,
            "overlay": WorldOverlayConfig().model_dump(mode="json", exclude_none=False),
        }
    )
    return WorldConfig.model_validate(payload)


def world_hash(world: WorldConfig | dict[str, Any]) -> str:
    normalized = normalize_public_world(world)
    return hashlib.sha256(
        canonical_json_bytes(normalized.model_dump(mode="json", exclude_none=False))
    ).hexdigest()


def _validate_world_definition(world: WorldConfig) -> list[dict[str, str]]:
    """Validate the subset consumed directly by ``Maze`` before publication."""

    definition = world.definition
    errors: list[dict[str, str]] = []
    size = definition.get("size") if isinstance(definition, dict) else None
    if (
        not isinstance(size, list)
        or len(size) != 2
        or any(not isinstance(value, int) or value < 1 for value in size)
    ):
        errors.append(
            {
                "code": "WORLD_SIZE_INVALID",
                "path": "definition.size",
                "message": "地图尺寸必须是 [高度, 宽度]，且两项均为正整数",
            }
        )
        return errors
    height, width = size
    if not isinstance(definition.get("world"), str) or not definition["world"].strip():
        errors.append(
            {
                "code": "WORLD_NAME_REQUIRED",
                "path": "definition.world",
                "message": "运行时世界名称不能为空",
            }
        )
    tile_size = definition.get("tile_size")
    if not isinstance(tile_size, int) or tile_size < 1:
        errors.append(
            {
                "code": "WORLD_TILE_SIZE_INVALID",
                "path": "definition.tile_size",
                "message": "Tile 像素尺寸必须是正整数",
            }
        )
    address_keys = definition.get("tile_address_keys")
    if (
        not isinstance(address_keys, list)
        or not address_keys
        or address_keys[0] != "world"
        or any(not isinstance(value, str) or not value.strip() for value in address_keys)
    ):
        errors.append(
            {
                "code": "WORLD_ADDRESS_KEYS_INVALID",
                "path": "definition.tile_address_keys",
                "message": "地址层级必须从 world 开始，并至少包含一个有效层级",
            }
        )
        address_keys = ["world"]
    tiles = definition.get("tiles")
    if not isinstance(tiles, list):
        errors.append(
            {
                "code": "WORLD_TILES_INVALID",
                "path": "definition.tiles",
                "message": "地图 Tile 定义必须是数组",
            }
        )
        return errors
    seen: set[tuple[int, int]] = set()
    for index, tile in enumerate(tiles):
        coord = tile.get("coord") if isinstance(tile, dict) else None
        if (
            not isinstance(coord, list)
            or len(coord) != 2
            or any(not isinstance(value, int) for value in coord)
        ):
            errors.append(
                {
                    "code": "WORLD_TILE_COORD_INVALID",
                    "path": f"definition.tiles.{index}.coord",
                    "message": "Tile 坐标必须是 [x, y] 整数对",
                }
            )
            continue
        x, y = coord
        if not (0 <= x < width and 0 <= y < height):
            errors.append(
                {
                    "code": "WORLD_TILE_OUT_OF_BOUNDS",
                    "path": f"definition.tiles.{index}.coord",
                    "message": f"Tile 坐标 [{x}, {y}] 超出地图边界",
                }
            )
        if (x, y) in seen:
            errors.append(
                {
                    "code": "WORLD_TILE_DUPLICATED",
                    "path": f"definition.tiles.{index}.coord",
                    "message": f"Tile 坐标 [{x}, {y}] 重复定义",
                }
            )
        seen.add((x, y))
        address = tile.get("address") if isinstance(tile, dict) else None
        if address is not None and (
            not isinstance(address, list)
            or len(address) >= len(address_keys)
            or any(not isinstance(value, str) or not value.strip() for value in address)
        ):
            errors.append(
                {
                    "code": "WORLD_TILE_ADDRESS_INVALID",
                    "path": f"definition.tiles.{index}.address",
                    "message": "Tile 地址必须是非空字符串数组，且层级数不能超过地址定义",
                }
            )
    return errors


class WorldMapService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_builtin_map(self) -> dict[str, Any]:
        """Register the bundled town once and keep its first public revision immutable."""

        with self.database.session_factory.begin() as session:
            existing = session.scalar(select(WorldMap).where(WorldMap.map_key == "the-ville"))
            if existing is not None:
                return self._map_detail(session, existing)
            now = _utc_now()
            map_id = str(uuid4())
            public_map = WorldMap(
                id=map_id,
                map_key="the-ville",
                name="the Ville 标准小镇",
                description="内置公共地图，可被多个实验引用并在实验内独立微调。",
                status="DRAFT",
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(public_map)
            session.flush()
            world = normalize_public_world(
                make_builtin_definition(
                    key="builtin-map-catalog",
                    name="公共地图目录",
                ).world
            )
            payload = world.model_dump(mode="json", exclude_none=False)
            digest = world_hash(world)
            published = WorldMapRevision(
                id=str(uuid4()),
                map_id=map_id,
                revision_no=1,
                state="PUBLISHED",
                schema_version=1,
                world_json=payload,
                world_hash=digest,
                validation_json={"valid": True, "errors": [], "warnings": []},
                lock_version=1,
                created_at=now,
                updated_at=now,
                published_at=now,
            )
            session.add(published)
            session.flush()
            draft = WorldMapRevision(
                id=str(uuid4()),
                map_id=map_id,
                revision_no=2,
                state="DRAFT",
                base_revision_id=published.id,
                schema_version=1,
                world_json=copy.deepcopy(payload),
                world_hash=digest,
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            public_map.current_published_revision_id = published.id
            public_map.current_draft_revision_id = draft.id
            return self._map_detail(session, public_map)

    def create_map(
        self,
        *,
        name: str,
        description: str = "",
        source_revision_id: str | None = None,
        map_key: str | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ServiceError("INVALID_MAP_NAME", "地图名称不能为空", status_code=422)
        stable_key = map_key.strip() if map_key else _make_key(name)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", stable_key):
            raise ServiceError(
                "INVALID_MAP_KEY",
                "地图稳定键必须由小写字母、数字和连字符组成",
                status_code=422,
            )
        with self.database.session_factory.begin() as session:
            if session.scalar(select(WorldMap.id).where(WorldMap.map_key == stable_key)):
                raise ServiceError(
                    "MAP_KEY_CONFLICT", "地图稳定键已被使用", status_code=409
                )
            base_revision: WorldMapRevision | None = None
            if source_revision_id:
                base_revision = session.get(WorldMapRevision, source_revision_id)
                if base_revision is None or base_revision.state != "PUBLISHED":
                    raise not_found("map_revision", source_revision_id)
                world = normalize_public_world(base_revision.world_json)
            else:
                world = normalize_public_world(
                    make_blank_definition(key="blank-map", name="空白地图").world
                )
            now = _utc_now()
            public_map = WorldMap(
                id=str(uuid4()),
                map_key=stable_key,
                name=name,
                description=description,
                status="DRAFT",
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(public_map)
            session.flush()
            revision = WorldMapRevision(
                id=str(uuid4()),
                map_id=public_map.id,
                revision_no=1,
                state="DRAFT",
                base_revision_id=base_revision.id if base_revision else None,
                schema_version=1,
                world_json=world.model_dump(mode="json", exclude_none=False),
                world_hash=world_hash(world),
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(revision)
            session.flush()
            public_map.current_draft_revision_id = revision.id
            return self._map_detail(session, public_map)

    def list_maps(
        self,
        *,
        query: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ServiceError("INVALID_PAGINATION", "地图分页参数无效", status_code=422)
        with self.database.session_factory() as session:
            statement = select(WorldMap)
            count_statement = select(func.count()).select_from(WorldMap)
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                predicate = or_(WorldMap.name.ilike(pattern), WorldMap.map_key.ilike(pattern))
                statement = statement.where(predicate)
                count_statement = count_statement.where(predicate)
            total = int(session.scalar(count_statement) or 0)
            rows = list(
                session.scalars(
                    statement.order_by(WorldMap.updated_at.desc(), WorldMap.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return {
                "items": [self._map_detail(session, item) for item in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, ceil(total / page_size)),
            }

    def get_map(self, map_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            return self._map_detail(session, public_map)

    def get_draft(self, map_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            public_map, revision = self._require_draft(session, map_id)
            return self._revision_detail(revision, public_map)

    def get_revision(self, map_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            public_map = session.get(WorldMap, map_id)
            revision = session.get(WorldMapRevision, revision_id)
            if public_map is None:
                raise not_found("map", map_id)
            if revision is None or revision.map_id != map_id:
                raise not_found("map_revision", revision_id)
            return self._revision_detail(revision, public_map)

    def update_draft(
        self,
        map_id: str,
        *,
        expected_lock_version: int,
        world: WorldConfig | dict[str, Any],
    ) -> dict[str, Any]:
        normalized = normalize_public_world(world)
        digest = world_hash(normalized)
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            public_map, revision = self._require_draft(session, map_id)
            result = session.execute(
                update(WorldMapRevision)
                .where(
                    WorldMapRevision.id == revision.id,
                    WorldMapRevision.state == "DRAFT",
                    WorldMapRevision.lock_version == expected_lock_version,
                )
                .values(
                    world_json=normalized.model_dump(mode="json", exclude_none=False),
                    world_hash=digest,
                    validation_json=None,
                    lock_version=WorldMapRevision.lock_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                actual = session.scalar(
                    select(WorldMapRevision.lock_version).where(
                        WorldMapRevision.id == revision.id
                    )
                )
                raise ServiceError(
                    "MAP_REVISION_CONFLICT",
                    "地图草稿已被其他请求修改，请重新载入",
                    status_code=409,
                    details={
                        "expected_lock_version": expected_lock_version,
                        "actual_lock_version": actual,
                    },
                )
            public_map.updated_at = now
            public_map.row_version += 1
            session.flush()
            return self._revision_detail(session.get(WorldMapRevision, revision.id), public_map)

    def publish_draft(
        self,
        map_id: str,
        *,
        draft_revision_id: str,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            public_map, revision = self._require_draft(session, map_id)
            if revision.id != draft_revision_id or revision.lock_version != expected_lock_version:
                raise ServiceError(
                    "MAP_REVISION_CONFLICT",
                    "地图草稿已变化，请重新载入",
                    status_code=409,
                )
            world = normalize_public_world(revision.world_json)
            errors = _validate_world_definition(world)
            if errors:
                revision.validation_json = {"valid": False, "errors": errors, "warnings": []}
                raise ServiceError(
                    "MAP_VALIDATION_FAILED",
                    "地图未通过发布校验",
                    status_code=422,
                    details=revision.validation_json,
                )
            now = _utc_now()
            revision.world_json = world.model_dump(mode="json", exclude_none=False)
            revision.world_hash = world_hash(world)
            revision.validation_json = {"valid": True, "errors": [], "warnings": []}
            revision.state = "PUBLISHED"
            revision.published_at = now
            revision.updated_at = now
            public_map.current_draft_revision_id = None
            public_map.current_published_revision_id = revision.id
            public_map.status = "PUBLISHED"
            public_map.row_version += 1
            public_map.updated_at = now
            session.flush()
            return self._revision_detail(revision, public_map)

    def fork_revision(self, map_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            if public_map.current_draft_revision_id:
                raise ServiceError("MAP_DRAFT_EXISTS", "该地图已有编辑中的草稿", status_code=409)
            source = session.get(WorldMapRevision, revision_id)
            if source is None or source.map_id != map_id or source.state != "PUBLISHED":
                raise not_found("map_revision", revision_id)
            number = int(
                session.scalar(
                    select(func.max(WorldMapRevision.revision_no)).where(
                        WorldMapRevision.map_id == map_id
                    )
                )
                or 0
            ) + 1
            now = _utc_now()
            draft = WorldMapRevision(
                id=str(uuid4()),
                map_id=map_id,
                revision_no=number,
                state="DRAFT",
                base_revision_id=source.id,
                schema_version=source.schema_version,
                world_json=copy.deepcopy(source.world_json),
                world_hash=source.world_hash,
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            public_map.current_draft_revision_id = draft.id
            public_map.status = "DRAFT"
            public_map.row_version += 1
            public_map.updated_at = now
            return self._revision_detail(draft, public_map)

    def list_revisions(self, map_id: str) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            public_map = session.get(WorldMap, map_id)
            if public_map is None:
                raise not_found("map", map_id)
            revisions = list(
                session.scalars(
                    select(WorldMapRevision)
                    .where(WorldMapRevision.map_id == map_id)
                    .order_by(WorldMapRevision.revision_no.desc())
                )
            )
            return [self._revision_detail(item, public_map, include_world=False) for item in revisions]

    def select_for_experiment(
        self,
        experiment_id: str,
        *,
        expected_lock_version: int,
        map_revision_id: str,
    ) -> dict[str, Any]:
        with self.database.session_factory() as session:
            revision = session.get(WorldMapRevision, map_revision_id)
            if revision is None or revision.state != "PUBLISHED":
                raise not_found("map_revision", map_revision_id)
            world = self.materialize_world(revision, WorldOverlayConfig())
        from .experiments import ExperimentService

        return ExperimentService(self.database).patch_draft_section(
            experiment_id=experiment_id,
            section="world",
            expected_lock_version=expected_lock_version,
            data=world.model_dump(mode="json", exclude_none=False),
        )

    def update_experiment_overlay(
        self,
        experiment_id: str,
        *,
        expected_lock_version: int,
        overlay: WorldOverlayConfig | dict[str, Any],
    ) -> dict[str, Any]:
        from .experiments import ExperimentService

        experiment_service = ExperimentService(self.database)
        draft = experiment_service.get_draft(experiment_id)
        current = WorldConfig.model_validate(draft["definition"]["world"])
        if not current.map_revision_id:
            raise ServiceError(
                "EXPERIMENT_MAP_REQUIRED",
                "请先为实验选择一个已发布公共地图",
                status_code=409,
            )
        with self.database.session_factory() as session:
            revision = session.get(WorldMapRevision, current.map_revision_id)
            if revision is None or revision.state != "PUBLISHED":
                raise ServiceError(
                    "MAP_REVISION_UNAVAILABLE",
                    "实验引用的公共地图版本不可用",
                    status_code=409,
                )
            world = self.materialize_world(revision, WorldOverlayConfig.model_validate(overlay))
        return experiment_service.patch_draft_section(
            experiment_id=experiment_id,
            section="world",
            expected_lock_version=expected_lock_version,
            data=world.model_dump(mode="json", exclude_none=False),
        )

    def materialize_for_publish_in_session(
        self, session: Session, world: WorldConfig
    ) -> WorldConfig:
        if not world.map_revision_id:
            return world
        revision = session.get(WorldMapRevision, world.map_revision_id)
        if (
            revision is None
            or revision.state != "PUBLISHED"
            or revision.map_id != world.map_id
            or revision.world_hash != world.map_revision_hash
        ):
            raise ServiceError(
                "MAP_REVISION_CONFLICT",
                "实验引用的公共地图版本已失效",
                status_code=409,
            )
        return self.materialize_world(revision, world.overlay)

    @staticmethod
    def materialize_world(
        revision: WorldMapRevision, overlay: WorldOverlayConfig
    ) -> WorldConfig:
        base = normalize_public_world(revision.world_json)
        definition = _merge_patch(base.definition, overlay.definition_patch)
        assets = {item.logical_path: item for item in base.assets}
        for logical_path in overlay.removed_asset_paths:
            assets.pop(logical_path, None)
        for item in overlay.asset_additions:
            assets[item.logical_path] = item
        return WorldConfig(
            world_key=base.world_key,
            world_name=base.world_name,
            definition=definition,
            assets=list(assets.values()),
            map_id=revision.map_id,
            map_revision_id=revision.id,
            map_revision_hash=revision.world_hash,
            overlay=overlay,
        )

    def _require_draft(
        self, session: Session, map_id: str
    ) -> tuple[WorldMap, WorldMapRevision]:
        public_map = session.get(WorldMap, map_id)
        if public_map is None:
            raise not_found("map", map_id)
        revision = (
            session.get(WorldMapRevision, public_map.current_draft_revision_id)
            if public_map.current_draft_revision_id
            else None
        )
        if revision is None or revision.map_id != map_id or revision.state != "DRAFT":
            raise ServiceError("MAP_DRAFT_UNAVAILABLE", "地图没有可编辑草稿", status_code=409)
        return public_map, revision

    def _usage_experiment_ids(self, session: Session, map_id: str) -> set[str]:
        result: set[str] = set()
        revisions = session.execute(
            select(ExperimentRevision.experiment_id, ExperimentRevision.definition_json)
        )
        for experiment_id, payload in revisions:
            if ((payload or {}).get("world") or {}).get("map_id") == map_id:
                result.add(experiment_id)
        return result

    def _map_detail(self, session: Session, public_map: WorldMap) -> dict[str, Any]:
        draft = (
            session.get(WorldMapRevision, public_map.current_draft_revision_id)
            if public_map.current_draft_revision_id
            else None
        )
        published = (
            session.get(WorldMapRevision, public_map.current_published_revision_id)
            if public_map.current_published_revision_id
            else None
        )
        source = draft or published
        world = WorldConfig.model_validate(source.world_json) if source else None
        definition = world.definition if world else {}
        size = definition.get("size") if isinstance(definition, dict) else None
        return {
            "id": public_map.id,
            "map_key": public_map.map_key,
            "name": public_map.name,
            "description": public_map.description,
            "status": public_map.status,
            "row_version": public_map.row_version,
            "current_draft": self._revision_summary(draft),
            "current_published": self._revision_summary(published),
            "usage_count": len(self._usage_experiment_ids(session, public_map.id)),
            "dimensions": size if isinstance(size, list) else None,
            "tile_size": definition.get("tile_size") if isinstance(definition, dict) else None,
            "updated_at": public_map.updated_at.isoformat(),
            "created_at": public_map.created_at.isoformat(),
        }

    @staticmethod
    def _revision_summary(revision: WorldMapRevision | None) -> dict[str, Any] | None:
        if revision is None:
            return None
        return {
            "id": revision.id,
            "revision_no": revision.revision_no,
            "state": revision.state,
            "world_hash": revision.world_hash,
            "lock_version": revision.lock_version,
            "updated_at": revision.updated_at.isoformat(),
            "published_at": revision.published_at.isoformat() if revision.published_at else None,
        }

    def _revision_detail(
        self,
        revision: WorldMapRevision,
        public_map: WorldMap,
        *,
        include_world: bool = True,
    ) -> dict[str, Any]:
        result = self._revision_summary(revision) or {}
        result.update(
            {
                "map_id": public_map.id,
                "map_key": public_map.map_key,
                "map_name": public_map.name,
                "base_revision_id": revision.base_revision_id,
                "schema_version": revision.schema_version,
                "validation": revision.validation_json,
            }
        )
        if include_world:
            result["world"] = revision.world_json
        return result
