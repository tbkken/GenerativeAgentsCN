from pathlib import Path
from types import SimpleNamespace

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
    monkeypatch.setattr('generative_agents.ga_runtime.skills.trial.create_llm_model',
        lambda *args, **kwargs: SimpleNamespace(chat_completion=lambda *args, **kwargs: {
            'content': '交流项目进度。'}))
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
INPUT = {'input_text': '林晨与赵悦交流项目进度'}


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
    assert result['input_text'] == INPUT['input_text']
    assert [event['event'] for event in result['trace']] == ['skill.start', 'skill.result', 'trial.complete']
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
    assert 'API Key' in detail['message']
    assert detail['trace'][0]['event'] == 'skill.start'


def test_studio_trial_preserves_input_whitespace(trial, monkeypatch):
    client, resources, *_ = trial
    configure(resources)
    raw_input = '  林晨说：\n“这件事还未确认。”\n  '
    def complete(runtime, messages, tools=None):
        assert messages[-1]['content'] == raw_input
        return {'content': '收到原文。'}
    monkeypatch.setattr(SkillRuntime, '_complete', complete)
    response = client.post(URL, json={'input_text': raw_input})
    assert response.status_code == 200, response.text
    assert response.json()['input_text'] == raw_input


def test_studio_trial_diagnoses_missing_world_context(trial):
    client, resources, registry, skill = trial
    configure(resources)
    registry.save(skill.name, skill.markdown + '\n调用 `@world-perceive`。')
    response = client.post(URL, json=INPUT)
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'SKILL_ITERATION_CONTEXT_REQUIRED'


def test_studio_trial_checks_interfaces_in_the_full_child_closure(trial, monkeypatch):
    client, resources, registry, skill = trial
    configure(resources)
    leaf = registry.create(name='read-private-memory', description='读取当前参与者记忆')
    registry.save(leaf.name, leaf.markdown + '\n调用 @memory-stream-search。')
    child = registry.create(name='review-context', description='组合上下文', kind='skill')
    registry.save(child.name, child.markdown + '\n使用 $read-private-memory。')
    registry.save(skill.name, skill.markdown + '\n使用 $review-context。')
    monkeypatch.setattr(SkillRuntime, '_complete', lambda *args, **kwargs: pytest.fail('must reject before model call'))
    response = client.post(URL, json=INPUT)
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'SKILL_ITERATION_CONTEXT_REQUIRED'
    assert 'memory-stream-search' in response.json()['detail']['message']


@pytest.mark.parametrize('body, code', [
    ('调用 @unknown-interface。', 'SKILL_INTERFACE_UNKNOWN'),
    ('使用 $missing-child。', 'SKILL_DEPENDENCY_MISSING'),
    ('使用 $social-interaction-decision。', 'SKILL_DEPENDENCY_CYCLE'),
])
def test_studio_trial_rejects_invalid_dependency_closure(trial, body, code):
    client, resources, registry, skill = trial
    configure(resources)
    registry.save(skill.name, skill.markdown + '\n' + body)
    response = client.post(URL, json=INPUT)
    assert response.status_code == 422
    assert response.json()['detail']['code'] == code


def test_studio_trial_rejects_invalid_input_and_missing_skill(trial):
    client, *_ = trial
    assert client.post(URL, json={'input_text': ''}).status_code == 422
    response = client.post(URL.replace('social-interaction-decision', 'missing-skill'), json=INPUT)
    assert response.status_code == 404
    assert response.json()['detail'] != 'Not Found'


def test_studio_trial_does_not_accept_user_runtime_identity(trial):
    client, *_ = trial
    response = client.post(URL, json={**INPUT, 'context': {'agent_key': 'someone-else'}})
    assert response.status_code == 422


def test_studio_trial_defaults_to_first_available_model_not_reserved_key(trial, monkeypatch):
    client, resources, *_ = trial
    configure(resources)
    first = resources.create_model_preset(name='新生成式模型', preset_key='first-model', config={
        'chat': {'base_url': 'http://127.0.0.1:9999/v1', 'model': 'first-model'}})
    monkeypatch.setattr(SkillRuntime, '_complete', lambda self, *args, **kwargs: {'content': self.model})
    response = client.post(URL, json=INPUT)
    assert response.status_code == 200, response.text
    assert response.json()['model'] == 'first-model'
