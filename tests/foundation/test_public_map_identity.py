"""User-selected map identities must remain unique in the author workspace."""
from fastapi.testclient import TestClient

from tests.studio_support import create_test_studio


def test_api_accepts_unique_human_selected_public_map_key(database_url):
    app = create_test_studio(database_url=database_url)
    with TestClient(app) as client:
        created = client.post('/api/studio/resources/maps',
                              json={'map_key': 'my-workspace', 'name': '用户地图'})
        duplicate = client.post('/api/studio/resources/maps',
                                json={'map_key': 'my-workspace', 'name': '重复地图'})
    assert created.status_code == 201
    assert created.json()['map_key'] == 'my-workspace'
    assert duplicate.status_code == 409
    assert duplicate.json()['detail']['code'] == 'MAP_KEY_CONFLICT'
