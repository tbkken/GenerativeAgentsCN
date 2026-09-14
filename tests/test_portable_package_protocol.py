from __future__ import annotations

import gzip
import hashlib
import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from generative_agents.ga_protocol import (
    RunState,
    RunStatus,
    atomic_write_json,
    seal_directory,
    validate_experiment_directory,
    validate_run_directory,
    write_integrity_manifest,
)
from generative_agents.ga_replay import ReplayReader
from generative_agents.ga_runtime.memory import FileMemoryStream
from generative_agents.ga_runtime.service import RunService
from generative_agents.ga_studio import ExperimentPackageBuilder, SkillSource
from generative_agents.ga_studio.catalog import StudioPackageCatalogService
from generative_agents.ga_studio.resources import StudioAgentDefinition, StudioResourceService
from generative_agents.ga_studio.workspace import ExperimentSelection, ExperimentWorkspaceService
from generative_agents.ga_studio.web import create_studio_app
from generative_agents.persistence import create_database
from generative_agents.persistence.models import Base, StudioAgent, StudioCrowd, WorldMap
from generative_agents.skills import DatabaseSkillRegistry


def _definition() -> dict:
    return {
        "schema_version": 1,
        "experiment": {
            "key": "portable-test",
            "name": "Portable Test",
            "goal": "prove physical package isolation",
            "timezone": "Asia/Shanghai",
        },
        "engine": {
            "algorithm_version": "ga-cn-v1",
            "brain_skill": "test-brain",
            "brain_revision_id": "must-be-removed",
            "brain_revision_hash": "a" * 64,
        },
        "simulation": {
            "start_time": "2026-09-03T08:00:00+08:00",
            "stride_minutes": 10,
            "max_steps": 3,
            "checkpoint_interval_steps": 1,
            "checkpoint_retention": 2,
            "random_seed": 42,
            "log_level": "INFO",
        },
        "results": {"agent_step_projection_interval_steps": 1, "capture_model_payloads": False},
        "models": {
            "chat": {
                "provider": "vllm",
                "model": "local-test",
                "base_url": "http://127.0.0.1:8888/v1",
            },
            "embedding": {
                "provider": "openai_compatible",
                "model": "local-test",
                "base_url": "http://127.0.0.1:5002/v1",
            },
        },
        "world": {
            "world_key": "test-world",
            "world_name": "Test World",
            "definition": {
                "world": "test-world",
                "size": [1, 1],
                "size_unit": "TILE",
                "tile_size": 32,
                "tile_address_keys": ["world", "sector", "arena", "game_object"],
                "tiles": [
                    {
                        "coord": [0, 0],
                        "collision": False,
                        "address": ["test-world", "test-sector", "test-arena", "test-object"],
                    }
                ],
            },
            "assets": [],
            "map_id": "must-not-survive",
            "map_snapshot_hash": "b" * 64,
        },
        "agents": [],
    }


def _experiment(tmp_path: Path) -> Path:
    source = tmp_path / "author-skill" / "test-brain"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: test-brain\ndescription: test brain\n---\n\n# Brain\n\nReturn WAIT.\n",
        encoding="utf-8",
    )
    package = tmp_path / "renamable-experiment-directory"
    ExperimentPackageBuilder().build_directory(
        package,
        definition=_definition(),
        skills=[SkillSource("test-brain", "brain", source / "SKILL.md")],
        brain_skill="test-brain",
    )
    return package


def test_quality_overview_and_export_rebuild_all_committed_attempts(tmp_path: Path) -> None:
    """A UI export archives the same full-Run diagnostics as the read-only view."""
    var_dir = tmp_path / "var"
    package_root = var_dir / "packages"
    experiment = _experiment(package_root)
    run_root = RunService().create(experiment, package_root / "runs" / "quality", requested_steps=3)
    manifest = validate_run_directory(run_root)
    run_id = manifest.run_id
    attempts = ["b1d1b2dc-a5f3-4558-95cb-cb0f40f4fcf8", "28e20a39-55e0-4fdd-af56-ddb52743778d"]
    (run_root / "frames").mkdir()
    for step in (1, 2, 3):
        calls = [{"event": "mcp.call", "skill": "test-brain", "tool": tool,
                  "input_text": "{}", "output_text": "boundary rejected", "is_error": True}
                 for tool in ("world-navigate", "world-act")]
        if step == 1:
            calls.insert(0, {"event": "mcp.call", "skill": "test-brain", "tool": "memory-stream-search",
                             "input_text": "{}", "output_text": "[]", "is_error": False})
        result = {
            "run_id": run_id, "attempt_id": attempts[0 if step == 1 else 1], "step_no": step,
            "virtual_time": "2026-09-03T08:00:00+08:00", "agents": [], "conversations": [],
            "memory_deltas": [], "schedule_revisions": [], "domain_events": [], "committed_model_usage": [],
            "effects": [{"effect_id": f"brain-{step}", "kind": "SKILL_EXECUTED", "agent_keys": ["test-agent"],
                         "payload": {"execution_source": "BRAIN_RUNTIME", "trace": calls}}],
        }
        (run_root / "frames" / f"step-{step:06d}.json.gz").write_bytes(
            gzip.compress(json.dumps({"schema_version": 1, "result": result}).encode(), mtime=0)
        )
    status = RunStatus.model_validate(json.loads((run_root / "status.json").read_text(encoding="utf-8")))
    status.status = RunState.PAUSED
    status.committed_step = 2
    atomic_write_json(run_root / "status.json", status.model_dump(mode="json"))
    # Represents the old Attempt-local report. Reads and exports must not rewrite it.
    old_report = {"quality_status": "WARNING", "evaluated_agent_steps": 1,
                  "issues": [{"code": "MCP_TOOL_ERROR", "step_no": 2}], "evaluator": {"status": "SKIPPED"}}
    atomic_write_json(run_root / "artifacts" / "quality-report.json", old_report)
    old_bytes = (run_root / "artifacts" / "quality-report.json").read_bytes()
    old_id = hashlib.sha256(old_bytes).hexdigest()[:32]
    snapshot = {p.relative_to(run_root).as_posix(): p.read_bytes() for p in run_root.rglob("*") if p.is_file()}
    app = create_studio_app(database_url=f"sqlite:///{(tmp_path / 'studio.sqlite').as_posix()}", var_dir=var_dir)
    with TestClient(app) as client:
        assert client.post("/api/studio/packages/rebuild").status_code == 200
        response = client.get(f"/api/studio/runs/{run_id}")
        assert response.status_code == 200
        quality = response.json()["quality"]
        assert quality["source_committed_step"] == 2
        assert quality["evaluated_agent_steps"] == 2
        assert len(quality["issues"]) == 5
        assert {issue["source"]["attempt_id"] for issue in quality["issues"]} == set(attempts)
        assert len({issue["issue_id"] for issue in quality["issues"]}) == 5
        assert {p.relative_to(run_root).as_posix(): p.read_bytes() for p in run_root.rglob("*") if p.is_file()} == snapshot
        exported = client.post(f"/api/studio/runs/{run_id}/artifact-jobs", json={"job_type": "RESULT_BUNDLE", "parameters": {}})
        assert exported.status_code == 201, exported.text
        artifacts = client.get(f"/api/studio/runs/{run_id}/results/operations").json()["artifacts"]
        report = next(a for a in artifacts if a["logical_name"].startswith("quality-report-step-"))
        report_response = client.get(f"/api/studio/runs/{run_id}/artifacts/{report['artifact_id']}/download")
        assert report_response.json() == quality
        assert report["artifact_id"] == hashlib.sha256(report_response.content).hexdigest()[:32]
        assert report["artifact_id"] != old_id
        assert client.get(f"/api/studio/runs/{run_id}/artifacts/{old_id}/download").content == old_bytes
        bundle = next(a for a in artifacts if a["logical_name"] == exported.json()["artifact_name"])
        bundle_bytes = client.get(f"/api/studio/runs/{run_id}/artifacts/{bundle['artifact_id']}/download").content
        with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as archive:
            assert json.loads(archive.read(f"artifacts/{report['logical_name']}")) == quality
            assert "artifacts/quality-report.json" not in archive.namelist()
        repeated = client.post(f"/api/studio/runs/{run_id}/artifact-jobs", json={"job_type": "RESULT_BUNDLE", "parameters": {}})
        assert repeated.status_code == 201
        assert repeated.json()["artifact_name"] != exported.json()["artifact_name"]
        assert (run_root / "artifacts" / exported.json()["artifact_name"]).read_bytes() == bundle_bytes
        assert client.get(f"/api/studio/runs/{run_id}").json()["quality"] == quality
    assert (run_root / "artifacts" / "quality-report.json").read_bytes() == old_bytes
    for relative, content in snapshot.items():
        assert (run_root / relative).read_bytes() == content


def test_runtime_finalization_keeps_diagnostics_from_before_resume(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import replace
    from uuid import uuid4

    from generative_agents.ga_runtime.control import FileRunControl
    from generative_agents.ga_runtime.executor import _StatusCommitter
    from generative_agents.runtime.results import StepEffectKind, StepEffectRecord

    experiment = _experiment(tmp_path)
    run_root = tmp_path / "run"
    commit = _StatusCommitter.commit

    def record_test_call_and_pause(self, result, *, force_checkpoint):
        # Inject model-independent Brain audit facts before the real commit.
        effect = StepEffectRecord(
            effect_id=uuid4(), sequence=0, kind=StepEffectKind.SKILL_EXECUTED,
            agent_keys=("test-agent",), payload={"execution_source": "BRAIN_RUNTIME", "trace": [
                {"event": "mcp.call", "skill": "test-brain", "tool": "world-navigate",
                 "input_text": "{}", "output_text": "outside vision", "is_error": True},
            ]},
        )
        outcome = commit(self, replace(result, effects=(*result.effects, effect)), force_checkpoint=force_checkpoint)
        if result.step_no == 1:
            FileRunControl(run_root).request_pause()
        return outcome

    monkeypatch.setattr(_StatusCommitter, "commit", record_test_call_and_pause)
    paused = RunService().start(experiment, run_root, requested_steps=2)
    assert paused.status == RunState.PAUSED and paused.committed_step == 1
    first = json.loads((run_root / "artifacts" / "quality-report.json").read_text(encoding="utf-8"))
    assert len(first["issues"]) == 1
    completed = RunService().resume(run_root)
    assert completed.status == RunState.COMPLETED and completed.committed_step == 2
    final = json.loads((run_root / "artifacts" / "quality-report.json").read_text(encoding="utf-8"))
    assert final["source_committed_step"] == 2
    assert final["evaluated_agent_steps"] == 2
    assert [issue["step_no"] for issue in final["issues"]] == [1, 2]
    assert final["issues"][0] == first["issues"][0]
    assert len({issue["source"]["attempt_id"] for issue in final["issues"]}) == 2


def test_experiment_is_physical_and_archive_name_is_not_identity(tmp_path: Path) -> None:
    package = _experiment(tmp_path)
    manifest = validate_experiment_directory(package)
    engine = json.loads((package / "runtime" / "engine.json").read_text(encoding="utf-8"))
    world = json.loads((package / "world" / "world.json").read_text(encoding="utf-8"))
    assert "brain_revision_id" not in engine
    assert "map_id" not in world
    semantic_index = json.loads(
        (package / "world" / "semantic-index.json").read_text(encoding="utf-8")
    )
    assert [item["kind"] for item in semantic_index["nodes"]] == [
        "WORLD",
        "SECTOR",
        "ARENA",
        "GAME_OBJECT",
    ]

    archive_one = seal_directory(package, tmp_path / "anything.gaexp")
    archive_two = seal_directory(package, tmp_path / "renamed.gaexp")
    assert hashlib.sha256(archive_one.read_bytes()).digest() == hashlib.sha256(archive_two.read_bytes()).digest()

    renamed = tmp_path / "a-completely-different-directory-name"
    package.rename(renamed)
    assert validate_experiment_directory(renamed).experiment.experiment_id == manifest.experiment.experiment_id


def test_run_embeds_experiment_and_replay_uses_run_id(tmp_path: Path) -> None:
    experiment = _experiment(tmp_path)
    run_root = RunService().create(experiment, tmp_path / "display-name-only", requested_steps=2)
    manifest = validate_run_directory(run_root)
    experiment_id = validate_experiment_directory(run_root / "experiment").experiment.experiment_id
    assert manifest.experiment.experiment_id == experiment_id
    assert manifest.run_id != experiment_id

    # Prove the Run does not need the original experiment directory.
    for child in sorted(experiment.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    experiment.rmdir()
    assert validate_run_directory(run_root).run_id == manifest.run_id

    with ReplayReader(run_root) as replay:
        summary = replay.summary()
        assert replay.semantic_index()["levels"] == [
            "WORLD",
            "SECTOR",
            "ARENA",
            "GAME_OBJECT",
        ]
    assert summary["run_id"] == manifest.run_id
    assert summary["experiment_id"] == experiment_id
    assert summary["status"] == "CREATED"


def test_replay_reads_only_committed_frames(tmp_path: Path) -> None:
    experiment = _experiment(tmp_path)
    run_root = RunService().create(experiment, tmp_path / "run", requested_steps=2)
    manifest = validate_run_directory(run_root)
    result = {
        "run_id": manifest.run_id,
        "attempt_id": "b1d1b2dc-a5f3-4558-95cb-cb0f40f4fcf8",
        "step_no": 1,
        "virtual_time": "2026-09-03T08:00:00+08:00",
        "agents": [],
        "conversations": [],
        "memory_deltas": [],
        "schedule_revisions": [],
        "domain_events": [],
        "committed_model_usage": [],
        "effects": [],
    }
    compressed = gzip.compress(
        json.dumps({"schema_version": 1, "result": result}, sort_keys=True, separators=(",", ":")).encode(),
        mtime=0,
    )
    frame = run_root / "frames" / "step-000001.json.gz"
    frame.parent.mkdir()
    frame.write_bytes(compressed)
    atomic_write_json(
        run_root / "projection.json",
        {
            "run_id": manifest.run_id,
            "available_step": 1,
            "virtual_time": result["virtual_time"],
            "result_version": 1,
            "steps": {
                "1": {
                    "frame": "frames/step-000001.json.gz",
                    "frame_sha256": hashlib.sha256(compressed).hexdigest(),
                    "checkpoint": None,
                }
            },
        },
    )
    status = RunStatus.model_validate(json.loads((run_root / "status.json").read_text()))
    status.status = RunState.PAUSED
    status.committed_step = 1
    atomic_write_json(run_root / "status.json", status.model_dump(mode="json"))
    with ReplayReader(run_root) as replay:
        assert replay.available_steps() == (1,)
        assert replay.read_step(1) == result
        assert replay.state_at(1)["step_no"] == 1


def test_replay_does_not_expose_uncommitted_frame_files(tmp_path: Path) -> None:
    experiment = _experiment(tmp_path)
    run_root = RunService().create(experiment, tmp_path / "run", requested_steps=2)
    manifest = validate_run_directory(run_root)
    result = {
        "run_id": manifest.run_id,
        "attempt_id": "b1d1b2dc-a5f3-4558-95cb-cb0f40f4fcf8",
        "step_no": 1,
        "virtual_time": "2026-09-03T08:00:00+08:00",
        "agents": [],
    }
    frame = run_root / "frames" / "step-000001.json.gz"
    frame.parent.mkdir()
    frame.write_bytes(gzip.compress(json.dumps({"result": result}).encode(), mtime=0))
    with ReplayReader(run_root) as replay:
        assert replay.available_steps() == ()
        try:
            replay.read_step(1)
        except IndexError:
            pass
        else:  # pragma: no cover - explicit contract assertion
            raise AssertionError("uncommitted frame became visible to Replay")


def test_replay_serves_only_assets_embedded_in_the_run(tmp_path: Path) -> None:
    experiment = _experiment(tmp_path)
    content = b"package-owned-rendering-asset"
    logical_path = "assets/demo.bin"
    (experiment / logical_path).parent.mkdir(parents=True)
    (experiment / logical_path).write_bytes(content)
    world_path = experiment / "world" / "world.json"
    world = json.loads(world_path.read_text())
    world["assets"] = [
        {
            "logical_path": logical_path,
            "asset_hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
            "media_type": "application/octet-stream",
            "size": len(content),
        }
    ]
    atomic_write_json(world_path, world)
    write_integrity_manifest(experiment)
    validate_experiment_directory(experiment)
    run_root = RunService().create(experiment, tmp_path / "asset-run", requested_steps=1)
    with ReplayReader(run_root) as replay:
        assert replay.asset(logical_path) == (content, "application/octet-stream")
        try:
            replay.asset("manifest.json")
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("Replay exposed a non-asset package file")


def test_file_memory_stream_has_no_database_file(tmp_path: Path) -> None:
    from datetime import datetime

    memory = FileMemoryStream(
        tmp_path / "memory",
        run_id="7d5a7f5a-9e38-4d14-8f20-a493025e9ef3",
        attempt_id="ad1d40fc-bcd6-4d23-9519-89c6a6e457cc",
    )
    memory.begin_step(1, datetime.fromisoformat("2026-09-03T08:00:00+08:00"))
    created = memory.append(agent_key="alice", content="Alice saw the cafe", poignancy=4)
    assert memory.search(agent_key="alice", query="cafe")[0]["id"] == created["id"]
    assert (tmp_path / "memory" / "memories.json").is_file()
    assert not list((tmp_path / "memory").glob("*.sqlite*"))


def test_studio_catalog_is_rebuildable_from_package_files(tmp_path: Path) -> None:
    experiment = _experiment(tmp_path)
    database = create_database(f"sqlite:///{tmp_path / 'studio.sqlite'}")
    Base.metadata.create_all(database.engine)
    try:
        catalog = StudioPackageCatalogService(database)
        records = catalog.rebuild([tmp_path])
        assert [(item.package_kind, item.package_id) for item in records] == [
            ("experiment", validate_experiment_directory(experiment).experiment.experiment_id)
        ]
        assert catalog.list(package_kind="experiment")[0].location == str(experiment.resolve())
    finally:
        database.close()


def test_runtime_executes_package_without_studio_database(tmp_path: Path) -> None:
    experiment = _experiment(tmp_path)
    status = RunService().start(experiment, tmp_path / "executed-run", requested_steps=1)
    assert status.status is RunState.COMPLETED
    assert status.committed_step == 1
    with ReplayReader(tmp_path / "executed-run") as replay:
        assert replay.available_steps() == (1,)
        assert replay.read_step(1)["agents"] == []


def test_public_agents_have_no_system_or_builtin_variant(tmp_path: Path) -> None:
    database = create_database(f"sqlite:///{tmp_path / 'studio.sqlite'}")
    Base.metadata.create_all(database.engine)
    resources = StudioResourceService(database)
    try:
        agent = resources.create_agent(
            {
                "agent_key": "user-created-agent",
                "name": "User Created Agent",
                "scratch": {
                    "age": 30,
                    "innate": "calm",
                    "learned": "careful",
                    "lifestyle": "regular",
                    "daily_plan": "work",
                },
            }
        )
        crowd = resources.create_crowd(
            name="User Crowd",
            agent_ids=[agent["id"]],
        )
        assert "is_builtin" not in agent
        assert "is_builtin" not in crowd
        assert "is_builtin" not in StudioAgent.__table__.columns
        assert "is_builtin" not in StudioCrowd.__table__.columns
    finally:
        database.close()


def test_studio_selection_physically_copies_map_agent_brain_and_skill(tmp_path: Path) -> None:
    database = create_database(f"sqlite:///{tmp_path / 'studio.sqlite'}")
    Base.metadata.create_all(database.engine)
    resources = StudioResourceService(database)
    skills = DatabaseSkillRegistry(database, cache_root=tmp_path / "skill-cache")
    world = {
        "world_key": "copy-world",
        "world_name": "Copy World",
        "definition": {
            "world": "Copy World",
            "size": [1, 1],
            "size_unit": "TILE",
            "tile_size": 32,
            "tile_address_keys": ["world", "sector", "arena", "game_object"],
            "tiles": [
                {
                    "coord": [0, 0],
                    "collision": False,
                    "address": ["Copy World", "Home", "Room", "Desk"],
                }
            ],
        },
        "assets": [],
        "map_id": None,
        "map_snapshot_hash": None,
    }
    try:
        with database.session_factory.begin() as session:
            public_map = WorldMap(
                map_key="copy-world",
                name="Copy World",
                world_json=world,
                world_hash="0" * 64,
            )
            session.add(public_map)
            session.flush()
            map_id = public_map.id
        child = skills.create(name="copy-child", description="child", kind="atomic")
        brain = skills.create(name="copy-brain", description="brain", kind="brain")
        brain_markdown = brain.markdown + "\n\nCall $copy-child and then stop.\n"
        brain = skills.save("copy-brain", brain_markdown)
        agent = resources.create_agent(
            StudioAgentDefinition.model_validate(
                {
                    "agent_key": "alice-copy",
                    "name": "Alice",
                    "scratch": {
                        "age": 30,
                        "innate": "calm",
                        "learned": "careful",
                        "lifestyle": "regular",
                        "daily_plan": "work",
                    },
                }
            )
        )
        model = resources.create_model_preset(
            name="Local models",
            config={
                "chat": {
                    "provider": "vllm",
                    "model": "local-test",
                    "base_url": "http://127.0.0.1:8888/v1",
                },
                "embedding": {
                    "provider": "openai_compatible",
                    "model": "local-test",
                    "base_url": "http://127.0.0.1:5002/v1",
                },
            },
        )
        embedding_model = resources.create_model_preset(name="Selected embedding", config={
            "embedding": {"provider": "openai_compatible", "model": "separate-embedding",
                          "base_url": "http://127.0.0.1:5002/v1", "credential_env": "GA_MODEL_TEST_EMBEDDING"},
        })
        crowd = resources.create_crowd(name="Copy Crowd", agent_ids=[agent["id"]])
        evaluator = resources.create_evaluator(
            name="Copy Evaluator",
            config={"key": "copy-evaluator", "type": "assertion", "expression": "true"},
        )
        workspace_service = ExperimentWorkspaceService(
            database,
            package_root=tmp_path / "packages",
            var_dir=tmp_path,
            skill_registry=skills,
        )
        created = workspace_service.create(
            ExperimentSelection(
                name="Copied Experiment",
                goal="prove selection isolation",
                map_id=map_id,
                brain_skill_id=brain.resource_id,
                model_preset_id=model["id"],
                embedding_model_preset_id=embedding_model["id"],
                crowd_ids=(crowd["id"],),
                evaluator_ids=(evaluator["id"],),
            )
        )
        package = Path(created["location"])
        copied_models = json.loads((package / "models/models.json").read_text())
        assert copied_models["embedding"]["model"] == "separate-embedding"
        assert copied_models["embedding"]["credential_env"] == "GA_MODEL_TEST_EMBEDDING"
        assert embedding_model["id"] not in json.dumps(copied_models)
        before = (package / "integrity" / "sha256.json").read_bytes()
        packaged_agents = json.loads((package / "agents" / "index.json").read_text())
        assert packaged_agents["agents"][0]["coord"] == [0, 0]
        assert packaged_agents["agents"][0]["spatial"]["address"] == {
            "initial_location": ["Copy World", "Home", "Room", "Desk"]
        }
        registry = json.loads((package / "skills" / "registry.json").read_text())
        assert {item["skill_id"] for item in registry["skills"]} == {
            "copy-brain",
            "copy-child",
        }
        evaluation = json.loads(
            (package / "evaluation" / "evaluators.json").read_text()
        )
        assert evaluation["evaluators"] == [
            {"key": "copy-evaluator", "type": "assertion", "expression": "true"}
        ]

        # Change and delete every source.  The experiment is already physical.
        changed = dict(agent["definition"])
        changed["name"] = "Alice Changed"
        resources.save_agent(
            agent["id"],
            changed,
            expected_row_version=agent["row_version"],
        )
        skills.delete("copy-brain")
        skills.delete("copy-child")
        resources.delete("agent", agent["id"])
        resources.delete("crowd", crowd["id"])
        resources.delete("model", model["id"])
        resources.delete("model", embedding_model["id"])
        resources.delete("evaluator", evaluator["id"])
        with database.session_factory.begin() as session:
            session.delete(session.get(WorldMap, map_id))

        assert validate_experiment_directory(package).experiment.experiment_id == created["experiment_id"]
        assert (package / "integrity" / "sha256.json").read_bytes() == before
        packaged_agents = json.loads((package / "agents" / "index.json").read_text())
        assert packaged_agents["agents"][0]["name"] == "Alice"
    finally:
        database.close()


def test_studio_replaces_map_upload_ids_with_package_paths() -> None:
    digest = "a" * 64
    world = {
        "assets": [
            {
                "asset_hash": f"sha256:{digest}",
                "logical_path": "assets/maps/source.png",
            }
        ],
        "definition": {
            "editor_v2": {
                "material_sources": [
                    {
                        "name": "Source",
                        "kind": "UPLOADED",
                        "asset_id": "studio-only-id",
                        "asset_hash": digest,
                    }
                ]
            }
        },
    }

    ExperimentWorkspaceService._replace_uploaded_world_asset_references(world)

    source = world["definition"]["editor_v2"]["material_sources"][0]
    assert source["kind"] == "BUNDLED"
    assert source["bundled_path"] == "assets/maps/source.png"
    assert "asset_id" not in source


def test_established_console_reads_and_edits_package_backed_experiments(tmp_path: Path) -> None:
    var_dir = tmp_path / "var"
    package_root = var_dir / "packages"
    experiment = _experiment(package_root)
    run_root = package_root / "runs" / "display-name-only"
    RunService().start(experiment, run_root, requested_steps=2)
    experiment_id = validate_experiment_directory(experiment).experiment.experiment_id
    run_id = validate_run_directory(run_root).run_id
    app = create_studio_app(
        database_url=f"sqlite:///{(tmp_path / 'studio.sqlite').as_posix()}",
        var_dir=var_dir,
    )

    with TestClient(app) as client:
        rebuilt = client.post("/api/studio/packages/rebuild")
        detail = client.get(f"/api/studio/experiments/{experiment_id}")
        saved = client.put(
            f"/api/studio/experiments/{experiment_id}",
            json={"definition": detail.json()["definition"]},
        )
        duplicate = client.post(f"/api/studio/experiments/{experiment_id}/duplicate", json={})
        duplicate_id = duplicate.json()["experiment_id"]
        archived = client.post(f"/api/studio/experiments/{experiment_id}/archive", json={})
        archived_list = client.get("/api/studio/experiments?archived=archived")
        restored = client.post(f"/api/studio/experiments/{experiment_id}/restore", json={})
        timeline = client.get(f"/api/studio/runs/{run_id}/results/timeline")
        operations = client.get(f"/api/studio/runs/{run_id}/results/operations")
        attempts = client.get(f"/api/studio/runs/{run_id}/attempts")
        replay_manifest = client.get(f"/api/studio/runs/{run_id}/replay/manifest")
        replay_steps = client.get(f"/api/studio/runs/{run_id}/replay/steps")
        removed = client.delete(f"/api/studio/experiments/{duplicate_id}")

    assert rebuilt.status_code == 200
    assert detail.status_code == 200
    assert saved.status_code == 200
    assert saved.json()["definition"]["experiment"]["name"] == "Portable Test"
    assert duplicate.status_code == 201
    assert duplicate_id != experiment_id
    assert archived.status_code == 200
    assert {item["id"] for item in archived_list.json()["items"]} == {experiment_id}
    assert restored.status_code == 200
    assert len(timeline.json()["steps"]) == 2
    assert {item["logical_name"] for item in operations.json()["artifacts"]} == {
        "quality-report.json"
    }
    assert len(attempts.json()["items"]) == 1
    assert attempts.json()["items"][0]["end_step"] == 2
    assert replay_manifest.json()["schema_version"] == 2
    assert replay_manifest.json()["run_id"] == run_id
    assert replay_manifest.json()["experiment_id"] == experiment_id
    assert "revision_id" not in replay_manifest.json()
    assert len(replay_steps.json()["steps"]) == 2
    assert removed.status_code == 204


def test_workspace_create_persists_owner_and_tags_in_overview(tmp_path: Path, monkeypatch) -> None:
    """The create wizard's presentation metadata survives the first overview read."""
    source = _experiment(tmp_path)
    var_dir = tmp_path / "var"
    destination = var_dir / "packages" / "experiments" / "metadata-test"
    destination.parent.mkdir(parents=True)
    source.rename(destination)
    experiment_id = validate_experiment_directory(destination).experiment.experiment_id

    captured = {}

    def fake_create(self, selection):
        captured["selection"] = selection
        return {
            "experiment_id": experiment_id,
            "location": str(destination),
            "content_sha256": "test",
            "name": selection.name,
        }

    monkeypatch.setattr(ExperimentWorkspaceService, "create", fake_create)
    app = create_studio_app(
        database_url=f"sqlite:///{(tmp_path / 'studio.sqlite').as_posix()}",
        var_dir=var_dir,
    )
    with TestClient(app) as client:
        rebuilt = client.post("/api/studio/packages/rebuild")
        shell = client.get("/").text
        console_script = client.get("/static/console/console-api.js").text
        created = client.post(
            "/api/studio/experiments",
            json={
                "name": "案例1",
                "goal": "验证晨间流程",
                "owner": "当前研究员",
                "tags": ["书稿案例", "晨间流程", "中文", "单Agent"],
                "map_id": "map",
                "brain_skill_id": "brain",
                "model_preset_id": "model",
            },
        )
        detail = client.get(f"/api/studio/experiments/{experiment_id}")

    assert rebuilt.status_code == 200
    assert 'id="newExperimentOwner"' in shell
    assert "owner: $('newExperimentOwner').value.trim()" in console_script
    assert "tags: $('newExperimentTag').value.split(/[,，]/)" in console_script
    assert created.status_code == 201, created.text
    assert captured["selection"].name == "案例1"
    assert created.json()["owner"] == "当前研究员"
    assert created.json()["tags"] == ["书稿案例", "晨间流程", "中文", "单Agent"]
    assert detail.json()["owner"] == "当前研究员"
    assert detail.json()["tags"] == ["书稿案例", "晨间流程", "中文", "单Agent"]
