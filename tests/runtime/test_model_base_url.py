"""Runtime must use the same provider API prefix as Studio and package schemas."""
import pytest

from generative_agents.ga_runtime.models.gateway import VLLMLLMModel


@pytest.mark.parametrize("base_url,prefix", [
    ("https://model.example", "https://model.example/v1"),
    ("https://model.example/v1/", "https://model.example/v1"),
    ("https://model.example/api/plan/v3/", "https://model.example/api/plan/v3"),
])
def test_discovery_and_completions_preserve_provider_api_path(monkeypatch, base_url, prefix):
    calls = []

    def request(model, method, url, **kwargs):
        calls.append((method.upper(), url))
        data = {"data": [{"id": "test-model"}]} if method.upper() == "GET" else {
            "choices": [{"message": {"role": "assistant", "content": "OK"}}],
        }
        return data

    monkeypatch.setattr(VLLMLLMModel, "_request_json", request)
    model = VLLMLLMModel({"model": "auto", "base_url": base_url, "retry_attempts": 1})
    assert model.chat_completion([{"role": "user", "content": "Reply OK."}])["content"] == "OK"
    assert model.completion("Reply OK.") == "OK"
    assert calls == [
        ("GET", prefix + "/models"),
        ("POST", prefix + "/chat/completions"),
        ("POST", prefix + "/chat/completions"),
    ]
