from io import BytesIO

import pytest

from generative_agents.services import ServiceError
from generative_agents.services.catalog import AssetService, SecretService


def test_asset_registration_is_content_idempotent_without_exposing_path(database, tmp_path):
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
    service = AssetService(database, var_dir=tmp_path / "var")
    with pytest.raises(ServiceError) as error:
        service.upload(
            BytesIO(b"not a supported asset"),
            logical_name="notes.md",
            media_type="text/markdown",
        )
    assert error.value.code == "INVALID_ASSET"
    assert error.value.status_code == 422


def test_secret_replacement_keeps_old_ciphertext_and_never_returns_plaintext(database, tmp_path):
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
