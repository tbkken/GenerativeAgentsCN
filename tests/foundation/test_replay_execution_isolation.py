"""Recorded Run facts do not depend on today's executable Skill schema."""

import gzip
import hashlib
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from generative_agents.ga_protocol import (
    PackageError,
    atomic_write_json,
    read_json,
    seal_directory,
    validate_run_directory,
    write_integrity_manifest,
)
from generative_agents.ga_replay import ReplayReader
from generative_agents.ga_runtime.service import RunService
from generative_agents.ga_studio.web import create_studio_app
from tests.test_portable_package_protocol import _experiment


def _file_hashes(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


@pytest.fixture
def recorded_run(tmp_path):
    var = tmp_path / "var"
    root = var / "packages/runs/recorded"
    RunService().start(_experiment(var / "author"), root, requested_steps=2)
    # Reproduce the sealed registry at the time these facts were committed.
    # This is fixture construction only; Replay must never rewrite these bytes.
    registry_path = root / "experiment/skills/registry.json"
    registry = read_json(registry_path)
    registry["passive_roots"] = registry.pop("object_roots")
    atomic_write_json(registry_path, registry)
    asset = root / "experiment/assets/replay.svg"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    integrity = write_integrity_manifest(root / "experiment")
    manifest = read_json(root / "run.json")
    manifest["experiment"]["root_sha256"] = integrity["root_sha256"]
    atomic_write_json(root / "run.json", manifest)
    return root


@pytest.mark.parametrize("archive", [False, True])
def test_recorded_facts_replay_without_execution_validation_or_package_changes(recorded_run, tmp_path, monkeypatch, archive):
    from generative_agents.ga_protocol import validation

    root = recorded_run
    expected = json.loads(gzip.decompress((root / "frames/step-000002.json.gz").read_bytes()))["result"]
    run_id = read_json(root / "run.json")["run_id"]
    with pytest.raises(PackageError, match="passive_roots"):
        validate_run_directory(root)

    package = root
    if archive:
        write_integrity_manifest(root)
        package = seal_directory(root, tmp_path / "renamed.garun")
    before = _file_hashes(root)
    archive_before = package.read_bytes() if archive else None

    def execution_validation_is_forbidden(*args, **kwargs):
        pytest.fail("Replay must not load the current execution Skill/world contracts")

    monkeypatch.setattr(validation, "validate_experiment_directory", execution_validation_is_forbidden)
    with ReplayReader(package) as replay:
        assert replay.manifest.run_id == run_id
        assert replay.available_steps() == (1, 2)
        assert replay.read_step(2) == expected
        assert replay.state_at(2)["step_no"] == 2
        assert replay.experiment_world()["definition"]["size"] == [1, 1]
        assert replay.semantic_index()["levels"] == ["WORLD", "SECTOR", "ARENA", "GAME_OBJECT"]
        assert replay.asset("assets/replay.svg")[0].startswith(b"<svg")
    assert _file_hashes(root) == before
    if archive:
        assert package.read_bytes() == archive_before


def test_web_replay_and_run_catalog_use_recorded_facts(recorded_run, tmp_path):
    root = recorded_run
    run_id = read_json(root / "run.json")["run_id"]
    before = _file_hashes(root)
    experiment_id = read_json(root / "run.json")["experiment"]["experiment_id"]
    experiment_archive = seal_directory(root / "experiment", tmp_path / "var/packages/experiments/recorded.gaexp")
    archive_before = experiment_archive.read_bytes()
    app = create_studio_app(database_url="sqlite:///" + (tmp_path / "studio.db").as_posix(), var_dir=tmp_path / "var")
    with TestClient(app) as client:
        response = client.post("/api/studio/packages/rebuild")
        assert response.status_code == 200, response.text
        response = client.get(f"/api/studio/runs/{run_id}/replay/manifest")
        assert response.status_code == 200, response.text
        assert response.json()["available_step"] == 2
        response = client.get(f"/api/studio/runs/{run_id}/replay/steps?from_step=1&limit=100")
        assert response.status_code == 200, response.text
        assert [step["step_no"] for step in response.json()["steps"]] == [1, 2]
        response = client.get(f"/api/studio/runs/{run_id}/replay/assets/replay.svg")
        assert response.status_code == 200, response.text
        assert response.content.startswith(b"<svg")
        prefix = f"/api/studio/experiments/{experiment_id}/resources"
        response = client.get(f"{prefix}/skills?kind=atomic")
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        response = client.get(f"{prefix}/skills/test-brain")
        assert response.status_code == 200, response.text
        assert "Return WAIT." in response.json()["markdown"]
        assert response.json()["editable"] is False
        response = client.get(f"{prefix}/map-editor/ville-document")
        assert response.status_code == 200, response.text
        response = client.get(f"{prefix}/maps/{experiment_id}")
        assert response.status_code == 200, response.text
        assert response.json()["validation"] is None
        # Explicit validation and trial remain execution operations.
        for endpoint in (f"maps/{experiment_id}/validate", "skills/test-brain/run"):
            response = client.post(f"{prefix}/{endpoint}", json={"input_text": "test"})
            assert response.status_code == 422, response.text
            assert "passive_roots" in response.text
    assert _file_hashes(root) == before
    assert experiment_archive.read_bytes() == archive_before


def test_start_resume_and_rerun_still_reject_non_executable_skill_registry(recorded_run, tmp_path):
    root = recorded_run
    before = _file_hashes(root)
    service = RunService()
    for operation in (
        lambda: service.create(root / "experiment", tmp_path / "created"),
        lambda: service.resume(root),
        lambda: service.create_rerun(root, tmp_path / "rerun"),
    ):
        with pytest.raises(PackageError, match="passive_roots"):
            operation()
    assert _file_hashes(root) == before
    assert not (tmp_path / "created").exists()
    assert not (tmp_path / "rerun").exists()


@pytest.mark.parametrize("damage", ["skill_bytes", "missing_skill", "embedded_hash", "embedded_id", "frame_hash", "frame_id", "frame_gap"])
def test_replay_still_rejects_corruption_and_mismatched_identity(recorded_run, damage):
    root = recorded_run
    registry = read_json(root / "experiment/skills/registry.json")
    skill = root / "experiment" / registry["skills"][0]["path"]
    # Warm the directory cache so immutable changes must invalidate it.
    with ReplayReader(root):
        pass
    if damage == "skill_bytes":
        skill.write_bytes(skill.read_bytes() + b"\nchanged")
    elif damage == "missing_skill":
        skill.unlink()
    elif damage == "embedded_hash":
        skill.write_bytes(skill.read_bytes() + b"\nchanged")
        write_integrity_manifest(root / "experiment")
    elif damage == "embedded_id":
        manifest = read_json(root / "run.json")
        manifest["experiment"]["experiment_id"] = str(uuid4())
        atomic_write_json(root / "run.json", manifest)
    elif damage == "frame_hash":
        frame = root / "frames/step-000001.json.gz"
        frame.write_bytes(frame.read_bytes() + b"changed")
    elif damage == "frame_id":
        frame = root / "frames/step-000001.json.gz"
        document = json.loads(gzip.decompress(frame.read_bytes()))
        document["result"]["run_id"] = str(uuid4())
        frame.write_bytes(gzip.compress(json.dumps(document).encode(), mtime=0))
        (root / "projection.json").unlink()
    elif damage == "frame_gap":
        (root / "frames/step-000001.json.gz").unlink()
    with pytest.raises(PackageError):
        with ReplayReader(root) as replay:
            replay.available_steps()
            replay.read_step(1)


@pytest.mark.parametrize("entrypoint", ["../outside.json", "missing.json"])
def test_read_only_validation_still_checks_safe_existing_entrypoints(recorded_run, entrypoint):
    root = recorded_run
    experiment = read_json(root / "experiment/manifest.json")
    experiment["entrypoints"]["skills"] = entrypoint
    atomic_write_json(root / "experiment/manifest.json", experiment)
    integrity = write_integrity_manifest(root / "experiment")
    manifest = read_json(root / "run.json")
    manifest["experiment"]["root_sha256"] = integrity["root_sha256"]
    atomic_write_json(root / "run.json", manifest)
    with pytest.raises(PackageError):
        with ReplayReader(root):
            pass


def test_sealed_replay_requires_outer_archive_integrity(recorded_run, tmp_path):
    root = recorded_run
    write_integrity_manifest(root)
    frame = root / "frames/step-000001.json.gz"
    frame.write_bytes(frame.read_bytes() + b"changed")
    archive = seal_directory(root, tmp_path / "damaged.garun")
    with pytest.raises(PackageError, match="integrity mismatch"):
        with ReplayReader(archive):
            pass
