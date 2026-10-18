"""The decision compiler is an explicit Studio setting, not portable model content."""
from __future__ import annotations

import json
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from generative_agents.adapters.web.routes.models import create_model_service_router
from generative_agents.ga_protocol.packages.resources import read_resource_set
from generative_agents.ga_studio.resources.exchange import ResourceExchangeService
from generative_agents.ga_studio.resources.models import ModelService, ModelServiceInput
from generative_agents.ga_studio.storage.assets import AssetStore
from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import Base


URL = "/api/studio/resources/model-services"


@pytest.fixture
def compiler_center(tmp_path):
    database = create_database(f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    app = FastAPI()
    app.include_router(create_model_service_router(database, tmp_path))
    with TestClient(app) as client:
        yield client, database, tmp_path
    database.close()


def model_body(**changes):
    return {
        "name": "生成式模型",
        "purpose": "chat",
        "base_url": "http://localhost:9876/v1",
        "model": "test-chat",
    } | changes


def create(client, **changes):
    response = client.post(URL, json=model_body(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def update(client, item, **changes):
    response = client.put(
        f"{URL}/{item['id']}",
        json=model_body(row_version=item["row_version"], **changes),
    )
    assert response.status_code == 200, response.text
    return response.json()


def selection(root):
    path = root / "studio-model-settings.json"
    return json.loads(path.read_text(encoding="utf-8")).get("decision_compiler_model_id") if path.exists() else None


def selected_ids(client):
    response = client.get(URL)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"] if item["decision_compiler"]}


@pytest.mark.parametrize("value", [None, False, True])
def test_input_accepts_optional_top_level_compiler_flag(value):
    assert ModelServiceInput(**model_body()).decision_compiler is None
    body = ModelServiceInput(**model_body(decision_compiler=value))
    assert body.decision_compiler is value


def test_creating_models_does_not_implicitly_choose_compiler(compiler_center):
    client, _, root = compiler_center
    for purpose in ("chat", "embedding", "decision"):
        item = create(client, purpose=purpose, model=f"test-{purpose}")
        assert item["decision_compiler"] is False
        assert client.get(f"{URL}/{item['id']}").json()["decision_compiler"] is False
    assert selection(root) is None
    assert selected_ids(client) == set()


def test_create_selects_stable_id_and_survives_service_recreation(compiler_center):
    client, database, root = compiler_center
    selected = create(client, decision_compiler=True)
    assert selected["decision_compiler"] is True
    assert selection(root) == selected["id"]
    assert selected_ids(client) == {selected["id"]}
    reopened = ModelService(database, root)
    assert reopened.get_model(selected["id"])["decision_compiler"] is True
    assert reopened.list_models()["items"][0]["decision_compiler"] is True


def test_save_selects_existing_model_and_replaces_previous_selection(compiler_center):
    client, _, root = compiler_center
    first = create(client, name="第一个", decision_compiler=True)
    second = create(client, name="第二个")
    saved = update(client, second, name="第二个", decision_compiler=True)
    assert saved["decision_compiler"] is True
    assert selection(root) == second["id"]
    assert selected_ids(client) == {second["id"]}
    assert client.get(f"{URL}/{first['id']}").json()["decision_compiler"] is False


def test_create_with_selection_replaces_previous_selection(compiler_center):
    client, _, root = compiler_center
    first = create(client, decision_compiler=True)
    second = create(client, name="替代编译模型", decision_compiler=True)
    assert selection(root) == second["id"]
    assert selected_ids(client) == {second["id"]}
    assert client.get(f"{URL}/{first['id']}").json()["decision_compiler"] is False


@pytest.mark.parametrize("extra", [{}, {"decision_compiler": None}])
def test_omitted_or_null_flag_preserves_selection_and_identity_on_rename(compiler_center, extra):
    client, _, root = compiler_center
    selected = create(client, decision_compiler=True)
    updated = update(client, selected, name="改名后仍为编译模型", model="chat-updated", **extra)
    assert updated["id"] == selected["id"]
    assert updated["name"] == "改名后仍为编译模型"
    assert updated["config"]["chat"]["model"] == "chat-updated"
    assert updated["decision_compiler"] is True
    assert selection(root) == selected["id"]


def test_false_on_other_model_does_not_clear_selection(compiler_center):
    client, _, root = compiler_center
    selected = create(client, decision_compiler=True)
    other = create(client, name="未选模型", decision_compiler=False)
    assert selection(root) == selected["id"]
    update(client, other, name="未选模型改名", decision_compiler=False)
    assert selection(root) == selected["id"]
    assert selected_ids(client) == {selected["id"]}


def test_false_on_selected_model_clears_selection_without_fallback(compiler_center):
    client, _, root = compiler_center
    selected = create(client, decision_compiler=True)
    create(client, name="另一个可用模型")
    saved = update(client, selected, decision_compiler=False)
    assert saved["decision_compiler"] is False
    assert selection(root) is None
    assert selected_ids(client) == set()


@pytest.mark.parametrize("purpose", ["embedding", "decision"])
@pytest.mark.parametrize("flag", [True, False])
def test_non_chat_purpose_cannot_submit_compiler_selection(compiler_center, purpose, flag):
    client, _, root = compiler_center
    selected = create(client, decision_compiler=True)
    response = client.post(URL, json=model_body(purpose=purpose, decision_compiler=flag))
    assert response.status_code == 422, response.text
    other = create(client, purpose=purpose, model=f"test-{purpose}")
    response = client.put(f"{URL}/{other['id']}", json=model_body(
        purpose=purpose, model=f"test-{purpose}", row_version=other["row_version"], decision_compiler=flag,
    ))
    assert response.status_code == 422, response.text
    assert selection(root) == selected["id"]
    assert selected_ids(client) == {selected["id"]}


@pytest.mark.parametrize("changes, status", [
    ({"row_version": 0}, 409),
    ({"base_url": "http://localhost:9876/v1?api_key=invalid"}, 422),
])
def test_failed_save_does_not_change_compiler_selection(compiler_center, changes, status):
    client, _, root = compiler_center
    selected = create(client, decision_compiler=True)
    other = create(client, name="未选模型")
    body = model_body(row_version=other["row_version"], decision_compiler=True) | changes
    response = client.put(f"{URL}/{other['id']}", json=body)
    assert response.status_code == status, response.text
    assert selection(root) == selected["id"]
    assert selected_ids(client) == {selected["id"]}


def test_deletion_does_not_silently_select_another_model(compiler_center):
    client, _, root = compiler_center
    selected = create(client, decision_compiler=True)
    other = create(client, name="待删除的未选模型")
    survivor = create(client, name="保留的未选模型")
    assert client.delete(f"{URL}/{other['id']}").status_code == 204
    assert selection(root) == selected["id"]
    assert client.delete(f"{URL}/{selected['id']}").status_code == 204
    assert selection(root) == selected["id"]
    assert client.get(f"{URL}/{selected['id']}").status_code == 404
    assert selected_ids(client) == set()
    assert client.get(f"{URL}/{survivor['id']}").json()["decision_compiler"] is False


def test_selection_is_not_model_config_or_exported_resource_content(compiler_center):
    client, database, root = compiler_center
    selected = create(client, decision_compiler=True)
    config = selected["config"]
    assert "decision_compiler" not in json.dumps(config)
    exchange = ResourceExchangeService(database, asset_store=AssetStore(root))
    archive = exchange.export_resource("model", selected["id"], root / "model.zip")
    resources = read_resource_set(archive)
    record = resources.resources[0]
    assert record.definition == config
    with ZipFile(archive) as exported:
        assert "studio-model-settings.json" not in exported.namelist()
        serialized = "\n".join(exported.read(name).decode("utf-8") for name in exported.namelist())
    assert "decision_compiler" not in serialized
    assert selected["id"] not in serialized
    assert selection(root) == selected["id"]
