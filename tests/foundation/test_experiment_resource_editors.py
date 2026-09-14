import copy
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from generative_agents.ga_protocol import read_json, seal_directory, validate_experiment_directory
from generative_agents.ga_studio.catalog import StudioPackageCatalogService
from generative_agents.ga_studio.experiment_resources import create_experiment_resource_router
from generative_agents.ga_studio.workspace import ExperimentWorkspaceService
from generative_agents.web.portable_api import _experiment_definition
from tests.test_portable_package_protocol import _experiment


@pytest.fixture
def editing(database, tmp_path):
    root = _experiment(tmp_path)
    record = StudioPackageCatalogService(database).upsert(root)
    app = FastAPI()
    app.include_router(create_experiment_resource_router(database, tmp_path))
    client = TestClient(app)
    prefix = f"/api/studio/experiments/{record.package_id}/resources"
    return client, prefix, root, record.package_id


def test_map_edits_are_file_backed_and_reject_stale_saves(editing):
    client, prefix, root, identity = editing
    before = (root / "world/world.json").read_bytes()
    original = client.get(f"{prefix}/maps/{identity}").json()
    body = copy.deepcopy(original)
    body["world"]["world_name"] = "实验地图改名"
    saved = client.put(f"{prefix}/maps/{identity}", json={"row_version": body["row_version"], "world": body["world"]})
    assert saved.status_code == 200, saved.text
    assert client.get(f"{prefix}/maps/{identity}").json()["world"]["world_name"] == "实验地图改名"
    assert (root / "world/world.json").read_bytes() != before
    stale = client.put(f"{prefix}/maps/{identity}", json={"row_version": original["row_version"], "world": original["world"]})
    assert stale.status_code == 409
    validate_experiment_directory(root)


def test_brain_save_updates_package_hash_and_never_changes_source(editing):
    client, prefix, root, identity = editing
    source = root.parent / "author-skill/test-brain/SKILL.md"
    before = source.read_bytes()
    detail = client.get(f"{prefix}/skills/test-brain").json()
    changed = detail["markdown"] + "\n当前实验专用说明。\n"
    result = client.put(f"{prefix}/skills/test-brain", json={"row_version": detail["row_version"], "markdown": changed,
                                                          "scripts": {"scripts/local.py": "def run(): return 'local'\n"}})
    assert result.status_code == 200, result.text
    assert result.json()["script_sources"]["scripts/local.py"].startswith("def run")
    assert source.read_bytes() == before
    assert result.json()["row_version"] != detail["row_version"]
    validate_experiment_directory(root)


@pytest.mark.parametrize("change", ["\nCall $missing-child", "\nCall $test-brain"])
def test_skill_invalid_dependency_or_self_cycle_is_atomic(editing, change):
    client, prefix, root, identity = editing
    detail = client.get(f"{prefix}/skills/test-brain").json()
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = client.put(f"{prefix}/skills/test-brain", json={"row_version": detail["row_version"],
                                                          "markdown": detail["markdown"] + change, "scripts": {}})
    assert result.status_code == 422, result.text
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_crowds_are_local_memberships_preserved_by_general_save(database, tmp_path, editing):
    client, prefix, root, identity = editing
    digest = read_json(root / "integrity/sha256.json")["root_sha256"]
    created = client.post(f"{prefix}/crowds", json={"expected_content_sha256": digest, "name": "实验人群", "agent_ids": []})
    assert created.status_code == 200, created.text
    detail = created.json()
    rejected = client.put(f"{prefix}/crowds/{detail['id']}", json={"row_version": detail["row_version"], "name": "错误", "agent_ids": ["public-agent-id"]})
    assert rejected.status_code == 422
    _, definition = _experiment_definition(root)
    definition["experiment"]["goal"] = "更新概览"
    service = ExperimentWorkspaceService(database, package_root=tmp_path / "packages", var_dir=tmp_path)
    service.replace_definition(identity, definition, expected_content_sha256=detail["row_version"])
    assert client.get(f"{prefix}/crowds").json()["items"][0]["name"] == "实验人群"


def test_new_skill_dependency_and_referenced_deletion(editing):
    client, prefix, root, identity = editing
    detail = client.get(f"{prefix}/skills/test-brain").json()
    created = client.post(f"{prefix}/skills", json={"expected_content_sha256": detail["row_version"],
                                                 "name": "local-child", "description": "实验专用", "kind": "atomic"})
    assert created.status_code == 200, created.text
    saved = client.put(f"{prefix}/skills/test-brain", json={"row_version": created.json()["row_version"],
                                                        "markdown": detail["markdown"] + "\nCall $local-child", "scripts": {}})
    assert saved.status_code == 200, saved.text
    blocked = client.request("DELETE", f"{prefix}/skills/local-child", json={"expected_content_sha256": saved.json()["row_version"]})
    assert blocked.status_code == 422
    assert client.get(f"{prefix}/skills/local-child").status_code == 200


def test_upload_is_package_local_and_sealed_edits_are_rejected(database, editing):
    client, prefix, root, identity = editing
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(buffer, format="PNG")
    digest = read_json(root / "integrity/sha256.json")["root_sha256"]
    uploaded = client.post(f"{prefix}/assets", data={"expected_content_sha256": digest}, files={"file": ("map.png", buffer.getvalue(), "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    assert (root / uploaded.json()["logical_path"]).read_bytes() == buffer.getvalue()
    archive = seal_directory(root, root.parent / "sealed.gaexp")
    StudioPackageCatalogService(database).upsert(archive)
    detail = client.get(f"{prefix}/skills/test-brain").json()
    assert detail["editable"] is False
    assert client.post(f"{prefix}/maps/{identity}/validate", json={}).status_code == 200
    rejected = client.put(f"{prefix}/skills/test-brain", json={"row_version": detail["row_version"], "markdown": detail["markdown"], "scripts": {}})
    assert rejected.status_code == 409


def test_skill_pack_kind_and_trial_use_only_copied_models(editing, monkeypatch):
    client, prefix, root, identity = editing
    digest = read_json(root / "integrity/sha256.json")["root_sha256"]
    created = client.post(f"{prefix}/skills", json={"expected_content_sha256": digest,
                                                 "name": "local-pack", "description": "组合技能", "kind": "pack"})
    assert created.status_code == 200, created.text
    assert client.get(f"{prefix}/skills?kind=pack").json()["items"][0]["name"] == "local-pack"
    calls = []
    def copied_trial(snapshots, name, input_text, context, **model):
        calls.append((snapshots, name, model))
        return {"output": "copied model", "model": model["model"]}
    monkeypatch.setattr("generative_agents.ga_studio.experiment_resources.run_copied_skill_trial", copied_trial)
    result = client.post(f"{prefix}/skills/test-brain/run", json={"input_text": "test", "context": {}, "model_preset_id": "deleted-public-model"})
    assert result.status_code == 200, result.text
    assert result.json()["model"] == "local-test"
    assert set(calls[0][0]) == {"test-brain"}
    assert read_json(root / "integrity/sha256.json")["root_sha256"] == created.json()["row_version"]
