"""Compiler transport belongs to the host; author model CRUD cannot alter it."""
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from generative_agents.adapters.web.routes.models import create_model_service_router
from generative_agents.ga_protocol.packages.resources import read_resource_set
from generative_agents.ga_studio.resources.exchange import ResourceExchangeService
from generative_agents.ga_studio.storage.assets import AssetStore
from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import Base


URL = "/api/studio/resources/model-services"


@pytest.fixture
def center(tmp_path):
    database = create_database(f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    app = FastAPI()
    app.include_router(create_model_service_router(database, tmp_path, model_urls={}))
    with TestClient(app) as http:
        yield http, database
    database.close()


def test_author_model_crud_cannot_read_or_set_the_compiler(center, tmp_path):
    http, database = center
    legacy = tmp_path / "studio-model-settings.json"
    legacy.write_bytes(b"not a valid JSON file")
    body = dict(name="author model", purpose="chat", base_url="https://author.example/v2", model="ordinary-model")
    denied = http.post(URL, json={**body, "decision_compiler": True})
    assert denied.status_code == 422 and http.get(URL).json()["items"] == []
    created = http.post(URL, json=body)
    assert created.status_code == 201, created.text
    model = created.json()
    detail = f"{URL}/{model['id']}"
    assert "decision_compiler" not in model
    assert "decision_compiler" not in http.get(URL).json()["items"][0]
    for value in (True, False, None):
        denied = http.put(detail, json={**body, "row_version": model["row_version"], "decision_compiler": value})
        assert denied.status_code == 422
    assert http.get(detail).json()["row_version"] == model["row_version"]
    exchange = ResourceExchangeService(database, asset_store=AssetStore(tmp_path))
    archive = exchange.export_resource("model", model["id"], tmp_path / "model.zip")
    assert read_resource_set(archive).resources[0].definition == model["config"]
    with ZipFile(archive) as exported:
        assert all("decision_compiler" not in exported.read(name).decode("utf-8") for name in exported.namelist())
        assert legacy.name not in exported.namelist()
    assert http.delete(detail).status_code == 204
    assert legacy.read_bytes() == b"not a valid JSON file"
