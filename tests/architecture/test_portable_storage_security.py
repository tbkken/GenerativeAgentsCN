"""Storage isolation migrated from DB artifacts/logs to actual portable Runs."""
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from generative_agents.ga_runtime.service import RunService
from tests.studio_support import create_test_studio
from tests.test_portable_package_protocol import _experiment


@pytest.fixture
def portable_storage(tmp_path):
    var = tmp_path / "var"
    root = var / "packages" / "runs" / "arbitrary-name"
    status = RunService().start(_experiment(tmp_path), root, requested_steps=1)
    log = root / "logs" / "runtime-process.log"
    log.parent.mkdir(exist_ok=True)
    log.write_text("private log marker\n", encoding="utf-8")
    artifact = root / "artifacts" / "report.txt"
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_bytes(b"private artifact marker")
    app = create_test_studio(database_url=f"sqlite:///{tmp_path / 'studio.db'}", var_dir=var)
    with TestClient(app) as client:
        response = client.post("/api/studio/packages/rebuild")
        assert response.status_code == 200, response.text
        yield client, root, status.run_id, status.active_attempt_id


def native_link(link, target):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        if os.getenv("GA_REQUIRE_NATIVE_SYMLINK_TESTS"):
            pytest.fail(f"native symlink release capability unavailable: {exc}")
        pytest.skip(f"native symlink capability unavailable: {exc}")
    assert link.is_symlink()


def substitute_link(path, link_factory=native_link, target=None):
    """Keep the original bytes and replace only the designated path with a link."""
    original = path.with_name(path.name + "-physical")
    path.rename(original)
    link_factory(path, target or original)
    return original


def assert_rejected(client, urls, root):
    for url in urls:
        response = client.get(url)
        assert response.status_code == 409, (url, response.text)
        assert str(root) not in response.text
        assert "private artifact marker" not in response.text
        assert "private log marker" not in response.text


def artifact_urls(run_id, content=b"private artifact marker"):
    digest = hashlib.sha256(content).hexdigest()[:32]
    return [f"/api/studio/runs/{run_id}/results/operations", f"/api/studio/runs/{run_id}/artifacts/{digest}/download"]


def replay_urls(run_id):
    return [f"/api/studio/runs/{run_id}/replay/manifest", f"/api/studio/runs/{run_id}/replay/steps?from_step=1&limit=1"]


def test_def_047_log_service_rejects_a_real_symlink_chain(portable_storage):
    client, root, run_id, attempt = portable_storage
    link = root / "logs"
    substitute_link(link)
    try:
        assert_rejected(client, [f"/api/studio/runs/{run_id}/attempts/{attempt}/log", f"/api/studio/runs/{run_id}/attempts/{attempt}/log/download"], root)
    finally:
        link.unlink()


@pytest.mark.parametrize("position", ["final_symlink", "intermediate_symlink"])
def test_def_061_artifact_preview_and_download_enforce_persisted_storage_integrity(portable_storage, position):
    client, root, run_id, _ = portable_storage
    link = root / "artifacts"
    if position == "final_symlink":
        link /= "report.txt"
    substitute_link(link)
    try:
        assert_rejected(client, artifact_urls(run_id), root)
    finally:
        link.unlink()


def test_def_061_artifact_cross_run_native_directory_symlink_is_rejected(portable_storage):
    client, root, run_id, _ = portable_storage
    other = root.parent / "other-run"
    RunService().start(root / "experiment", other, requested_steps=1)
    target = other / "artifacts"
    target.mkdir(exist_ok=True)
    (target / "report.txt").write_bytes(b"private artifact marker")
    link = root / "artifacts"
    substitute_link(link, target=target)
    try:
        assert_rejected(client, artifact_urls(run_id), root)
    finally:
        link.unlink()


@pytest.mark.parametrize("position", ["final_symlink", "intermediate_symlink"])
def test_def_063_replay_frame_integrity_blocks_manifest_window_and_artifact(portable_storage, position):
    client, root, run_id, _ = portable_storage
    link = root / "frames"
    if position == "final_symlink":
        link /= "step-000001.json.gz"
    substitute_link(link)
    try:
        assert_rejected(client, replay_urls(run_id), root)
        assert client.post(f"/api/studio/runs/{run_id}/artifact-jobs", json={"job_type": "REPLAY"}).status_code == 409
    finally:
        link.unlink()


def test_def_063_replay_cross_run_native_file_symlink_blocks_all_consumers(portable_storage):
    client, root, run_id, _ = portable_storage
    other = root.parent / "other-run"
    RunService().start(root / "experiment", other, requested_steps=1)
    link = root / "frames" / "step-000001.json.gz"
    substitute_link(link, target=other / "frames" / link.name)
    try:
        assert_rejected(client, replay_urls(run_id), root)
    finally:
        link.unlink()


def test_log_download_rejects_an_attempt_outside_the_run(portable_storage):
    client, root, run_id, _ = portable_storage
    for suffix in ("log", "log/download"):
        response = client.get(f"/api/studio/runs/{run_id}/attempts/{uuid4()}/{suffix}")
        assert response.status_code == 404
        assert "private log marker" not in response.text


def test_artifact_download_rejects_a_changed_content_identity(portable_storage):
    client, root, run_id, _ = portable_storage
    url = artifact_urls(run_id)[1]
    assert client.get(url).content == b"private artifact marker"
    (root / "artifacts" / "report.txt").write_bytes(b"replaced")
    assert client.get(url).status_code == 404


def test_log_preview_reads_a_bounded_utf8_window_and_keeps_append_identity(portable_storage, monkeypatch):
    client, root, run_id, attempt = portable_storage
    path = root / "logs" / "runtime-process.log"
    path.write_bytes(("日志一行\n" * 100_000).encode("utf-8"))
    original = Path.read_bytes

    def guarded_read(value):
        assert value != path, "preview read the complete log"
        return original(value)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    url = f"/api/studio/runs/{run_id}/attempts/{attempt}/log"
    first = client.get(url, params={"limit_bytes": 13})
    assert first.status_code == 200, first.text
    assert first.json()["content"] == "日志一行\n"
    assert first.json()["next_cursor"] == 13
    assert first.json()["eof"] is False
    with path.open("a", encoding="utf-8") as handle:
        handle.write("新增\n")
    second = client.get(url, params={"cursor": 13, "limit_bytes": 13}).json()
    assert second["content"] == first.json()["content"]
    assert second["file_id"] == first.json()["file_id"]
    assert client.get(url, params={"cursor": 1}).status_code == 422
    tiny = client.get(url, params={"limit_bytes": 2})
    assert tiny.status_code == 200
    assert tiny.json()["content"] == "日"
    assert tiny.json()["next_cursor"] == 3
    assert client.get(url, params={"cursor": path.stat().st_size + 1}).status_code == 409


@pytest.mark.parametrize("relative", ["skills/test-brain/SKILL.md", "world/world.json"])
def test_changed_embedded_inputs_cannot_be_replayed(portable_storage, relative):
    client, root, run_id, _ = portable_storage
    # Find the actual copied Skill entrypoint instead of a public resource ID.
    if relative.startswith("skills/"):
        target = next((root / "experiment" / "skills").rglob("SKILL.md"))
    else:
        target = root / "experiment" / relative
    with target.open("a", encoding="utf-8") as handle:
        handle.write("\nchanged after sealing\n")
    assert_rejected(client, replay_urls(run_id), root)
