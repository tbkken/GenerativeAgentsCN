"""User Agent and Crowd lifecycle through current author-resource endpoints."""
from fastapi.testclient import TestClient
from tests.studio_support import create_test_studio


def test_user_agent_and_crowd_can_be_archived_restored_and_deleted(database_url):
    with TestClient(create_test_studio(database_url=database_url)) as client:
        prefix = "/api/studio/resources"
        assert client.get(prefix + "/agents").json()["items"] == []
        definition = {"agent_key": "reader", "name": "Reader", "scratch": {
            "age": 30, "innate": "calm", "learned": "careful", "lifestyle": "regular", "daily_plan": "read"}}
        response = client.post(prefix + "/agents", json={"definition": definition})
        assert response.status_code == 201, response.text
        agent = response.json()
        response = client.post(prefix + "/crowds", json={"name": "Readers", "agent_ids": [agent["id"]]})
        assert response.status_code == 201, response.text
        crowd = response.json()
        for kind, resource in (("crowds", crowd), ("agents", agent)):
            url = f"{prefix}/{kind}/{resource['id']}"
            assert "is_builtin" not in resource and "revision_id" not in resource
            assert client.post(url + "/archive").status_code == 200
            assert not client.get(prefix + "/" + kind).json()["items"]
            assert client.post(url + "/restore").status_code == 200
            assert client.get(url).json()["id"] == resource["id"]
            assert client.delete(url).status_code == 204
            assert client.get(url).status_code == 404
