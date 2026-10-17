"""Studio trial selection, physical isolation and current Skill provenance."""
from pathlib import Path
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from generative_agents.adapters.web.routes.resources import create_resource_router
from generative_agents.adapters.web.routes.experiment_resources import create_experiment_resource_router
from generative_agents.ga_protocol.packages.io import atomic_write_json, read_json, seal_directory
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_studio.catalog.packages import StudioPackageCatalogService
from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder, SkillSource
from generative_agents.ga_studio.resources.maps import normalize_public_world
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.resources.trials import prepare_skill_trial
from tests.foundation.test_navigation import navigation_world
from tests.test_portable_package_protocol import _definition


@pytest.fixture
def scene(database, tmp_path):
    definition = _definition()
    definition['world'] = normalize_public_world(navigation_world()).model_dump(mode='json')
    definition['agents'] = [
        {'agent_key': key, 'name': name, 'coord': coord, 'scratch': {'age': 25},
         'spatial': {'address': {'initial_location': ['Campus', 'Sector', 'Room', 'Floor']},
                     'tree': {'Campus': {'Sector': {'Room': ['Floor']}}}}, 'enabled': enabled}
        for key, name, coord, enabled in [('alice', '小艾', [1, 2], True), ('bob', '小博', [2, 2], False)]]
    source = tmp_path / 'author' / 'SKILL.md'
    source.parent.mkdir()
    source.write_text('---\nname: test-brain\ndescription: 旧实验大脑\n---\n旧实验技能内容。', encoding='utf-8')
    root = ExperimentPackageBuilder().build_directory(tmp_path / 'scene', definition=definition,
        skills=[SkillSource('test-brain', 'brain', source)], brain_skill='test-brain')
    catalog = StudioPackageCatalogService(database)
    record = catalog.upsert(root)
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / 'cache')
    current = registry.create(name='test-brain', description='当前编辑大脑', kind='brain')
    registry.save(current.name, current.markdown + '\n当前页面的技能。调用 @world-perceive。')
    app = FastAPI()
    app.include_router(create_resource_router(database, registry))
    app.include_router(create_experiment_resource_router(database, tmp_path))
    with TestClient(app) as client:
        yield client, registry, catalog, root, record.package_id


def file_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_trial_choices_are_lightweight_and_only_offer_enabled_scene_agents(scene, tmp_path):
    client, _, _, _, identity = scene
    url = '/api/studio/resources/skill-trial/experiments'
    choices = client.get(url).json()['items']
    assert choices == [{'id': identity, 'experiment_id': identity, 'name': 'Portable Test',
                        'status': 'DRAFT', 'agent_count': 1}]
    agents = client.get(f'{url}/{identity}/agents').json()
    assert agents['items'] == [{'key': 'alice', 'agent_key': 'alice', 'name': '小艾'}]
    atomic_write_json(tmp_path / 'studio-presentation.json', {'experiments': {identity: {'archived_at': '2026-10-15T10:00:00+08:00'}}})
    assert client.get(url).json()['items'] == []
    assert client.get(f'{url}/{identity}/agents').status_code == 404


@pytest.mark.parametrize('agent_key, code', [(None, 'SKILL_AGENT_REQUIRED'), ('unknown', 'SKILL_AGENT_UNAVAILABLE'), ('bob', 'SKILL_AGENT_UNAVAILABLE')])
def test_trial_rejects_missing_foreign_or_disabled_actor_before_model(scene, monkeypatch, agent_key, code):
    client, _, _, _, identity = scene
    monkeypatch.setattr('generative_agents.ga_runtime.skills.trial.create_llm_model', lambda *a, **k: pytest.fail('model must not run'))
    response = client.post('/api/studio/resources/skills/test-brain/run', json={
        'input_text': '描述当前情景', 'experiment_id': identity, 'agent_key': agent_key})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == code


@pytest.mark.parametrize('sealed', [False, True])
def test_trial_scene_and_current_skill_are_distinct_physical_copies(scene, database, tmp_path, sealed):
    _, registry, catalog, original, identity = scene
    before = file_bytes(original)
    current = registry.get('test-brain').markdown
    if sealed:
        catalog.upsert(seal_directory(original, tmp_path / 'scene.gaexp'))
    destination = tmp_path / 'isolated-trial'
    credentials = prepare_skill_trial(database, registry, 'test-brain', '描述当前情景',
        destination=destination, experiment_id=identity, agent_key='alice')
    request = read_json(destination / 'trial.json')
    assert 'context' not in request
    assert request['experiment_directory'] == 'experiment' and request['agent_key'] == 'alice'
    assert request['model'] == 'local-test'
    copied = SkillRegistry(destination / 'skills').get('test-brain')
    assert copied.markdown == current and '当前页面的技能' in copied.body
    assert file_bytes(destination / 'experiment') == before
    assert file_bytes(original) == before
    assert registry.get('test-brain').markdown == current
    assert len(catalog.list(package_kind='experiment')) == 1
    assert credentials == {'GA_SKILL_TRIAL_KEY': ''}


def test_public_trial_executes_selected_actor_and_current_skill_only(scene, monkeypatch):
    client, _, _, original, identity = scene
    before = file_bytes(original)
    calls = []
    class Model:
        def chat_completion(self, messages, **kwargs):
            calls.append((messages, kwargs))
            assert kwargs['purpose'] == 'skill_runtime'
            assert '当前页面的技能' in messages[0]['content']
            if len(calls) == 1:
                return {'content': '', 'tool_calls': [{'id': 'perceive', 'type': 'function',
                    'function': {'name': 'world-perceive', 'arguments': '{}'}}]}
            result = json.loads(messages[-1]['content'])
            assert result['agent']['agent_key'] == 'alice'
            return {'content': '小艾正在当前实验场景中。'}
    monkeypatch.setattr('generative_agents.ga_runtime.skills.trial.create_llm_model', lambda *a, **k: Model())
    response = client.post('/api/studio/resources/skills/test-brain/run', json={
        'input_text': '描述当前情景', 'experiment_id': identity, 'agent_key': 'alice'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['output_text'] == '小艾正在当前实验场景中。'
    assert result['environment']['experiment_id'] == identity
    assert result['world_commit'] == 'NOT_COMMITTED'
    assert file_bytes(original) == before
    assert len(calls) == 2


def test_experiment_trial_rejects_user_context_and_foreign_scene(scene):
    client, _, _, _, identity = scene
    url = f'/api/studio/experiments/{identity}/resources/skills/test-brain/run'
    response = client.post(url, json={'input_text': '描述当前情景', 'context': {'agent_key': 'forged'}})
    assert response.status_code == 422 and '系统自动生成' in response.text
    response = client.post(url, json={'input_text': '描述当前情景', 'experiment_id': 'another-experiment', 'agent_key': 'alice'})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'SKILL_EXPERIMENT_UNAVAILABLE'
