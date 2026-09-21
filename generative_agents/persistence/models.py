"""Studio author-resource SQLAlchemy mappings.

Only the Studio author database is mapped here. Runtime and Replay use file
protocols; experiment revisions, Run queues and result projections have no ORM.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """执行 的`utc``now`操作。

    返回:
        返回 `datetime` 类型的处理结果。
    """
    return datetime.now(timezone.utc)


def uuid_str() -> str:
    """执行 的`uuid``str`操作。

    返回:
        返回处理后的文本或稳定标识。
    """
    return str(uuid4())


class Base(DeclarativeBase):
    """所有 SQLAlchemy ORM 表模型共享的声明式基类。"""

    pass


class SeedResourceTombstone(Base):
    """Remember an explicitly deleted bundled seed so startup never recreates it."""

    __tablename__ = "seed_resource_tombstones"

    resource_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    resource_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class StudioSkill(Base):
    """Mutable public Skill author resource used by the package-first Studio."""

    __tablename__ = "studio_skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    skill_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    children_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    scripts_json: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    is_builtin: Mapped[bool] = mapped_column(nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("kind IN ('atomic','pack','brain')", name="ck_studio_skills_kind"),
        CheckConstraint("row_version >= 1", name="ck_studio_skills_row_version"),
        Index("ix_studio_skills_kind_updated", "kind", "updated_at"),
        Index("ix_studio_skills_archived", "archived_at", "updated_at"),
    )


class StudioAgent(Base):
    """Mutable map-independent Agent author resource.

    Coordinates and semantic addresses deliberately do not belong here.  They
    are experiment-owned placement data written into ``agents/index.json``.
    """

    __tablename__ = "studio_agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    agent_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("row_version >= 1", name="ck_studio_agents_row_version"),
        Index("ix_studio_agents_updated", "updated_at", "id"),
        Index("ix_studio_agents_archived", "archived_at", "updated_at"),
    )


class StudioCrowd(Base):
    """Mutable Studio selection set; never a Runtime relationship."""

    __tablename__ = "studio_crowds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    crowd_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    agent_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("row_version >= 1", name="ck_studio_crowds_row_version"),
        Index("ix_studio_crowds_updated", "updated_at", "id"),
        Index("ix_studio_crowds_archived", "archived_at", "updated_at"),
    )


class StudioModelPreset(Base):
    """Mutable reusable model configuration copied into an experiment package."""

    __tablename__ = "studio_model_presets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    preset_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("row_version >= 1", name="ck_studio_model_presets_row_version"),
        Index("ix_studio_model_presets_updated", "updated_at", "id"),
    )


class StudioEvaluator(Base):
    """Mutable reusable evaluator definition copied into an experiment package."""

    __tablename__ = "studio_evaluators"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    evaluator_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("row_version >= 1", name="ck_studio_evaluators_row_version"),
        Index("ix_studio_evaluators_updated", "updated_at", "id"),
    )


class WorldMap(Base):
    """Mutable authoring map; experiments freeze self-contained copies at publish time."""

    __tablename__ = "world_maps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    map_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    world_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    world_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="ck_world_maps_schema"),
        CheckConstraint("row_version >= 1", name="ck_world_maps_row_version"),
        Index("ix_world_maps_updated_at", "updated_at", "id"),
        Index("ix_world_maps_archived", "archived_at", "updated_at"),
    )


class SpatialAssetDefinition(Base):
    """Mutable reusable visual/physical map component identity."""

    __tablename__ = "spatial_asset_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    asset_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    asset_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    is_builtin: Mapped[bool] = mapped_column(nullable=False, default=False)
    schema_version: Mapped[str] = mapped_column(
        String(40), nullable=False, default="ga-spatial-asset/v2"
    )
    contract_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint(
            "asset_kind IN ('TILE','OBJECT','ZONE','MARKING','NETWORK')",
            name="ck_spatial_asset_definitions_kind",
        ),
        CheckConstraint(
            "row_version >= 1", name="ck_spatial_asset_definitions_version"
        ),
        Index("ix_spatial_asset_definitions_updated", "updated_at", "id"),
    )


class Secret(Base):
    """经主密钥加密保存的模型或外部服务凭据。"""

    __tablename__ = "secrets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("secrets.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    rewrapped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('OPENAI_API_KEY','GENERIC_TOKEN')", name="ck_secrets_kind"
        ),
    )


class Asset(Base):
    """按内容摘要去重的上传资产元数据和受控存储位置。"""

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    logical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_assets_size"),
        Index("ix_assets_sha256", "sha256"),
    )


class StudioPackageCatalog(Base):
    """Rebuildable Studio navigation index over authoritative package files."""

    __tablename__ = "studio_package_catalog"

    package_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    package_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36))
    location: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    package_status: Mapped[str | None] = mapped_column(String(32))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    is_archive: Mapped[bool] = mapped_column(nullable=False, default=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint(
            "package_kind IN ('experiment', 'run')",
            name="ck_studio_package_catalog_kind",
        ),
        Index("ix_studio_package_catalog_experiment", "experiment_id"),
        Index("ix_studio_package_catalog_run", "run_id"),
    )
