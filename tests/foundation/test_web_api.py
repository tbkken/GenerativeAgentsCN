"""Current Studio asset, secret and health HTTP contracts."""
import struct
import zlib
from fastapi.testclient import TestClient
from tests.studio_support import create_test_studio

def _test_png(width: int, height: int) -> bytes:
    """为本测试模块封装 ``_test_png`` 辅助步骤，减少重复的场景搭建代码。"""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        """为本测试模块封装 ``chunk`` 辅助步骤，减少重复的场景搭建代码。"""
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    rows = b"".join(b"\x00" + b"\x2c\x91\x76\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )

def test_asset_and_secret_http_contracts_are_safe_and_idempotent(database_url):
    """回归验证 ``test_asset_and_secret_http_contracts_are_safe_and_idempotent`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        payload = b'{"world":"http-test"}'
        first = client.post(
            "/api/studio/resources/assets",
            files={"file": ("maze.json", payload, "application/json")},
        )
        second = client.post(
            "/api/studio/resources/assets",
            files={"file": ("copy.json", payload, "application/json")},
        )
        assert first.status_code == 201
        assert second.json()["asset_id"] == first.json()["asset_id"]
        assert "relative_path" not in first.json()

        content = client.get(
            f"/api/studio/resources/assets/{first.json()['asset_id']}/content"
        )
        assert content.content == payload
        assert content.headers["etag"] == f'"{first.json()["sha256"]}"'
        assert client.get(
            f"/api/studio/resources/assets/{first.json()['asset_id']}/content",
            headers={"If-None-Match": content.headers["etag"]},
        ).status_code == 304

        invalid = client.post(
            "/api/studio/resources/assets",
            files={"file": ("notes.md", b"# not an allowed asset", "text/markdown")},
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]

        agent_images = client.post(
            "/api/studio/resources/agent-images",
            files={
                "portrait": ("portrait.png", _test_png(64, 64), "image/png"),
                "sprite": ("sprite.png", _test_png(128, 128), "image/png"),
            },
        )
        assert agent_images.status_code == 201, agent_images.text
        image_payload = agent_images.json()
        assert image_payload["sprite"]["content_url"].startswith("/api/studio/resources/assets/")
        sprite_content = client.get(image_payload["sprite"]["content_url"])
        assert sprite_content.content == _test_png(128, 128)
        assert sprite_content.headers["content-type"] == "image/png"
        assert "immutable" in sprite_content.headers["cache-control"]

        three_frame_sprite = client.post(
            "/api/studio/resources/agent-images",
            files={"sprite": ("sprite.png", _test_png(96, 128), "image/png")},
        )
        assert three_frame_sprite.status_code == 201
        assert three_frame_sprite.json()["sprite"]["width"] == 96

        invalid_sprite = client.post(
            "/api/studio/resources/agent-images",
            files={"sprite": ("sprite.png", _test_png(64, 128), "image/png")},
        )
        assert invalid_sprite.status_code == 422
        assert invalid_sprite.json()["detail"]

        secret = client.post(
            "/api/studio/secrets",
            json={"kind": "OPENAI_API_KEY", "value": "sk-http-secret"},
        )
        replacement = client.post(
            f"/api/studio/secrets/{secret.json()['secret_id']}/replacement",
            json={"kind": "OPENAI_API_KEY", "value": "sk-http-replacement"},
        )
        assert secret.status_code == replacement.status_code == 201
        assert replacement.json()["supersedes_id"] == secret.json()["secret_id"]
        assert "value" not in secret.text
        assert "sk-http" not in secret.text + replacement.text

def test_health_endpoint_checks_database_connectivity(database_url):
    """回归验证 ``test_health_endpoint_checks_database_connectivity`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        response = client.get("/api/studio/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "runtime_truth": "files", "database_owner": "ga_studio"}
