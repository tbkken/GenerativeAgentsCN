from __future__ import annotations

from generative_agents.services.catalog import SecretService
from generative_agents.services.model_probes import ModelProbeService


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._body


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return _Response({"data": [{"id": "resolved-test-model"}]})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/embeddings"):
            return _Response({"data": [{"embedding": [0.1, 0.2]}]})
        return _Response({"choices": [{"message": {"content": "OK"}}]})


def test_auto_model_probe_pins_resolved_model_into_the_same_draft(
    service, database, tmp_path
):
    created = service.create_experiment(name="Probe", source_type="BLANK")
    draft = service.get_draft(created["id"])
    session = _Session()
    probes = ModelProbeService(
        database,
        experiments=service,
        secrets=SecretService(database, var_dir=tmp_path / "var"),
        session=session,
    )

    chat = probes.probe(
        created["id"], "chat", expected_lock_version=draft["lock_version"]
    )
    embedding = probes.probe(
        created["id"], "embedding", expected_lock_version=chat["lock_version"]
    )

    saved = service.get_draft(created["id"])
    assert chat["resolved_model"] == embedding["resolved_model"] == "resolved-test-model"
    assert saved["definition"]["models"]["chat"]["resolved_model"] == "resolved-test-model"
    assert saved["definition"]["models"]["embedding"]["resolved_model"] == "resolved-test-model"
    assert [method for method, _url, _kwargs in session.calls] == [
        "GET", "POST", "GET", "POST"
    ]
