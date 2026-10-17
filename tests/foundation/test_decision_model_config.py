"""Decision connections remain explicit, portable, and isolated in experiments."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from generative_agents.ga_protocol.packages.definition import _experiment_definition, read_experiment_assembly
from generative_agents.ga_protocol.packages.io import open_package, seal_directory
from generative_agents.ga_protocol.packages.resources import read_resource_set
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_protocol.schemas.experiment import DecisionModelConfig, ModelsConfig
from generative_agents.ga_studio.experiments.workspace import ExperimentSelection, ExperimentWorkspaceService
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.resources.models import ModelService, ModelServiceError, ModelServiceInput
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.storage.models import WorldMap
from tests.foundation.test_resource_exchange import agent, import_all, machines
from tests.test_portable_package_protocol import _definition


def decision_input(**changes):
    values = {
        "name": "本地决策模型",
        "purpose": "decision",
        "base_url": "http://127.0.0.1:5003/v1/systemone",
        "model": "laya-multilingual",
    }
    return ModelServiceInput(**(values | changes))


@pytest.mark.parametrize("base_url", [
    "http://127.0.0.1:5003", "http://127.0.0.1:5003/v1",
    "http://127.0.0.1:5003/v1/systemone", "http://127.0.0.1:5003/v1/systemone/",
])
def test_decision_config_normalizes_service_endpoint_without_credentials(base_url):
    config = DecisionModelConfig(model="laya-multilingual", base_url=base_url)
    assert config.provider == "unsloth"
    assert str(config.base_url) == "http://127.0.0.1:5003/v1"
    assert config.credential_env is None
    assert config.timeout_seconds > 0 and config.retry_attempts >= 1
    assert ModelsConfig.model_validate(_definition()["models"]).decision is None
    models = ModelsConfig.model_validate(_definition()["models"] | {"decision": config.model_dump(mode="json")})
    assert models.decision == config


@pytest.mark.parametrize("model", ["", " ", "auto", "AUTO"])
def test_decision_config_requires_explicit_model(model):
    with pytest.raises(ValidationError):
        DecisionModelConfig(model=model, base_url="http://127.0.0.1:5003/v1")


def test_decision_model_crud_and_purpose_filter(database, tmp_path):
    models = ModelService(database, tmp_path)
    chat = models.create(ModelServiceInput(name="生成式", purpose="chat", model="local-chat", base_url="http://chat/v1"))
    saved = models.create(decision_input())
    config = saved["config"]["decision"]
    assert config["provider"] == "unsloth" and config["model"] == "laya-multilingual"
    assert config["base_url"] == "http://127.0.0.1:5003/v1"
    assert config["credential_env"] is None
    assert saved["credential_configured"]["decision"] is False
    assert models.get_model(saved["id"])["config"]["decision"] == config
    selected = models.list_models(purpose="decision")
    assert selected["total"] == 1 and selected["items"][0]["id"] == saved["id"]
    assert selected["items"][0]["purposes"][0]["purpose"] == "decision"
    assert models.list_models(purpose="chat")["items"][0]["id"] == chat["id"]
    updated = models.update(saved["id"], decision_input(
        name="另一台决策服务", model="laya-updated", base_url="http://127.0.0.1:5013/v1",
        row_version=saved["row_version"],
    ))
    assert updated["config"]["decision"]["model"] == "laya-updated"
    with pytest.raises(ModelServiceError) as conflict:
        models.update(saved["id"], decision_input(row_version=saved["row_version"]))
    assert conflict.value.status_code == 409
    models.delete(saved["id"])
    assert models.list_models(purpose="decision")["total"] == 0
    with pytest.raises(ModelServiceError) as missing:
        models.get_model(saved["id"])
    assert missing.value.status_code == 404


def mock_decision_endpoint(monkeypatch, payload, status=200):
    requests = []
    original_client = httpx.Client

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    return requests


def test_decision_probe_uses_systemone_question_protocol_without_api_key(database, tmp_path, monkeypatch):
    models = ModelService(database, tmp_path)
    saved = models.create(decision_input())
    requests = mock_decision_endpoint(monkeypatch, {"answers": {"connection": {"type": "noul", "noul": 0.75}}})
    assert models.test(saved["id"], "decision")["ok"] is True
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST" and str(request.url) == "http://127.0.0.1:5003/v1/systemone"
    assert "authorization" not in request.headers
    payload = json.loads(request.content)
    assert payload["model"] == "laya-multilingual" and payload["state"]
    assert payload["questions"]["connection"]["type"] == "noul"
    assert payload["questions"]["connection"]["instructions"]
    assert "messages" not in payload


@pytest.mark.parametrize("payload", [
    {}, {"answers": {}}, {"answers": {"connection": {"type": "noul", "noul": -0.1}}},
    {"answers": {"connection": {"type": "noul", "noul": 1.1}}},
    {"answers": {"connection": {"type": "noul", "noul": "unavailable"}}},
    {"answers": {"connection": {"type": "noul", "noul": True}}},
    {"answers": {"connection": {"type": "different", "noul": 0.75}}},
])
def test_decision_probe_rejects_missing_or_invalid_scores(database, tmp_path, monkeypatch, payload):
    models = ModelService(database, tmp_path)
    saved = models.create(decision_input())
    mock_decision_endpoint(monkeypatch, payload)
    with pytest.raises(ModelServiceError) as failure:
        models.test(saved["id"], "decision")
    assert failure.value.status_code == 422


def test_decision_config_resource_export_import_preserves_transport(machines, tmp_path):
    source_db, source, source_store = machines("source")
    target_db, target, target_store = machines("target")
    source_models = ModelService(source_db, source_store.root.parent)
    original = source_models.create(decision_input())
    archive = source.export_resource("model", original["id"], tmp_path / "decision.zip")
    resources = read_resource_set(archive)
    assert len(resources.resources) == 1
    record = resources.resources[0]
    assert record.kind == "model" and record.definition["decision"] == original["config"]["decision"]
    assert original["id"] not in json.dumps(record.model_dump(mode="json"))
    imported = import_all(target, archive)["imported"][0]
    target_models = ModelService(target_db, target_store.root.parent)
    restored = target_models.get_model(imported["id"])
    assert restored["id"] != original["id"]
    assert restored["config"]["decision"] == original["config"]["decision"]
    assert restored["credential_configured"]["decision"] is False
    returned = target.export_resource("model", restored["id"], tmp_path / "returned.zip")
    assert source.preview_import(returned)["resources"][0]["status"] == "reuse"


def selected_decision_experiment(machines, tmp_path):
    database, _, store = machines("source")
    resources = StudioResourceService(database)
    models = ModelService(database, store.root.parent)
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "skill-cache")
    brain = registry.create(name="decision-test-brain", description="brain", kind="brain")
    person = agent(database, store, images=False)
    definition = _definition()
    world = definition["world"]
    world.pop("map_id")
    world.pop("map_snapshot_hash")
    with database.session_factory.begin() as session:
        row = WorldMap(map_key=world["world_key"], name=world["world_name"], world_json=world, world_hash="a" * 64)
        session.add(row)
        session.flush()
        map_id = row.id
    chat = resources.create_model_preset(name="生成与向量", config=definition["models"])
    decision = models.create(decision_input())
    workspace = ExperimentWorkspaceService(database, package_root=tmp_path / "packages",
        var_dir=store.root.parent, skill_registry=registry)
    created = workspace.create(ExperimentSelection(name="独立决策模型实验", goal="physical copy", map_id=map_id,
        brain_skill_id=brain.resource_id, model_preset_id=chat["id"],
        decision_model_preset_id=decision["id"], agent_ids=(person["id"],)))
    return workspace, created, models, decision


def test_selected_decision_model_is_copied_and_survives_public_deletion_and_sealing(machines, tmp_path):
    _, created, models, decision = selected_decision_experiment(machines, tmp_path)
    experiment = Path(created["location"])
    expected = _experiment_definition(experiment)[1]["models"]["decision"]
    assert expected == decision["config"]["decision"]
    assembly = read_experiment_assembly(experiment)
    assert assembly.models["decision"].key == decision["key"]
    models.update(decision["id"], decision_input(model="laya-public-changed", row_version=decision["row_version"]))
    models.delete(decision["id"])
    assert _experiment_definition(experiment)[1]["models"]["decision"] == expected
    archive = seal_directory(experiment, tmp_path / "decision.gaexp")
    with open_package(archive) as sealed:
        manifest = validate_experiment_directory(sealed)
        assert manifest.experiment.experiment_id == created["experiment_id"]
        assert _experiment_definition(sealed)[1]["models"]["decision"] == expected
    exported = read_resource_set(archive)
    model = next(record for record in exported.resources if record.kind == "model" and record.key == decision["key"])
    assert model.definition["decision"] == expected


def test_experiment_decision_settings_normalize_endpoint_and_can_be_cleared(machines, tmp_path):
    workspace, created, public_models, original = selected_decision_experiment(machines, tmp_path)
    experiment = Path(created["location"])
    settings = _experiment_definition(experiment)[1]["models"]
    settings["decision"].update(base_url="http://127.0.0.1:5013/v1/systemone", model="laya-experiment-only")
    saved = workspace.update_settings(created["experiment_id"], {"models": settings},
        expected_content_sha256=created["content_sha256"])
    actual = _experiment_definition(experiment)[1]["models"]
    assert actual["decision"]["base_url"] == "http://127.0.0.1:5013/v1"
    assert actual["decision"]["model"] == "laya-experiment-only"
    assert public_models.get_model(original["id"])["config"] == original["config"]

    actual["decision"] = None
    workspace.update_entrypoint(created["experiment_id"], "models", actual,
        expected_content_sha256=saved["content_sha256"])
    assert "decision" not in read_experiment_assembly(experiment).models
    assert _experiment_definition(experiment)[1]["models"].get("decision") is None
    assert public_models.get_model(original["id"])["config"] == original["config"]
    archive = seal_directory(experiment, tmp_path / "decision-cleared.gaexp")
    with open_package(archive) as sealed:
        validate_experiment_directory(sealed)
        assert "decision" not in read_experiment_assembly(sealed).models
        assert _experiment_definition(sealed)[1]["models"].get("decision") is None


def test_public_model_export_omits_absent_optional_decision_config(machines, tmp_path):
    database, exchange, _ = machines("source")
    config = ModelsConfig.model_validate(_definition()["models"]).model_dump(mode="json")
    assert config["decision"] is None
    preset = StudioResourceService(database).create_model_preset(name="仅生成与向量", config=config)
    archive = exchange.export_resource("model", preset["id"], tmp_path / "without-decision.zip")
    record = read_resource_set(archive).resources[0]
    assert set(record.definition) == {"chat", "embedding"}
    assert record.definition["chat"] == config["chat"]
    assert record.definition["embedding"] == config["embedding"]
