import copy

import pytest
from fastapi.testclient import TestClient

from generative_agents.adapters.web.app import create_studio_app
from generative_agents.ga_protocol.packages.definition import _experiment_definition, write_experiment_definition
from generative_agents.ga_protocol.packages.io import read_json, write_integrity_manifest
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from tests.test_portable_package_protocol import _experiment


def _experiment_with_crowds(package_root):
    root = _experiment(package_root)
    _, definition = _experiment_definition(root)
    world = definition['world']['definition']
    tile = world['tiles'][0]
    world['size'] = [1, 3]
    world['tiles'] = [{**copy.deepcopy(tile), 'coord': [index, 0]} for index in range(3)]
    world.pop('semantic_index', None)
    address = tile['address']
    definition['agents'] = [
        {'agent_key': key, 'name': key.title(), 'scratch': {'age': 30},
         'coord': [index, 0], 'spatial': {'address': {'initial_location': address},
             'tree': {address[0]: {address[1]: {address[2]: [address[3]]}}}}}
        for index, key in enumerate(('alice', 'bob', 'carol'))
    ]
    definition['crowds'] = [
        {'crowd_key': 'friends', 'name': 'Friends', 'description': 'Alice and Bob',
         'agent_keys': ['alice', 'bob']},
        {'crowd_key': 'colleagues', 'name': 'Colleagues', 'description': 'Bob and Carol',
         'agent_keys': ['bob', 'carol']},
    ]
    write_experiment_definition(root, definition)
    write_integrity_manifest(root)
    validate_experiment_directory(root)
    return root


@pytest.mark.parametrize('operation', ['rename', 'delete'])
def test_settings_patch_saves_agents_and_crowd_members_together(tmp_path, operation):
    var = tmp_path / 'var'
    root = _experiment_with_crowds(var / 'packages')
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'studio.db').as_posix(), var_dir=var)
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        identity = read_json(root / 'manifest.json')['experiment']['experiment_id']
        url = '/api/studio/experiments/' + identity
        before = client.get(url, params={'view': 'definition'}).json()
        definition = before['definition']
        sections = {key: copy.deepcopy(definition[key]) for key in ('agents', 'crowds')}
        if operation == 'rename':
            next(agent for agent in sections['agents'] if agent['agent_key'] == 'bob')['agent_key'] = 'robert'
            for crowd in sections['crowds']:
                crowd['agent_keys'] = ['robert' if key == 'bob' else key for key in crowd['agent_keys']]
        else:
            sections['agents'] = [agent for agent in sections['agents'] if agent['agent_key'] != 'bob']
            for crowd in sections['crowds']:
                crowd['agent_keys'] = [key for key in crowd['agent_keys'] if key != 'bob']

        saved = client.patch(url, json={'sections': sections,
                                       'expected_content_sha256': before['content_sha256']})
        assert saved.status_code == 200, saved.text
        persisted = client.get(url, params={'view': 'definition'}).json()
        for key in ('agents', 'crowds'):
            assert persisted['definition'][key] == sections[key]
        for key in set(definition) - {'agents', 'crowds'}:
            assert persisted['definition'][key] == definition[key]
        assert persisted['content_sha256'] != before['content_sha256']
        assert [crowd['agent_keys'] for crowd in persisted['definition']['crowds']] == (
            [['alice', 'robert'], ['robert', 'carol']] if operation == 'rename' else [['alice'], ['carol']]
        )
        validate_experiment_directory(root)


@pytest.mark.parametrize('incomplete_update', [True, False])
def test_settings_patch_rejects_missing_crowd_members_without_changing_package(tmp_path, incomplete_update):
    var = tmp_path / 'var'
    root = _experiment_with_crowds(var / 'packages')
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'studio.db').as_posix(), var_dir=var)
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        identity = read_json(root / 'manifest.json')['experiment']['experiment_id']
        url = '/api/studio/experiments/' + identity
        before = client.get(url, params={'view': 'definition'}).json()
        definition = before['definition']
        sections = {'agents': [agent for agent in definition['agents'] if agent['agent_key'] != 'bob']}
        if not incomplete_update:
            sections['crowds'] = copy.deepcopy(definition['crowds'])
            sections['crowds'][0]['agent_keys'] = ['alice', 'missing-agent']
        original_files = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        rejected = client.patch(url, json={'sections': sections,
                                          'expected_content_sha256': before['content_sha256']})
        assert rejected.status_code == 422, rejected.text
        assert 'dependencies missing' in rejected.text
        assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == original_files
        persisted = client.get(url, params={'view': 'definition'}).json()
        assert persisted['definition'] == definition
        assert persisted['content_sha256'] == before['content_sha256']
        validate_experiment_directory(root)


def test_settings_patch_preserves_package_content_and_rejects_invalid_updates(tmp_path):
    var = tmp_path / 'var'
    root = _experiment(var / 'packages')
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'studio.db').as_posix(), var_dir=var)
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        identity = read_json(root / 'manifest.json')['experiment']['experiment_id']
        url = '/api/studio/experiments/' + identity
        before = client.get(url, params={'view': 'definition'}).json()
        definition = before['definition']
        original_files = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        body = {'sections': {'simulation': {**definition['simulation'], 'max_steps': 7}},
                'expected_content_sha256': before['content_sha256']}
        saved = client.patch(url, json=body)
        assert saved.status_code == 200, saved.text
        updated = saved.json()
        assert updated['definition']['simulation']['max_steps'] == 7
        assert set(updated['definition']) == {'simulation'}
        persisted = client.get(url, params={'view': 'definition'}).json()['definition']
        for section in set(definition) - {'simulation'}:
            assert persisted[section] == definition[section]
        for relative, content in original_files.items():
            if relative.as_posix() not in {'runtime/assembly.json', 'integrity/sha256.json'}:
                assert (root / relative).read_bytes() == content
        assert client.patch(url, json=body).status_code == 409
        current_files = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        token = updated['definition_hash']
        invalid_models = {**definition['models'], 'chat': {**definition['models']['chat'], 'secret_ref': None}}
        for sections in ({'world': {}}, {'models': invalid_models}, {}):
            rejected = client.patch(url, json={'sections': sections, 'expected_content_sha256': token})
            assert rejected.status_code == 422, rejected.text
        assert client.patch(url, json={'sections': body['sections']}).status_code == 422
        assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == current_files
        validate_experiment_directory(root)
        client.post(url + '/seal').raise_for_status()
        assert client.patch(url, json={'sections': body['sections'], 'expected_content_sha256': token}).status_code == 422
