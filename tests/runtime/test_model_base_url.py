"""Runtime must use the same provider API prefix as Studio and package schemas."""
import json

import pytest
import requests

from generative_agents.ga_runtime.models.gateway import VLLMLLMModel


@pytest.mark.parametrize("base_url,prefix", [
    ("https://model.example", "https://model.example/v1"),
    ("https://model.example/v1/", "https://model.example/v1"),
    ("https://model.example/api/plan/v3/", "https://model.example/api/plan/v3"),
])
def test_discovery_and_completions_preserve_provider_api_path(monkeypatch, base_url, prefix):
    calls = []

    def request(session, method, url, **kwargs):
        calls.append((method.upper(), url))
        response = requests.Response()
        response.status_code = 200
        response.url = url
        data = {"data": [{"id": "test-model"}]} if method.upper() == "GET" else {
            "choices": [{"message": {"role": "assistant", "content": "OK"}}],
        }
        response._content = json.dumps(data).encode()
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    model = VLLMLLMModel({"model": "auto", "base_url": base_url, "retry_attempts": 1})
    assert model.chat_completion([{"role": "user", "content": "Reply OK."}])["content"] == "OK"
    assert model.completion("Reply OK.") == "OK"
    assert calls == [
        ("GET", prefix + "/models"),
        ("POST", prefix + "/chat/completions"),
        ("POST", prefix + "/chat/completions"),
    ]
