"""List work is bounded by a page; preview and conditional reads avoid large content."""
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event

from generative_agents.adapters.web.routes.authoring import install_routes
from generative_agents.ga_studio.resources.assets import AssetService
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.resources.exchange import ResourceExchangeService
from generative_agents.ga_studio.resources.maps import WorldMapService
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.resources.spatial_assets import SpatialAssetService
from generative_agents.ga_studio.storage.models import StudioSkill, WorldMap
from tests.foundation.test_resource_exchange import agent, png


@contextmanager
def statements(database):
    queries = []
    def collect(_connection, _cursor, statement, _parameters, _context, _many):
        queries.append(statement)
    event.listen(database.engine, "before_cursor_execute", collect)
    try:
        yield queries
    finally:
        event.remove(database.engine, "before_cursor_execute", collect)


def forbidden(*args, **kwargs):
    raise AssertionError("list/preview attempted to load detail content")


def test_agent_and_crowd_lists_page_in_sql_and_detail_members_are_batched(database, tmp_path, monkeypatch):
    assets = AssetService(database, var_dir=tmp_path)
    service = StudioResourceService(database)
    people = [agent(database, assets.store, key=f"person-{n}", images=False) for n in range(7)]
    crowd = service.create_crowd(name="Group", agent_ids=[item["id"] for item in people])
    monkeypatch.setattr(service, "_agent", forbidden)
    with statements(database) as sql:
        page = service.list_summaries("agent", query="person", page=2, page_size=3)
    assert page["total"] == 7 and page["total_pages"] == 3 and len(page["items"]) == 3
    assert all("definition" not in item for item in page["items"])
    assert len(sql) == 2 and "LIMIT" in sql[-1] and "OFFSET" in sql[-1]
    assert ", studio_agents.definition_json," not in sql[-1]
    assert service.list_summaries("crowd")["items"][0]["member_count"] == 7
    with statements(database) as sql:
        detail = service.get_crowd(crowd["id"])
    assert len(detail["members"]) == 7
    assert sum("FROM studio_agents" in query for query in sql) == 1


def test_skill_list_never_materializes_or_selects_markdown_and_scripts(database, tmp_path, monkeypatch):
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    with database.session_factory.begin() as session:
        for number, kind in enumerate(("atomic", "pack", "brain", "atomic")):
            session.add(StudioSkill(skill_key=f"skill-{number}", kind=kind, description="description",
                markdown="x" * 100_000, scripts_json={"scripts/large.py": "x" * 100_000}, content_hash=str(number) * 64))
    monkeypatch.setattr(registry, "_document", forbidden)
    with statements(database) as sql:
        result = registry.list_summaries(kind="skill", page=2, page_size=2)
    assert result["total"] == 3 and len(result["items"]) == 1
    assert result["counts"] == {"atomic": 2, "pack": 1, "brain": 1}
    assert len(sql) == 3 and not any("markdown" in query or "scripts_json" in query for query in sql)
    assert list((tmp_path / "cache").iterdir()) == []


def test_map_and_spatial_lists_do_not_hydrate_or_scan_usage(database, monkeypatch):
    maps = WorldMapService(database, skill_registry=object())
    with database.session_factory.begin() as session:
        session.add(WorldMap(map_key="heavy-map", name="Heavy", world_hash="0" * 64,
            world_json={"definition": {"size": [20, 30], "tile_size": 32, "tiles": ["x" * 10_000]}}))
    monkeypatch.setattr(maps, "_map_detail", forbidden)
    result = maps.list_maps()
    assert result["items"][0]["dimensions"] == [20, 30]
    assert "world" not in result["items"][0]
    spatial = SpatialAssetService(database)
    spatial.ensure_builtin_assets()
    monkeypatch.setattr(spatial, "_detail", forbidden)
    monkeypatch.setattr(spatial, "ensure_builtin_assets", forbidden)
    with statements(database) as sql:
        result = spatial.list_assets(page_size=2)
    assert len(result["items"]) == 2 and result["total"] > 2
    assert len(sql) == 2 and not any("world_maps" in query or "contract_json" in query for query in sql)


def test_image_304_only_reads_metadata_and_thumbnail_preserves_original(database, tmp_path, monkeypatch):
    assets = AssetService(database, var_dir=tmp_path)
    original = png(512, 256)
    stored = assets.upload(BytesIO(original), logical_name="portrait.png", media_type="image/png")
    router, app = APIRouter(prefix="/api/studio"), FastAPI()
    install_routes(router, SimpleNamespace(asset_service=assets))
    app.include_router(router)
    path = f"/api/studio/resources/assets/{stored['asset_id']}/content"
    with TestClient(app) as client:
        with monkeypatch.context() as patch, statements(database) as sql:
            patch.setattr(assets, "delivery_content", forbidden)
            response = client.get(path, headers={"If-None-Match": f'W/"{stored["sha256"]}"'})
        assert response.status_code == 304 and len(sql) == 1
        assert "content_blob IS NOT NULL" in sql[0] and "assets.content_blob," not in sql[0]
        thumbnail = client.get(path + "?width=96")
        assert thumbnail.status_code == 200 and thumbnail.headers["content-type"] == "image/webp"
        with Image.open(BytesIO(thumbnail.content)) as image:
            assert image.size == (96, 48)
        assert client.get(path).content == original
        assert client.get(path + "?width=97").status_code == 422


def test_export_plan_does_not_read_images_and_catalog_is_bounded(database, tmp_path, monkeypatch):
    assets = AssetService(database, var_dir=tmp_path)
    person = agent(database, assets.store)
    service = ResourceExchangeService(database, asset_store=assets.store)
    monkeypatch.setattr(service, "_asset_bytes", forbidden)
    preview = service.preview_export("agent", person["id"])
    assert preview["resources"][0]["attachment_count"] == 2
    with statements(database) as sql:
        result = service.catalog("agent", page_size=1)
    assert result["total"] == 1 and len(result["items"]) == 1
    assert not any("definition_json" in query or "content_blob" in query for query in sql)


def test_model_lists_filter_purpose_without_decrypting_credentials(database, tmp_path, monkeypatch):
    from generative_agents.ga_studio.resources.models import ModelService
    service = ModelService(database, tmp_path)
    service.resources.create_model_preset(name="Chat", config={"chat": {"model": "chat", "base_url": "http://model", "credential_env": "MODEL_SECRET"}})
    service.resources.create_model_preset(name="Embedding", config={"embedding": {"model": "embed", "base_url": "http://model"}})
    monkeypatch.setenv("MODEL_SECRET", "private")
    monkeypatch.setattr(service.credentials, "resolve", forbidden)
    with statements(database) as sql:
        result = service.list_models(purpose="chat")
    assert result["total"] == 1 and "config" not in result["items"][0]
    assert result["items"][0]["purposes"][0]["credential_configured"] is True
    assert len(sql) == 2 and "secrets.encrypted_value" not in " ".join(sql)


def test_experiment_summary_reuses_parsed_index_and_never_loads_skill_or_image_bytes(database, tmp_path, monkeypatch):
    from generative_agents.ga_studio.catalog.packages import StudioPackageCatalogService
    from generative_agents.ga_studio.experiments.workspace import ExperimentWorkspaceService
    from generative_agents.ga_studio.experiments.editor import ExperimentResourceEditor
    from generative_agents.ga_protocol.packages import definition
    from tests.test_portable_package_protocol import _experiment
    root = _experiment(tmp_path)
    record = StudioPackageCatalogService(database).upsert(root)
    workspace = ExperimentWorkspaceService(database, package_root=tmp_path / "packages", var_dir=tmp_path)
    editor = ExperimentResourceEditor(workspace)
    first = editor.read(record.package_id, "skills", {})
    original = definition.read_package_json
    def metadata_only(path, relative):
        assert relative != "resources/index.json", "same package reparsed its full resource index"
        assert not relative.startswith("assets/")
        return original(path, relative)
    monkeypatch.setattr(definition, "read_package_json", metadata_only)
    monkeypatch.setattr(editor, "skill", forbidden)
    monkeypatch.setattr(editor, "registry", forbidden)
    monkeypatch.setattr("generative_agents.ga_studio.experiments.editor._experiment_definition", forbidden)
    monkeypatch.setattr("generative_agents.ga_studio.experiments.editor.read_experiment_resource_set", forbidden)
    assert editor.read(record.package_id, "skills", {}) == first
    maps = editor.read(record.package_id, "maps", {})
    assert "world" not in maps["items"][0]
    size = list(maps["items"][0]["dimensions"])
    maps["items"][0]["dimensions"][0] = 1000
    assert editor.read(record.package_id, "maps", {})["items"][0]["dimensions"] == size
    assert all("definition" not in item for item in editor.read(record.package_id, "agents", {})["items"])
