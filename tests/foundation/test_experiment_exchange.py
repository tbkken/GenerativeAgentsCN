from pathlib import Path
from zipfile import ZipFile

import pytest

from generative_agents.ga_protocol.packages.io import (
    PackageError, atomic_write_json, open_package, read_json, seal_directory, write_integrity_manifest,
)
from generative_agents.ga_runtime.api import RunService
from generative_agents.ga_replay.api import ReplayReader
from generative_agents.ga_studio.api import ExperimentExchangeConflict, ExperimentExchangeService
from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import Base
from tests.test_portable_package_protocol import _experiment


@pytest.fixture
def exchange(tmp_path):
    database = create_database(f"sqlite:///{(tmp_path / 'target.sqlite').as_posix()}")
    Base.metadata.create_all(database.engine)
    yield ExperimentExchangeService(database, package_root=tmp_path / "target/packages")
    database.close()


def _collection(tmp_path, *, completed=True):
    experiment = _experiment(tmp_path / "source")
    run = tmp_path / "source/run"
    runtime = RunService()
    if completed:
        status = runtime.start(experiment, run, requested_steps=1)
        assert status.status.value == "COMPLETED"
    else:
        runtime.create(experiment, run, requested_steps=2)
    run_id = read_json(run / "run.json")["run_id"]
    collection = tmp_path / "collection"
    seal_directory(experiment, collection / "experiment.gaexp")
    runtime.seal(run, collection / "runs" / f"{run_id}.garun")
    write_integrity_manifest(collection)
    return experiment, run, collection, seal_directory(collection, tmp_path / "share.zip")


def test_collection_restores_results_in_fresh_system_and_repeated_import_is_noop(exchange, tmp_path):
    experiment, source_run, collection, archive = _collection(tmp_path)
    result = exchange.import_package(archive)
    experiment_id = read_json(experiment / "manifest.json")["experiment"]["experiment_id"]
    run_id = read_json(source_run / "run.json")["run_id"]
    assert result == {"experiment_id": experiment_id, "run_ids": [run_id], "imported_count": 2, "existing_count": 0}
    run_record = exchange.catalog.get("run", run_id)
    assert Path(run_record.location).read_bytes() == (collection / "runs" / f"{run_id}.garun").read_bytes()
    with ReplayReader(Path(run_record.location)) as replay:
        assert replay.status.committed_step == 1
        assert replay.manifest.run_id == run_id
    rows_before = [(row.package_id, row.location, row.updated_at) for row in exchange.catalog.list()]
    assert exchange.import_package(archive)["existing_count"] == 2
    assert [(row.package_id, row.location, row.updated_at) for row in exchange.catalog.list()] == rows_before


def test_single_run_restores_its_embedded_experiment(exchange, tmp_path):
    experiment, run, collection, _ = _collection(tmp_path, completed=False)
    run_id = read_json(run / "run.json")["run_id"]
    result = exchange.import_package(collection / "runs" / f"{run_id}.garun")
    assert result["imported_count"] == 2
    assert exchange.catalog.get("experiment", result["experiment_id"]).package_status == "SEALED"


def test_imported_paused_run_resumes_same_identity_from_next_step(exchange, tmp_path, monkeypatch):
    from generative_agents.ga_runtime.lifecycle.control import FileRunControl
    from generative_agents.ga_runtime.lifecycle.executor import _StatusCommitter
    experiment = _experiment(tmp_path / "source")
    run = tmp_path / "source/run"
    original_commit = _StatusCommitter.commit
    def commit_and_pause(self, result, *, force_checkpoint):
        outcome = original_commit(self, result, force_checkpoint=force_checkpoint)
        if result.step_no == 1:
            FileRunControl(run).request_pause()
        return outcome
    monkeypatch.setattr(_StatusCommitter, "commit", commit_and_pause)
    paused = RunService().start(experiment, run, requested_steps=2)
    assert paused.status.value == "PAUSED" and paused.committed_step == 1
    first_frame = (run / "frames/step-000001.json.gz").read_bytes()
    archive = RunService().seal(run, tmp_path / "paused.garun")
    imported = exchange.import_package(archive)
    imported_archive = Path(exchange.catalog.get("run", imported["run_ids"][0]).location)
    continued = tmp_path / "continued"
    monkeypatch.setattr(_StatusCommitter, "commit", original_commit)
    completed = RunService().resume(imported_archive, extracted_destination=continued)
    assert completed.status.value == "COMPLETED" and completed.committed_step == 2
    assert completed.run_id == paused.run_id
    assert completed.active_attempt_id != paused.active_attempt_id
    assert (continued / "frames/step-000001.json.gz").read_bytes() == first_frame
    assert len(list((continued / "attempts").iterdir())) == 2


def test_export_draft_keeps_source_editable_and_original_bytes(exchange, tmp_path):
    experiment = _experiment(tmp_path / "source")
    record = exchange.catalog.upsert(experiment)
    before = {p.relative_to(experiment): p.read_bytes() for p in experiment.rglob("*") if p.is_file()}
    exported = exchange.export_experiment(record.package_id, tmp_path / "export.gaexp")
    assert experiment.is_dir()
    assert exchange.catalog.get("experiment", record.package_id).package_status == "DRAFT"
    assert {p.relative_to(experiment): p.read_bytes() for p in experiment.rglob("*") if p.is_file()} == before
    assert exchange.import_package(exported)["existing_count"] == 1
    assert Path(exchange.catalog.get("experiment", record.package_id).location) == experiment


def test_changed_existing_identity_aborts_entire_collection(exchange, tmp_path):
    experiment, _, _, archive = _collection(tmp_path, completed=False)
    manifest = read_json(experiment / "manifest.json")
    manifest["experiment"]["name"] = "Changed local experiment"
    atomic_write_json(experiment / "manifest.json", manifest)
    write_integrity_manifest(experiment)
    exchange.catalog.upsert(experiment)
    with pytest.raises(ExperimentExchangeConflict, match="未覆盖"):
        exchange.import_package(archive)
    assert len(exchange.catalog.list()) == 1
    assert not (exchange.package_root / "runs").exists()


def test_disk_identity_is_recovered_without_duplicate_archive(exchange, tmp_path):
    experiment = _experiment(exchange.package_root / "renamed")
    archive = seal_directory(experiment, tmp_path / "export.gaexp")
    assert exchange.import_package(archive)["existing_count"] == 1
    record = exchange.catalog.list()[0]
    assert Path(record.location) == experiment
    assert not list(exchange.package_root.rglob("*.gaexp"))


def test_unchanged_mutable_run_skips_without_redirecting_and_changed_evidence_conflicts(exchange, tmp_path):
    experiment, run, _, archive = _collection(tmp_path)
    experiment_record = exchange.catalog.upsert(experiment)
    run_record = exchange.catalog.upsert(run)
    # A host-only preview and lock must not create a false content conflict.
    (run / "runtime-preview.json").write_text("temporary preview")
    (run / "artifact.lock").touch()
    result = exchange.import_package(archive)
    assert result["imported_count"] == 0 and result["existing_count"] == 2
    assert exchange.catalog.get("run", run_record.package_id).location == str(run)
    assert exchange.catalog.get("experiment", experiment_record.package_id).location == str(experiment)
    artifact = run / "artifacts" / "additional-evidence.txt"
    artifact.write_text("new local evidence")
    with pytest.raises(ExperimentExchangeConflict, match="未覆盖"):
        exchange.import_package(archive)
    assert artifact.read_text() == "new local evidence"


def test_import_rolls_back_files_and_catalog_on_publish_error(exchange, tmp_path, monkeypatch):
    _, _, _, archive = _collection(tmp_path, completed=False)
    original = exchange._copy_archive
    count = 0
    def failing_copy(source, target):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("disk is full")
        return original(source, target)
    monkeypatch.setattr(exchange, "_copy_archive", failing_copy)
    with pytest.raises(OSError, match="disk is full"):
        exchange.import_package(archive)
    assert exchange.catalog.list() == []
    assert not list(exchange.package_root.rglob("*.gaexp"))
    assert not list(exchange.package_root.rglob("*.garun"))


def test_catalog_failure_rolls_back_all_imported_archives(exchange, tmp_path):
    from sqlalchemy import event
    _, _, _, archive = _collection(tmp_path, completed=False)
    def reject_flush(*args):
        raise RuntimeError("catalog unavailable")
    event.listen(exchange.database.session_factory, "before_flush", reject_flush)
    try:
        with pytest.raises(RuntimeError, match="catalog unavailable"):
            exchange.import_package(archive)
    finally:
        event.remove(exchange.database.session_factory, "before_flush", reject_flush)
    assert exchange.catalog.list() == []
    assert not list(exchange.package_root.rglob("*.gaexp"))
    assert not list(exchange.package_root.rglob("*.garun"))


@pytest.mark.parametrize("invalid", ["active", "wrong_experiment", "corrupt_frame", "extra_member"])
def test_invalid_collection_is_rejected_before_publishing(exchange, tmp_path, invalid):
    experiment, run, collection, _ = _collection(tmp_path, completed=invalid == "corrupt_frame")
    if invalid == "wrong_experiment":
        manifest = read_json(experiment / "manifest.json")
        manifest["experiment"]["name"] = "Different content"
        atomic_write_json(experiment / "manifest.json", manifest)
        write_integrity_manifest(experiment)
        (collection / "experiment.gaexp").unlink()
        seal_directory(experiment, collection / "experiment.gaexp")
    elif invalid == "extra_member":
        (collection / "unknown.txt").write_text("unrecognized envelope payload")
    else:
        run_archive = next((collection / "runs").glob("*.garun"))
        with open_package(run_archive) as unpacked:
            if invalid == "active":
                status = read_json(unpacked / "status.json")
                status["status"] = "RUNNING"
                atomic_write_json(unpacked / "status.json", status)
            else:
                next((unpacked / "frames").glob("*.gz")).write_bytes(b"broken frame")
            write_integrity_manifest(unpacked)
            run_archive.unlink()
            seal_directory(unpacked, run_archive)
    write_integrity_manifest(collection)
    archive = seal_directory(collection, tmp_path / "invalid.zip")
    with pytest.raises((PackageError, ValueError)):
        exchange.import_package(archive)
    assert exchange.catalog.list() == []
    assert not exchange.package_root.exists()


def test_nested_budget_is_enforced(exchange, tmp_path, monkeypatch):
    _, _, collection, archive = _collection(tmp_path, completed=False)
    from generative_agents.ga_studio.experiments.exchange import _ExtractionBudget
    with ZipFile(archive) as package:
        outer_files = len(package.infolist())
    monkeypatch.setattr("generative_agents.ga_studio.experiments.exchange._ExtractionBudget", lambda: _ExtractionBudget(files=outer_files))
    with pytest.raises(PackageError, match="too many files"):
        exchange.import_package(archive)
    assert exchange.catalog.list() == []


def test_removing_run_from_collection_breaks_envelope_integrity(exchange, tmp_path):
    _, _, collection, _ = _collection(tmp_path, completed=False)
    next((collection / "runs").glob("*.garun")).unlink()
    archive = seal_directory(collection, tmp_path / "missing-run.zip")
    with pytest.raises(PackageError, match="integrity mismatch"):
        exchange.import_package(archive)
    assert exchange.catalog.list() == []
