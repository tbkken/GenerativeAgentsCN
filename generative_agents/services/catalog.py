"""Database registration for content-addressed assets and immutable secrets."""

from __future__ import annotations

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

    def content(self, asset_id: str) -> tuple[Asset, Path]:
        with self._database.session_factory() as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                raise not_found("asset", asset_id)
            session.expunge(asset)
        path = self.store.resolve(asset.relative_path, expected_sha256=asset.sha256)
        return asset, path

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
