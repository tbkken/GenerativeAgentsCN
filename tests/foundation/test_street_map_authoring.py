"""An existing hand-drawn map stays browsable without being offered for new experiments."""
from fastapi.testclient import TestClient

from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import WorldMap
from tests.studio_support import create_test_studio


def test_only_project_owned_maps_use_the_local_library_and_selector(database_url):
    with TestClient(create_test_studio(database_url=database_url)) as client:
        base = "/api/studio/resources/maps"
        rejected = client.post(base, json={"name": "空白街区", "environment_type": "spatial_3d"})
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "PREBUILT_SCENE_REQUIRED"
        tile = client.post(base, json={"name": "平面地图", "environment_type": "spatial_2d"}).json()
        chat = client.post(base, json={"name": "讨论", "environment_type": "social_1d"}).json()
        db = create_database(database_url)
        try:
            with db.session_factory.begin() as session:
                session.add(WorldMap(map_key="old-street", name="旧手绘街区", description="既有记录",
                    world_json={"world_key": "old-street", "world_name": "旧手绘街区",
                                "definition": {"environment_type": "spatial_3d", "roads": [], "instances": [], "anchors": []}},
                    world_hash="0" * 64))
        finally:
            db.close()
        catalog = client.get(base).json()["items"]
        assert not any(row["map_key"] == "old-street" for row in catalog)
        pages = [client.get(base, params={"for_experiment": True, "page_size": 1, "page": n}).json() for n in (1, 2)]
        assert all(page["total"] == 2 and page["total_pages"] == 2 for page in pages)
        assert {page["items"][0]["id"] for page in pages} == {tile["id"], chat["id"]}
