from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from generative_agents.adapters.web.routes.resources import create_resource_router
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import Base
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_runtime.skills.executor import SkillModelError
from generative_agents.ga_runtime.skills.executor import SkillRuntime


@pytest.fixture
def trial(tmp_path, monkeypatch):
    for key in ('GA_SKILL_LLM_BASE_URL', 'GA_SKILL_LLM_MODEL', 'GA_SKILL_LLM_API_KEY', 'GA_CHAT_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    database = create_database(f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / 'cache')
    skill = registry.create(name='social-interaction-decision', description='交流项目进度')
    app = FastAPI()
    app.include_router(create_resource_router(database, registry))
    with TestClient(app) as client:
        yield client, StudioResourceService(database), registry, skill
    database.close()


def configure(resources):
    resources.create_model_preset(name='试运行模型', preset_key='local-default', config={
        'chat': {'base_url': 'http://127.0.0.1:9999/v1', 'model': 'trial-model'},
    })


URL = '/api/studio/resources/skills/social-interaction-decision/run'
INPUT = {'input_text': '林晨与赵悦交流项目进度', 'context': {'virtual_time': '2026-09-05T10:00:00+08:00'}}


def test_studio_trial_returns_output_and_trace_from_physical_copy(trial, monkeypatch):
    client, resources, registry, skill = trial
    configure(resources)
    paths = []
    def complete(runtime, messages, tools=None):
        assert isinstance(runtime.registry, SkillRegistry)
        path = runtime.registry.get(skill.name).path
        assert path.is_file()
        paths.append(path)
        assert runtime.model == 'trial-model'
        assert INPUT['input_text'] in messages[-1]['content']
        return {'content': '赵悦：原型已完成，明天一起核对测试结果。'}
    monkeypatch.setattr(SkillRuntime, '_complete', complete)
    response = client.post(URL, json=INPUT)
    assert response.status_code == 200, response.text
    result = response.json()
    assert '原型已完成' in result['output_text']
    assert result['model'] == 'trial-model'
    assert [event['event'] for event in result['trace']] == ['skill.start', 'skill.result']
    assert all(not path.exists() for path in paths)


def test_studio_trial_diagnoses_missing_model(trial):
    client, *_ = trial
    response = client.post(URL, json=INPUT)
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'SKILL_MODEL_CONFIG_MISSING'


def test_studio_trial_preserves_trace_and_explains_authentication(trial, monkeypatch):
    client, resources, *_ = trial
    configure(resources)
    def fail(*args, **kwargs):
        raise SkillModelError('401 Not authenticated')
    monkeypatch.setattr(SkillRuntime, '_complete', fail)
    response = client.post(URL, json=INPUT)
    assert response.status_code == 502
    detail = response.json()['detail']
    assert detail['code'] == 'SKILL_MODEL_AUTH_FAILED'
    assert 'GA_SKILL_LLM_API_KEY' in detail['message']
    assert detail['trace'][0]['event'] == 'skill.start'


def test_studio_trial_diagnoses_missing_world_context(trial):
    client, resources, registry, skill = trial
    configure(resources)
    registry.save(skill.name, skill.markdown + '\n调用 `world-perceive`。')
    response = client.post(URL, json=INPUT)
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'SKILL_ITERATION_CONTEXT_REQUIRED'


def test_studio_trial_rejects_invalid_input_and_missing_skill(trial):
    client, *_ = trial
    assert client.post(URL, json={'input_text': ''}).status_code == 422
    response = client.post(URL.replace('social-interaction-decision', 'missing-skill'), json=INPUT)
    assert response.status_code == 404
    assert response.json()['detail'] != 'Not Found'
