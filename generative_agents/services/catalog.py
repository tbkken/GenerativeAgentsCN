"""Database registration for content-addressed assets and immutable secrets."""

from __future__ import annotations

import hashlib
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from generative_agents.assets import AssetStore, AssetValidationError
from generative_agents.persistence.database import Database
from generative_agents.persistence.models import Asset, Secret
from generative_agents.security import MasterKeyStore, SecretCipher

from .errors import ServiceError, not_found


class AssetService:
    _PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
    _AGENT_IMAGE_MAX_BYTES = 2 * 1024 * 1024

    def __init__(
        self,
        database: Database,
        *,
        var_dir: str | Path,
        max_bytes: int = 50 * 1024 * 1024,
    ):
        self._database = database
        self.store = AssetStore(var_dir, max_bytes=max_bytes)

    def upload(
        self,
        stream: BinaryIO,
        *,
        logical_name: str,
        media_type: str | None,
    ) -> dict:
        try:
            blob = self.store.put_stream(
                stream,
                logical_name=logical_name,
                declared_media_type=media_type,
            )
        except AssetValidationError as exc:
            raise ServiceError(
                "INVALID_ASSET",
                str(exc),
                status_code=422,
            ) from exc
        try:
            with self._database.session_factory.begin() as session:
                asset = session.scalar(select(Asset).where(Asset.sha256 == blob.sha256))
                if asset is None:
                    asset = Asset(
                        sha256=blob.sha256,
                        logical_name=blob.logical_name,
                        media_type=blob.media_type,
                        size_bytes=blob.size_bytes,
                        relative_path=blob.relative_path,
                    )
                    session.add(asset)
                    session.flush()
                result = self._detail(asset, deduplicated=not blob.created)
        except IntegrityError:
            with self._database.session_factory() as session:
                asset = session.scalar(select(Asset).where(Asset.sha256 == blob.sha256))
                if asset is None:
                    raise
                result = self._detail(asset, deduplicated=True)
        return result

    def get(self, asset_id: str) -> dict:
        with self._database.session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                raise not_found("asset", asset_id)
            return self._detail(asset)

    def upload_database_images(
        self,
        images: dict[str, tuple[BinaryIO, str]],
    ) -> dict[str, dict]:
        """Validate and persist Agent images as database BLOBs, never filesystem files."""

        prepared: dict[str, tuple[bytes, str, str, int, int]] = {}
        for kind, (stream, logical_name) in images.items():
            if kind not in {"portrait", "sprite"}:
                raise ServiceError("INVALID_AGENT_IMAGE_KIND", "Agent 图片类型不受支持", status_code=422)
            data = stream.read(self._AGENT_IMAGE_MAX_BYTES + 1)
            if len(data) > self._AGENT_IMAGE_MAX_BYTES:
                raise ServiceError("AGENT_IMAGE_TOO_LARGE", "Agent 图片不能超过 2 MB", status_code=422)
            width, height = self._validate_agent_png(data, kind=kind)
            prepared[kind] = (
                data,
                logical_name or f"agent-{kind}.png",
                hashlib.sha256(data).hexdigest(),
                width,
                height,
            )

        if not prepared:
            raise ServiceError("AGENT_IMAGE_REQUIRED", "请至少选择一张 Agent 图片", status_code=422)

        result: dict[str, dict] = {}
        with self._database.session_factory.begin() as session:
            for kind, (data, logical_name, digest, width, height) in prepared.items():
                asset = session.scalar(select(Asset).where(Asset.sha256 == digest))
                deduplicated = asset is not None
                if asset is None:
                    asset = Asset(
                        sha256=digest,
                        logical_name=logical_name,
                        media_type="image/png",
                        size_bytes=len(data),
                        relative_path="",
                        content_blob=data,
                    )
                    session.add(asset)
                    session.flush()
                elif asset.content_blob is None:
                    # A byte-identical legacy filesystem asset may already exist.  Keep its
                    # old reference intact while making the image independently DB-backed.
                    asset.content_blob = data
                detail = self._detail(asset, deduplicated=deduplicated)
                detail.update(
                    {
                        "kind": kind,
                        "width": width,
                        "height": height,
                        "content_url": f"/api/v1/agent-images/{asset.id}/content",
                    }
                )
                result[kind] = detail
        return result

    def database_image_content(self, asset_id: str) -> tuple[Asset, bytes]:
        with self._database.session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                raise not_found("asset", asset_id)
            if asset.content_blob is None:
                raise ServiceError(
                    "AGENT_IMAGE_NOT_DATABASE_BACKED",
                    "该资源不是数据库 Agent 图片",
                    status_code=404,
                )
            content = bytes(asset.content_blob)
            session.expunge(asset)
        return asset, content

    def content(self, asset_id: str) -> tuple[Asset, Path]:
        with self._database.session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                raise not_found("asset", asset_id)
            session.expunge(asset)
        path = self.store.resolve(asset.relative_path, expected_sha256=asset.sha256)
        return asset, path

    @classmethod
    def _validate_agent_png(cls, data: bytes, *, kind: str) -> tuple[int, int]:
        if len(data) < 24 or not data.startswith(cls._PNG_SIGNATURE) or data[12:16] != b"IHDR":
            raise ServiceError("INVALID_AGENT_IMAGE", "Agent 图片必须是有效 PNG", status_code=422)
        width, height = struct.unpack(">II", data[16:24])
        if width < 1 or height < 1 or width > 4096 or height > 4096:
            raise ServiceError("INVALID_AGENT_IMAGE_SIZE", "Agent 图片尺寸无效", status_code=422)
        if kind == "portrait" and (width != height or width < 32):
            raise ServiceError(
                "INVALID_AGENT_PORTRAIT_SIZE",
                "头像必须是边长至少 32px 的正方形 PNG",
                status_code=422,
            )
        if kind == "sprite" and (width, height) != (128, 128):
            raise ServiceError(
                "INVALID_AGENT_SPRITE_SIZE",
                "4×4 行走图必须是 128×128 PNG（每格 32×32）",
                status_code=422,
            )
        return width, height

    @staticmethod
    def _detail(asset: Asset, *, deduplicated: bool | None = None) -> dict:
        result = {
            "asset_id": asset.id,
            "sha256": asset.sha256,
            "logical_name": asset.logical_name,
            "media_type": asset.media_type,
            "size_bytes": asset.size_bytes,
            "created_at": asset.created_at.isoformat(),
        }
        if deduplicated is not None:
            result["deduplicated"] = deduplicated
        return result


class SecretService:
    ALLOWED_KINDS = frozenset({"OPENAI_API_KEY", "GENERIC_TOKEN"})

    def __init__(self, database: Database, *, var_dir: str | Path):
        self._database = database
        self._cipher = SecretCipher(MasterKeyStore(var_dir).load_or_create())

    def create(
        self,
        *,
        kind: str,
        value: str,
        supersedes_id: str | None = None,
    ) -> dict:
        if kind not in self.ALLOWED_KINDS:
            raise ServiceError("INVALID_SECRET_KIND", "密钥类型不受支持", status_code=422)
        encrypted = self._cipher.encrypt(value)
        now = datetime.now(timezone.utc)
        with self._database.session_factory.begin() as session:
            if supersedes_id is not None:
                previous = session.get(Secret, supersedes_id)
                if previous is None:
                    raise not_found("secret", supersedes_id)
                if previous.kind != kind:
                    raise ServiceError(
                        "SECRET_KIND_MISMATCH",
                        "替代密钥必须保持相同类型",
                        status_code=422,
                    )
            secret = Secret(
                kind=kind,
                encrypted_value=encrypted.encrypted_value,
                fingerprint=encrypted.fingerprint,
                supersedes_id=supersedes_id,
                created_at=now,
            )
            session.add(secret)
            session.flush()
            return self._detail(secret)

    def get(self, secret_id: str) -> dict:
        with self._database.session_factory() as session:
            secret = session.get(Secret, secret_id)
            if secret is None:
                raise not_found("secret", secret_id)
            return self._detail(secret)

    def resolve_plaintext(self, secret_id: str) -> str:
        """Internal worker-only resolution; API responses must never call this."""

        with self._database.session_factory() as session:
            secret = session.get(Secret, secret_id)
            if secret is None:
                raise not_found("secret", secret_id)
            encrypted = secret.encrypted_value
        return self._cipher.decrypt(encrypted)

    @staticmethod
    def _detail(secret: Secret) -> dict:
        return {
            "secret_id": secret.id,
            "kind": secret.kind,
            "fingerprint": secret.fingerprint,
            "supersedes_id": secret.supersedes_id,
            "created_at": secret.created_at.isoformat(),
        }
