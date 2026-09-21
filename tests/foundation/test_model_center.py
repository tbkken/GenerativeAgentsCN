import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from generative_agents.ga_protocol.schemas.experiment import ModelsConfig
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_studio.storage.credentials import HostModelCredentials
from generative_agents.adapters.web.routes.models import create_model_service_router
from generative_agents.adapters.web.routes.resources import create_resource_router
from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import Base
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_runtime.skills.executor import SkillRuntime


@pytest.fixture
def center(tmp_path):
    database = create_database(f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / 'cache')
    registry.create(name='test-social', description='讨论项目进度')
    app = FastAPI()
    app.include_router(create_resource_router(database, registry))
    app.include_router(create_model_service_router(database, tmp_path))
    with TestClient(app) as client:
        yield client, HostModelCredentials(database, tmp_path), tmp_path
    database.close()


URL = '/api/studio/resources/model-services'


def create(client, purpose='chat', key='secret-test-123'):
    body = dict(name=f'{purpose} 测试', purpose=purpose, base_url='http://localhost:9876/v1', model=f'test-{purpose}', api_key=key)
    response = client.post(URL, json=body)
    assert response.status_code == 201, response.text
    assert key not in response.text if key else True
    return body, response.json()


def test_keys_are_encrypted_and_survive_reopen_rotation_and_deletion(center):
    client, credentials, root = center
    body, item = create(client)
    alias = item['config']['chat']['credential_env']
    assert credentials.resolve(alias) == body['api_key']
    assert body['api_key'] not in client.get(URL).text
    assert body['api_key'].encode() not in (root / 'studio.db').read_bytes()
    assert body['api_key'] not in credentials.path.read_text()
    # Empty password keeps the key; replacement preserves the old alias for copied experiments.
    body.update(row_version=item['row_version'], api_key=None)
    kept = client.put(f"{URL}/{item['id']}", json=body).json()
    assert kept['config']['chat']['credential_env'] == alias
    body.update(row_version=kept['row_version'], api_key='replacement-key')
    rotated = client.put(f"{URL}/{item['id']}", json=body).json()
    assert rotated['config']['chat']['credential_env'] != alias
    assert credentials.resolve(alias) == 'secret-test-123'
    assert client.delete(f"{URL}/{item['id']}").status_code == 204
    assert credentials.resolve(alias) == 'secret-test-123'


def test_chat_and_embedding_config_copy_and_environment_do_not_contain_secret_ids(center):
    client, credentials, root = center
    _, chat = create(client)
    _, embedding = create(client, 'embedding', 'embedding-test-key')
    models = ModelsConfig.model_validate({**chat['config'], **embedding['config']}).model_dump(mode='json')
    experiment = root / 'run' / 'experiment'
    atomic_write_json(experiment / 'manifest.json', {'entrypoints': {'models':'models/models.json'}})
    atomic_write_json(experiment / 'models/models.json', models)
    environment = credentials.run_environment(root / 'run')
    assert set(environment.values()) == {'secret-test-123', 'embedding-test-key'}
    serialized = json.dumps(models)
    assert 'secret-test-123' not in serialized and 'embedding-test-key' not in serialized
    assert all(key.startswith('GA_MODEL_') for key in environment)
    assert all(key not in os.environ for key in environment)


def test_trial_uses_selected_model_and_encrypted_key(center, monkeypatch):
    client, _, _ = center
    _, model = create(client)
    monkeypatch.setenv('GA_SKILL_LLM_MODEL', 'must-not-override-selection')
    def complete(runtime, messages, tools=None):
        assert runtime.api_key == 'secret-test-123'
        assert runtime.model == 'test-chat'
        return {'content':'项目进度已确认。'}
    monkeypatch.setattr(SkillRuntime, '_complete', complete)
    response = client.post('/api/studio/resources/skills/test-social/run', json={
        'input_text':'询问项目进度', 'model_preset_id':model['id']})
    assert response.status_code == 200, response.text
    assert '项目进度已确认' in response.json()['output_text']
    assert 'secret-test-123' not in response.text


@pytest.mark.parametrize('purpose', ['chat', 'embedding'])
@pytest.mark.parametrize('base_url', ['http://localhost:9876/v1', 'https://model.example/api/plan/v3'])
def test_connection_probe_performs_authenticated_request(center, monkeypatch, purpose, base_url):
    client, _, _ = center
    body, model = create(client, purpose)
    body.update(base_url=base_url, row_version=model['row_version'], api_key=None)
    saved = client.put(f"{URL}/{model['id']}", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()['config'][purpose]['base_url'] == base_url
    def handle(request):
        endpoint = '/chat/completions' if purpose == 'chat' else '/embeddings'
        assert str(request.url) == base_url + endpoint
        assert request.headers['Authorization'] == 'Bearer secret-test-123'
        assert json.loads(request.content)['model'] == f'test-{purpose}'
        data = {'choices':[{'message':{'content':'OK'}}]} if purpose == 'chat' else {'data':[{'embedding':[0.1,0.2]}]}
        return httpx.Response(200, json=data)
    original = httpx.Client
    monkeypatch.setattr('generative_agents.ga_studio.resources.models.httpx.Client', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    response = client.post(f"{URL}/{model['id']}/test/{purpose}")
    assert response.status_code == 200, response.text
    assert response.json()['ok'] is True


def test_stale_save_rejected_and_clear_key_is_explicit(center):
    client, _, _ = center
    body, item = create(client)
    body.update(row_version=0, api_key=None)
    assert client.put(f"{URL}/{item['id']}",json=body).status_code == 409
    body.update(row_version=item['row_version'], clear_api_key=True)
    cleared = client.put(f"{URL}/{item['id']}",json=body).json()
    assert cleared['config']['chat']['credential_env'] is None
    assert cleared['credential_configured']['chat'] is False
