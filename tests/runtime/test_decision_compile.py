"""Decision parameters are extracted once from the complete natural-language Skill."""
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_protocol.skills.metadata import skill_frontmatter
from generative_agents.adapters.web.routes.resources import create_resource_router
from generative_agents.adapters.web.routes.experiment_resources import create_experiment_resource_router
from generative_agents.ga_studio.catalog.packages import StudioPackageCatalogService
from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder, SkillSource
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import Base
from tests.test_portable_package_protocol import _definition


BODY = "# 判断出行\n\n根据情境判断是否应当出门。\n\n<!-- PROMPT:START -->\n检查天气。\n<!-- PROMPT:END -->\n\n下雨且没有伞时不要出门。"
MARKDOWN = "---\nname: decide-outing\ndescription: 判断出行\nllm_type: decision\n---\n" + BODY
CHAT = {"provider": "vllm", "base_url": "http://127.0.0.1:8888/v1", "model": "chat-test"}


class Extractor:
    def __init__(self, questions):
        self.questions, self.calls = questions, []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        response = {"content": json.dumps({"questions": self.questions}, ensure_ascii=False)}
        kwargs["response_validator"](response)
        return response


def test_compile_natural_language_once_and_preserve_complete_body():
    from generative_agents.ga_runtime.api import compile_skill_decision

    client = Extractor({"go_out": {"type": "noul"}})
    result = compile_skill_decision(MARKDOWN, config=CHAT, model_client=client)

    assert len(client.calls) == 1
    messages, options = client.calls[0]
    assert options["retry"] == 1
    assert json.loads(messages[-1]["content"])["skill_instructions"] == BODY
    assert result["decision_questions"] == {"go_out": {"type": "noul", "instructions": BODY}}
    assert result["decision_prompt_sha256"] == hashlib.sha256(BODY.encode("utf-8")).hexdigest()
    fields, offset = skill_frontmatter(result["markdown"])
    assert result["markdown"][offset:].strip() == BODY
    assert json.loads(fields["decision_questions"]) == result["decision_questions"]
    assert fields["decision_prompt_sha256"] == result["decision_prompt_sha256"]


@pytest.mark.parametrize("questions", [
    {"mode": {"type": "choice", "criteria": {"walk": "步行", "bus": "公交"}}},
    {"risk": {"type": "score", "criteria": ["低", "中", "高"]}},
])
def test_compile_extracts_choices_and_ordered_scoring_criteria(questions):
    from generative_agents.ga_runtime.api import compile_skill_decision

    client = Extractor(questions)
    result = compile_skill_decision(MARKDOWN, config=CHAT, model_client=client)
    assert result["decision_questions"] == {
        key: {**question, "instructions": BODY} for key, question in questions.items()}
    assert len(client.calls) == 1


@pytest.mark.parametrize("questions", [{}, {"bad": {"type": "choice"}},
    {"bad": {"type": "noul", "answer": True}},
    {"bad": {"type": "noul", "instructions": "模型不可以重写正文"}}])
def test_compile_rejects_invalid_extraction_without_retry_or_decision(questions):
    from generative_agents.ga_runtime.api import compile_skill_decision

    client = Extractor(questions)
    with pytest.raises(SkillTrialError) as caught:
        compile_skill_decision(MARKDOWN, config=CHAT, model_client=client)
    assert caught.value.code == "SKILL_DECISION_COMPILE_INVALID"
    assert len(client.calls) == 1


@pytest.mark.parametrize("kwargs, body", [
    ({"kind": "brain"}, BODY),
    ({"scripts": ["scripts/test.py"]}, BODY),
    ({}, BODY + "\n调用 $child-skill。"),
    ({}, BODY + "\n调用 @world-perceive。"),
])
def test_compile_rejects_nonleaf_before_calling_model(kwargs, body):
    from generative_agents.ga_runtime.api import compile_skill_decision

    client = Extractor({"ready": {"type": "noul"}})
    with pytest.raises(SkillTrialError) as caught:
        compile_skill_decision(MARKDOWN.replace(BODY, body), config=CHAT, model_client=client, **kwargs)
    assert caught.value.code == "SKILL_DECISION_COMPILE_INVALID"
    assert client.calls == []


def test_compile_transport_failure_redacts_key_and_does_not_retry(monkeypatch):
    from generative_agents.ga_runtime.api import compile_skill_decision
    from generative_agents.ga_runtime.models.gateway import LLMModel

    class BrokenModel(LLMModel):
        provider = "test"
        def setup(self, config):
            self.attempts = 0
        def _chat_completion(self, messages, return_type, **kwargs):
            self.attempts += 1
            raise ValueError("remote error secret-key")

    client = BrokenModel({**CHAT, "api_key": "secret-key", "retry_attempts": 5})
    with pytest.raises(SkillTrialError) as caught:
        compile_skill_decision(MARKDOWN, config=CHAT, api_key="secret-key", model_client=client)
    assert caught.value.code == "SKILL_DECISION_COMPILE_FAILED"
    assert "secret-key" not in str(caught.value)
    assert client.attempts == 1


@pytest.fixture
def author(tmp_path, monkeypatch):
    database = create_database(f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    registry.create(name="decide-outing", description="判断出行")
    resources = StudioResourceService(database)
    client = Extractor({"go_out": {"type": "noul"}})
    settings = []
    def create(config, **kwargs):
        settings.append(config)
        return client
    monkeypatch.setattr("generative_agents.ga_runtime.skills.decision_compile.create_llm_model", create)
    app = FastAPI()
    app.include_router(create_resource_router(database, registry))
    app.include_router(create_experiment_resource_router(database, tmp_path))
    with TestClient(app) as http:
        yield database, registry, resources, client, settings, http
    database.close()


def test_public_compile_uses_chat_and_only_save_persists_current_results(author, monkeypatch):
    database, registry, resources, model, settings, http = author
    monkeypatch.setenv("TEST_COMPILE_KEY", "private-compile-key")
    resources.create_model_preset(name="解析模型", preset_key="compile-chat",
        config={"chat": {**CHAT, "credential_env": "TEST_COMPILE_KEY", "retry_attempts": 5}})
    original = registry.author_detail("decide-outing")
    response = http.post("/api/studio/resources/skills/decide-outing/decision-compile", json={"markdown": MARKDOWN})
    assert response.status_code == 200, response.text
    compiled = response.json()
    assert compiled["model"] == "chat-test"
    assert "private-compile-key" not in response.text
    assert settings[0]["api_key"] == "private-compile-key"
    assert settings[0]["retry_attempts"] == 1
    assert registry.author_detail("decide-outing")["markdown"] == original["markdown"]
    assert len(model.calls) == 1
    saved = http.put("/api/studio/resources/skills/decide-outing", json={"markdown": compiled["markdown"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["decision_questions"] == compiled["decision_questions"]
    stale = http.put("/api/studio/resources/skills/decide-outing", json={"markdown": compiled["markdown"] + "\n新规则。"})
    assert stale.status_code == 422
    assert "重新解析" in stale.text
    assert registry.author_detail("decide-outing")["markdown"] == compiled["markdown"]
    assert len(model.calls) == 1


def test_public_compile_requires_chat_purpose_and_keeps_failure_unsaved(author):
    database, registry, resources, model, settings, http = author
    preset = resources.create_model_preset(name="决策模型", preset_key="compile-decision",
        config={"decision": {"provider": "unsloth", "base_url": "http://127.0.0.1:5003/v1", "model": "laya-multilingual"}})
    original = registry.author_detail("decide-outing")["markdown"]
    response = http.post("/api/studio/resources/skills/decide-outing/decision-compile",
        json={"markdown": MARKDOWN, "model_preset_id": preset["id"]})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SKILL_MODEL_UNAVAILABLE"
    assert registry.author_detail("decide-outing")["markdown"] == original
    assert model.calls == []


@pytest.mark.parametrize("with_scripts", [False, True])
def test_experiment_compile_uses_copied_chat_and_does_not_modify_package(author, tmp_path, with_scripts):
    database, registry, resources, model, settings, http = author
    public = resources.create_model_preset(name="公共模型", preset_key="public-chat", config={"chat": CHAT})
    definition = _definition()
    definition["models"]["chat"] = {**CHAT, "model": "copied-chat"}
    definition["models"]["decision"] = {"provider": "unsloth", "base_url": "http://127.0.0.1:5003/v1", "model": "laya-multilingual"}
    sources = []
    for name, kind, markdown in [("test-brain", "brain", "---\nname: test-brain\ndescription: 测试大脑\n---\n返回等待。"),
                                  ("decide-outing", "skill", MARKDOWN.replace("llm_type: decision\n", ""))]:
        source = tmp_path / "author" / name / "SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text(markdown, encoding="utf-8")
        if with_scripts and name == "decide-outing":
            script = source.parent / "scripts" / "old.py"
            script.parent.mkdir()
            script.write_text("def run(): return 'old'\n", encoding="utf-8")
        sources.append(SkillSource(name, kind, source))
    root = ExperimentPackageBuilder().build_directory(tmp_path / "experiment", definition=definition,
        skills=sources, brain_skill="test-brain")
    record = StudioPackageCatalogService(database).upsert(root)
    prefix = f"/api/studio/experiments/{record.package_id}/resources/skills/decide-outing"
    original = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    if with_scripts:
        blocked = http.post(prefix + "/decision-compile", json={"markdown": MARKDOWN})
        assert blocked.status_code == 422, blocked.text
        assert model.calls == []
    response = http.post(prefix + "/decision-compile", json={"markdown": MARKDOWN, "scripts": {}})
    assert response.status_code == 200, response.text
    assert response.json()["model"] == "copied-chat"
    assert settings[0]["model"] == "copied-chat"
    assert len(model.calls) == 1
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == original
    invalid = http.post(prefix + "/decision-compile", json={"markdown": MARKDOWN, "model_preset_id": public["id"]})
    assert invalid.status_code == 422
    assert len(model.calls) == 1
    current = http.get(prefix).json()
    saved = http.put(prefix, json={"markdown": response.json()["markdown"], "scripts": {}, "row_version": current["row_version"]})
    assert saved.status_code == 200, saved.text
    stale = http.put(prefix, json={"markdown": response.json()["markdown"] + "\n新的规则。", "scripts": {},
                                 "row_version": saved.json()["row_version"]})
    assert stale.status_code == 422, stale.text
    assert len(model.calls) == 1


def test_public_compile_uses_draft_script_removal_and_preserves_saved_scripts(author):
    database, registry, resources, model, settings, http = author
    resources.create_model_preset(name="解析模型", preset_key="compile-chat", config={"chat": CHAT})
    skill = registry.get("decide-outing")
    registry.save(skill.name, skill.markdown, scripts={"scripts/old.py": "def run(): return 'old'\n"})
    prefix = "/api/studio/resources/skills/decide-outing"
    blocked = http.post(prefix + "/decision-compile", json={"markdown": MARKDOWN})
    assert blocked.status_code == 422
    assert model.calls == []
    for invalid in (["scripts/old.py"], {"scripts/old.py": 42}):
        response = http.post(prefix + "/decision-compile", json={"markdown": MARKDOWN, "scripts": invalid})
        assert response.status_code == 422
    compiled = http.post(prefix + "/decision-compile", json={"markdown": MARKDOWN, "scripts": {}})
    assert compiled.status_code == 200, compiled.text
    assert len(model.calls) == 1
    assert registry.script_sources(skill.name) == {"scripts/old.py": "def run(): return 'old'\n"}
    saved = http.put(prefix, json={"markdown": compiled.json()["markdown"], "scripts": {}})
    assert saved.status_code == 200, saved.text
    assert registry.script_sources(skill.name) == {}
