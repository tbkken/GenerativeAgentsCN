from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from generative_agents.persistence.models import (
    RunArtifact,
    RunResultSummary,
    RunStep,
)
from generative_agents.runtime.artifact_builder import ArtifactBuilder
from generative_agents.runtime.artifact_scheduler import ArtifactSchedulerRepository
from generative_agents.services.artifacts import ArtifactService
from generative_agents.services.errors import ServiceError
from tests.architecture.test_run_observability_lifecycle_redlines import (
    _claimed_run,
    _project_replay_step,
    _publish_run,
    _rich_step,
    web_runtime,
)


JUNCTION_POSITIONS = ("parent", "intermediate", "leaf", "cross_run")


def _make_junction(link: Path, target: Path) -> None:
    """Create the strongest unprivileged Windows reparse primitive available."""

    if not hasattr(Path, "is_junction"):
        pytest.skip("this Python runtime cannot identify Windows junctions")
    command = (
        "New-Item -ItemType Junction "
        f"-Path '{str(link).replace("'", "''")}' "
        f"-Target '{str(target).replace("'", "''")}' | Out-Null"
    )
    created = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert created.returncode == 0, created.stderr or created.stdout
    assert link.is_junction() and not link.is_symlink()


def _remove_only_junction(link: Path, target_file: Path, expected: bytes) -> None:
    """Prove cleanup removes the link itself and never traverses into its target."""

    assert link.is_junction()
    link.rmdir()
    assert not link.exists()
    assert target_file.is_file()
    assert target_file.read_bytes() == expected


def _artifact_junction_path(
    database,
    var_dir: Path,
    run: dict,
    position: str,
    content: bytes,
) -> tuple[Path, Path, Path]:
    run_root = var_dir / "runs" / run["run_id"]
    artifact_root = run_root / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    logical_file = artifact_root / "result.json"

    if position == "parent":
        logical_file.write_bytes(content)
        physical_run_root = run_root.with_name(f"{run_root.name}-physical")
        run_root.rename(physical_run_root)
        target_file = physical_run_root / "artifacts" / logical_file.name
        _make_junction(run_root, physical_run_root)
        return logical_file, run_root, target_file

    if position == "intermediate":
        target_root = artifact_root / "physical-intermediate"
        target_root.mkdir()
        target_file = target_root / logical_file.name
        target_file.write_bytes(content)
        link = artifact_root / "junction-intermediate"
        _make_junction(link, target_root)
        return link / logical_file.name, link, target_file

    if position == "leaf":
        target_root = artifact_root / "physical-leaf"
        target_root.mkdir()
        target_file = target_root / logical_file.name
        target_file.write_bytes(content)
        link = artifact_root / "nested" / "junction-leaf"
        link.parent.mkdir()
        _make_junction(link, target_root)
        return link / logical_file.name, link, target_file

    assert position == "cross_run"
    _other_experiment, _other_revision, other_run = _publish_run(
        database, var_dir, f"artifact-junction-target-{uuid4().hex}"
    )
    target_root = var_dir / "runs" / other_run["run_id"] / "artifacts"
    target_root.mkdir(parents=True, exist_ok=True)
    target_file = target_root / logical_file.name
    target_file.write_bytes(content)
    link = artifact_root / "other-run"
    _make_junction(link, target_root)
    return link / logical_file.name, link, target_file


@pytest.mark.parametrize("junction_position", JUNCTION_POSITIONS)
def test_def_072_artifact_preview_and_download_reject_windows_junction_chain(
    web_runtime, junction_position: str
):
    """DEF-072: parent/middle/leaf/cross-Run reparse paths are not ownership."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run = _publish_run(
        database, var_dir, f"artifact-junction-{junction_position.replace('_', '-')}"
    )
    content = b'{"result":"immutable"}'
    logical_file, link, target_file = _artifact_junction_path(
        database, var_dir, run, junction_position, content
    )

    with database.session_factory.begin() as session:
        artifact = RunArtifact(
            id=str(uuid4()),
            run_id=run["run_id"],
            artifact_type="REPORT",
            logical_name="result.json",
            media_type="application/json",
            relative_path=logical_file.relative_to(var_dir).as_posix(),
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            source_kind="DERIVED",
            generator_version="junction-integrity-test",
            source_step=1,
            partial=True,
            state="READY",
        )
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id

    responses = []
    try:
        for suffix in ("preview", "download"):
            responses.append(
                client.get(
                    f"/api/v1/runs/{run['run_id']}/artifacts/{artifact_id}/{suffix}"
                )
            )
    finally:
        _remove_only_junction(link, target_file, content)

    for response in responses:
        assert response.status_code in {409, 500}, response.text
        assert response.json()["error"]["code"] in {
            "ARTIFACT_CONTENT_INTEGRITY_ERROR",
            "RUN_STORAGE_INTEGRITY_ERROR",
        }
        assert str(var_dir) not in response.text
        assert str(target_file) not in response.text


def _replay_junction_path(
    database,
    var_dir: Path,
    run: dict,
    stored_path: Path,
    position: str,
    content: bytes,
) -> tuple[Path, Path, Path]:
    run_root = var_dir / "runs" / run["run_id"]
    frames_root = stored_path.parent

    if position == "parent":
        physical_run_root = run_root.with_name(f"{run_root.name}-physical")
        relative_frame = stored_path.relative_to(run_root)
        run_root.rename(physical_run_root)
        target_file = physical_run_root / relative_frame
        _make_junction(run_root, physical_run_root)
        return stored_path, run_root, target_file

    if position == "intermediate":
        link = frames_root / "junction-intermediate"
        _make_junction(link, frames_root)
        return link / stored_path.name, link, stored_path

    if position == "leaf":
        target_root = frames_root / "physical-leaf"
        target_root.mkdir()
        target_file = target_root / stored_path.name
        stored_path.replace(target_file)
        link = frames_root / "nested" / "junction-leaf"
        link.parent.mkdir()
        _make_junction(link, target_root)
        return link / stored_path.name, link, target_file

    assert position == "cross_run"
    _other_experiment, _other_revision, other_run = _publish_run(
        database, var_dir, f"replay-junction-target-{uuid4().hex}"
    )
    target_root = var_dir / "runs" / other_run["run_id"] / "frames"
    target_root.mkdir(parents=True, exist_ok=True)
    target_file = target_root / stored_path.name
    target_file.write_bytes(content)
    link = frames_root / "other-run"
    _make_junction(link, target_root)
    return link / stored_path.name, link, target_file


@pytest.mark.parametrize("junction_position", JUNCTION_POSITIONS)
def test_def_072_replay_consumers_reject_windows_junction_chain(
    web_runtime, junction_position: str
):
    """DEF-072: manifest/window/builder all share the reparse ownership boundary."""

    client, database, var_dir, _app = web_runtime
    _experiment, _revision, run, claimed = _claimed_run(
        database, var_dir, f"replay-junction-{junction_position.replace('_', '-')}"
    )
    result = _rich_step(run["run_id"], claimed.attempt_id, 1)
    stored = _project_replay_step(database, var_dir, run["run_id"], result)
    frame_content = stored.path.read_bytes()
    logical_frame, link, target_file = _replay_junction_path(
        database,
        var_dir,
        run,
        stored.path,
        junction_position,
        frame_content,
    )
    with database.session_factory.begin() as session:
        session.get(RunStep, (run["run_id"], 1)).frame_path = (
            logical_frame.relative_to(var_dir).as_posix()
        )
        session.add(
            RunResultSummary(
                run_id=run["run_id"],
                available_step=1,
                result_state="PARTIAL",
                projection_version="junction-test",
                result_version=1,
            )
        )

    manifest = window = None
    build_error = None
    ready: list[RunArtifact] = []
    try:
        manifest = client.get(f"/api/v1/runs/{run['run_id']}/replay/manifest")
        window = client.get(
            f"/api/v1/runs/{run['run_id']}/replay/steps",
            params={"from_step": 1, "limit": 1},
        )
        job = ArtifactService(database, var_dir=var_dir).create_job(
            run["run_id"], job_type="BUILD_REPLAY"
        )
        owned = ArtifactSchedulerRepository(database).claim_next()
        assert owned is not None and owned.job_id == job["job_id"]
        try:
            ArtifactBuilder(database, var_dir=var_dir).build(job["job_id"])
        except Exception as exc:
            build_error = exc
        with database.session_factory() as session:
            ready = list(
                session.scalars(
                    select(RunArtifact).where(
                        RunArtifact.run_id == run["run_id"],
                        RunArtifact.artifact_type == "REPLAY",
                        RunArtifact.state == "READY",
                    )
                )
            )
    finally:
        _remove_only_junction(link, target_file, frame_content)

    assert manifest is not None and window is not None
    evidence = {
        "manifest": (manifest.status_code, manifest.text[:160]),
        "window": (window.status_code, window.text[:160]),
        "build_error": repr(build_error),
        "ready_artifacts": len(ready),
    }
    assert manifest.status_code in {409, 500}, evidence
    assert window.status_code in {409, 500}, evidence
    assert isinstance(build_error, ServiceError), evidence
    assert build_error.code in {
        "RUN_STORAGE_INTEGRITY_ERROR",
        "REPLAY_FRAME_INTEGRITY_ERROR",
        "REPLAY_FRAME_OWNERSHIP_INVALID",
        "REPLAY_FRAME_INVALID",
    }, evidence
    assert not ready, evidence
    for response in (manifest, window):
        assert str(var_dir) not in response.text
        assert str(target_file) not in response.text
