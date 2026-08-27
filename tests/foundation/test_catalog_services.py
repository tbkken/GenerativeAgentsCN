"""基础能力回归测试：覆盖 ``test_catalog_services`` 对应的行为、故障边界和回归约束。"""
from io import BytesIO
import struct
import zlib

import pytest

from generative_agents.services import ServiceError
from generative_agents.services.catalog import AssetService, SecretService


def _png(width: int, height: int) -> bytes:
    """为本测试模块封装 ``_png`` 辅助步骤，减少重复的场景搭建代码。"""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        """为本测试模块封装 ``chunk`` 辅助步骤，减少重复的场景搭建代码。"""
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    rows = b"".join(b"\x00" + b"\x19\x8f\x77\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def test_asset_registration_is_content_idempotent_without_exposing_path(database, tmp_path):
    """回归验证 ``test_asset_registration_is_content_idempotent_without_exposing_path`` 所描述的业务结果、故障边界和隔离约束。"""
    service = AssetService(database, var_dir=tmp_path / "var")
    payload = b'{"world":"test"}'

    first = service.upload(
        BytesIO(payload), logical_name="maze.json", media_type="application/json"
    )
    second = service.upload(
        BytesIO(payload), logical_name="renamed.json", media_type="application/json"
    )

    assert first["asset_id"] == second["asset_id"]
    assert second["deduplicated"] is True
    assert "relative_path" not in first
    record, path = service.content(first["asset_id"])
    assert path.read_bytes() == payload
    assert record.sha256 == first["sha256"]


def test_asset_validation_is_a_client_error_not_an_unhandled_exception(database, tmp_path):
    """回归验证 ``test_asset_validation_is_a_client_error_not_an_unhandled_exception`` 所描述的业务结果、故障边界和隔离约束。"""
    service = AssetService(database, var_dir=tmp_path / "var")
    with pytest.raises(ServiceError) as error:
        service.upload(
            BytesIO(b"not a supported asset"),
            logical_name="notes.md",
            media_type="text/markdown",
        )
    assert error.value.code == "INVALID_ASSET"
    assert error.value.status_code == 422


def test_agent_images_are_validated_and_saved_as_database_blobs(database, tmp_path):
    """回归验证 ``test_agent_images_are_validated_and_saved_as_database_blobs`` 所描述的业务结果、故障边界和隔离约束。"""
    var_dir = tmp_path / "var"
    service = AssetService(database, var_dir=var_dir)
    result = service.upload_database_images(
        {
            "portrait": (BytesIO(_png(32, 32)), "portrait.png"),
            "sprite": (BytesIO(_png(128, 128)), "sprite.png"),
        }
    )

    assert result["portrait"]["width"] == 32
    assert result["sprite"]["width"] == result["sprite"]["height"] == 128
    record, content = service.database_image_content(result["sprite"]["asset_id"])
    assert content == _png(128, 128)
    assert record.relative_path == ""
    assert not any(path.is_file() for path in (var_dir / "assets").rglob("*"))

    with pytest.raises(ServiceError, match="128×128"):
        service.upload_database_images(
            {"sprite": (BytesIO(_png(96, 128)), "legacy-sprite.png")}
        )


def test_secret_replacement_keeps_old_ciphertext_and_never_returns_plaintext(database, tmp_path):
    """回归验证 ``test_secret_replacement_keeps_old_ciphertext_and_never_returns_plaintext`` 所描述的业务结果、故障边界和隔离约束。"""
    service = SecretService(database, var_dir=tmp_path / "var")
    first = service.create(kind="OPENAI_API_KEY", value="sk-first-value")
    replacement = service.create(
        kind="OPENAI_API_KEY",
        value="sk-second-value",
        supersedes_id=first["secret_id"],
    )

    assert replacement["secret_id"] != first["secret_id"]
    assert replacement["supersedes_id"] == first["secret_id"]
    assert service.resolve_plaintext(first["secret_id"]) == "sk-first-value"
    assert service.resolve_plaintext(replacement["secret_id"]) == "sk-second-value"
    assert "value" not in first and "encrypted_value" not in first
