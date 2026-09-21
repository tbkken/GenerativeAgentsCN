from __future__ import annotations

import gzip
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from generative_agents.ga_protocol.schemas.manifests import RunState
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.validation import validate_run_directory
from generative_agents.ga_protocol.packages.artifacts import provenance_path
from generative_agents.ga_protocol.packages.artifacts import read_artifact_provenance
from generative_agents.ga_protocol.packages.artifacts import record_artifact_provenance
from generative_agents.ga_runtime.lifecycle.service import RunService
from generative_agents.adapters.web.app import create_studio_app
from test_portable_package_protocol import _experiment


def _fixture(tmp_path: Path):
    var_dir = tmp_path / "var"
    package_root = var_dir / "packages"
    experiment = _experiment(package_root)
    root = RunService().create(experiment, package_root / "runs" / "artifacts", requested_steps=3)
    run_id = validate_run_directory(root).run_id
    (root / "frames").mkdir()
    for step in (1, 2, 3):
        result = {
            "run_id": run_id, "attempt_id": "b1d1b2dc-a5f3-4558-95cb-cb0f40f4fcf8",
            "step_no": step, "virtual_time": "2026-09-03T08:00:00+08:00",
            "agents": [], "conversations": [], "memory_deltas": [], "schedule_revisions": [],
            "domain_events": [], "committed_model_usage": [], "effects": [],
        }
        (root / "frames" / f"step-{step:06d}.json.gz").write_bytes(
            gzip.compress(json.dumps({"schema_version": 1, "result": result}).encode(), mtime=0)
        )
    status = RunStatus.model_validate(read_json(root / "status.json"))
    status.status = RunState.PAUSED
    status.committed_step = 2
    status.active_attempt_id = "b1d1b2dc-a5f3-4558-95cb-cb0f40f4fcf8"
    atomic_write_json(root / "status.json", status.model_dump(mode="json"))
    atomic_write_json(root / "attempts" / status.active_attempt_id / "attempt.json", {
        "run_id": run_id, "attempt_id": status.active_attempt_id, "ordinal": 1,
        "resumed_from_step": 0, "status": "PAUSED", "started_at": "2026-09-09T00:00:00Z",
        "finished_at": "2026-09-09T00:01:00Z", "failure": None,
    })
    app = create_studio_app(database_url=f"sqlite:///{(tmp_path / 'studio.sqlite').as_posix()}", var_dir=var_dir)
    return root, run_id, status, app


def _artifacts(client, run_id):
    response = client.get(f"/api/studio/runs/{run_id}/results/operations")
    assert response.status_code == 200, response.text
    return {item["logical_name"]: item for item in response.json()["artifacts"]}


def test_partial_export_keeps_its_origin_and_bytes_when_run_advances_during_export(tmp_path, monkeypatch):
    import generative_agents.adapters.web.routes.artifacts as portable_api

    root, run_id, status, app = _fixture(tmp_path)
    historical = root / "artifacts" / "result-bundle-step-000001-old.zip"
    historical.parent.mkdir()
    historical.write_bytes(b"historical artifact; never infer provenance from this filename")
    old_bytes = historical.read_bytes()
    original_quality = portable_api.read_run_quality

    def advance_after_snapshot(*args, **kwargs):
        quality = original_quality(*args, **kwargs)
        for relative in ("storage/agent/associate/memory.json", "runtime-storage/skill-memory/memories.json"):
            atomic_write_json(root / "attempts" / status.active_attempt_id / relative, {"memory": "uncommitted-next-step-memory"})
        log = root / "logs" / "runtime-process.log"
        log.parent.mkdir(exist_ok=True)
        log.write_text("WARNING next-Step uncommitted diagnostic\n", encoding="utf-8")
        status.status = RunState.COMPLETED
        status.committed_step = 3
        status.updated_at = datetime.now(UTC)
        atomic_write_json(root / "status.json", status.model_dump(mode="json"))
        return quality

    with TestClient(app) as client:
        assert client.post("/api/studio/packages/rebuild").status_code == 200
        before = _artifacts(client, run_id)[historical.name]
        assert before["provenance_status"] == "UNKNOWN"
        assert before["source_step"] is before["partial"] is before["generated_at"] is None
        # Advance only after the export has captured its source status.
        monkeypatch.setattr(portable_api, "read_run_quality", advance_after_snapshot)
        response = client.post(f"/api/studio/runs/{run_id}/artifact-jobs", json={"job_type": "RESULT_BUNDLE"})
        assert response.status_code == 201, response.text
        monkeypatch.setattr(portable_api, "read_run_quality", original_quality)
        first_name = response.json()["artifact_name"]
        first = _artifacts(client, run_id)[first_name]
        first_bytes = (root / "artifacts" / first_name).read_bytes()
        assert first["source_step"] == 2
        assert first["source_total_steps"] == 3
        assert first["source_status"] == "PAUSED"
        assert first["partial"] is True
        assert first["sha256"] == hashlib.sha256(first_bytes).hexdigest()
        assert datetime.fromisoformat(first["generated_at"]).utcoffset() is not None
        with zipfile.ZipFile(io.BytesIO(first_bytes)) as archive:
            assert archive.testzip() is None
            assert json.loads(archive.read("status.json"))["committed_step"] == 2
            assert json.loads(archive.read("status.json"))["status"] == "PAUSED"
            assert "frames/step-000003.json.gz" not in archive.namelist()
            assert len([name for name in archive.namelist() if name.startswith("frames/")]) == 2
            assert not any(name.startswith("attempts/") and ("/storage/" in name or "/runtime-storage/" in name) for name in archive.namelist())
            assert not any(b"uncommitted-next-step-memory" in archive.read(name) for name in archive.namelist())
            assert b"uncommitted diagnostic" in archive.read("logs/runtime-process.log")
            metadata_name = next(name for name in archive.namelist() if name.startswith("artifact-metadata/"))
            metadata = json.loads(archive.read(metadata_name))
            assert metadata["source_step"] == 2
            assert metadata["partial"] is True
            assert metadata["sha256"] == hashlib.sha256(archive.read("artifacts/" + metadata["logical_name"])).hexdigest()

        response = client.post(f"/api/studio/runs/{run_id}/artifact-jobs", json={"job_type": "RESULT_BUNDLE"})
        assert response.status_code == 201, response.text
        final_name = response.json()["artifact_name"]
        items = _artifacts(client, run_id)
        assert final_name != first_name
        assert items[first_name] == first
        assert (root / "artifacts" / first_name).read_bytes() == first_bytes
        assert items[final_name]["source_step"] == 3
        assert items[final_name]["source_status"] == "COMPLETED"
        assert items[final_name]["partial"] is False
        assert items[historical.name] == before
        assert historical.read_bytes() == old_bytes
        assert not provenance_path(root, historical.name, before["sha256"]).exists()
        downloaded = client.get(f"/api/studio/runs/{run_id}/artifacts/{first['artifact_id']}/download")
        assert downloaded.content == first_bytes


def test_same_step_filtered_exports_never_replace_each_other(tmp_path):
    root, run_id, status, app = _fixture(tmp_path)
    with TestClient(app) as client:
        client.post("/api/studio/packages/rebuild")
        names = []
        for job_type in ("FILTERED_MEMORIES", "FILTERED_CONVERSATIONS"):
            for query in ("first", "second"):
                response = client.post(f"/api/studio/runs/{run_id}/artifact-jobs", json={
                    "job_type": job_type, "parameters": {"q": query},
                })
                assert response.status_code == 201, response.text
                names.append(response.json()["artifact_name"])
        assert len(set(names)) == 4
        originals = {name: (root / "artifacts" / name).read_bytes() for name in names}
        records = _artifacts(client, run_id)
        status.status = RunState.COMPLETED
        status.committed_step = 3
        atomic_write_json(root / "status.json", status.model_dump(mode="json"))
        latest = client.post(f"/api/studio/runs/{run_id}/artifact-jobs", json={"job_type": "FILTERED_MEMORIES"})
        items = _artifacts(client, run_id)
        for name in names:
            assert items[name] == records[name]
            assert items[name]["source_step"] == 2
            assert items[name]["partial"] is True
            assert (root / "artifacts" / name).read_bytes() == originals[name]
        # Empty memory exports have the same bytes, but distinct source records.
        latest_item = items[latest.json()["artifact_name"]]
        assert latest_item["sha256"] == items[names[0]]["sha256"]
        assert latest_item["source_step"] == 3
        assert latest_item["partial"] is False


def test_content_or_identity_mismatch_invalidates_provenance_without_repair(tmp_path):
    root, run_id, status, _app = _fixture(tmp_path)
    path = root / "artifacts" / "test.txt"
    path.parent.mkdir()
    path.write_bytes(b"original")
    metadata = record_artifact_provenance(root, path, status)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    args = dict(run_id=run_id, logical_name=path.name, digest=digest, size_bytes=path.stat().st_size)
    assert read_artifact_provenance(root, **args)["provenance_status"] == "RECORDED"
    original_metadata = metadata.read_bytes()
    wrong_run = {**args, "run_id": "different-run"}
    assert read_artifact_provenance(root, **wrong_run)["provenance_status"] == "UNKNOWN"
    assert metadata.read_bytes() == original_metadata
    path.write_bytes(b"changed")
    changed = read_artifact_provenance(root, **{**args, "digest": hashlib.sha256(path.read_bytes()).hexdigest()})
    assert changed["provenance_status"] == "UNKNOWN"
    assert changed["source_step"] is changed["partial"] is None
    assert metadata.read_bytes() == original_metadata


def test_historical_checkpoint_export_uses_selected_boundary_and_remains_partial(tmp_path):
    var_dir = tmp_path / "var"
    root = var_dir / "packages" / "runs" / "checkpoint"
    RunService().start(_experiment(var_dir / "packages"), root, requested_steps=3)
    run_id = validate_run_directory(root).run_id
    app = create_studio_app(database_url=f"sqlite:///{(tmp_path / 'studio.sqlite').as_posix()}", var_dir=var_dir)
    with TestClient(app) as client:
        client.post("/api/studio/packages/rebuild")
        response = client.post(f"/api/studio/runs/{run_id}/checkpoints/2/artifact-job")
        assert response.status_code == 201, response.text
        item = _artifacts(client, run_id)[response.json()["artifact_name"]]
        assert item["source_step"] == 2
        assert item["source_status"] == "COMPLETED"
        assert item["partial"] is True
        future = client.post(f"/api/studio/runs/{run_id}/checkpoints/4/artifact-job")
        assert future.status_code == 422


def test_checkpoint_pruned_while_waiting_for_lock_does_not_export_empty_zip(tmp_path, monkeypatch):
    import generative_agents.ga_runtime.storage.exports as portable_api

    var_dir = tmp_path / "var"
    root = var_dir / "packages" / "runs" / "checkpoint"
    RunService().start(_experiment(var_dir / "packages"), root, requested_steps=3)
    run_id = validate_run_directory(root).run_id
    checkpoint = root / "checkpoints" / "step-000002"
    app = create_studio_app(database_url=f"sqlite:///{(tmp_path / 'studio.sqlite').as_posix()}", var_dir=var_dir)
    original_lock = portable_api.FileLock

    class PruneBeforeAcquiringLock:
        def __init__(self, path, **kwargs):
            self.inner = original_lock(path, **kwargs)

        def __enter__(self):
            checkpoint.rename(root / "pruned-checkpoint")
            return self.inner.__enter__()

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

    with TestClient(app) as client:
        client.post("/api/studio/packages/rebuild")
        monkeypatch.setattr(portable_api, "FileLock", PruneBeforeAcquiringLock)
        response = client.post(f"/api/studio/runs/{run_id}/checkpoints/2/artifact-job")
        assert response.status_code == 404, response.text
        assert not list((root / "artifacts").glob("checkpoint-step-*.zip"))
