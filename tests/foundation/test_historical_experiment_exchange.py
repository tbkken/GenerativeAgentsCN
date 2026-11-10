"""Archived v2 evidence is portable even when author contracts have changed."""
from __future__ import annotations

import base64
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

import pytest

from generative_agents.ga_protocol.packages.io import (
    PackageError, atomic_write_json, copy_package_tree, open_package, read_json,
    seal_directory, write_integrity_manifest,
)
from generative_agents.ga_protocol.packages.resources import read_resource_index, resource_content_hash
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory, validate_run_directory
from generative_agents.ga_protocol.schemas.experiment import WorldConfig
from generative_agents.ga_replay.reader import ReplayReader
from generative_agents.ga_runtime.lifecycle.executor import RuntimeModelRegistry
from generative_agents.ga_runtime.lifecycle.service import RunService
from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder, SkillSource
from tests.foundation.test_experiment_exchange_http import assert_empty, installation, package_files, upload
from tests.runtime.test_object_skill_runtime import ToolModel
from tests.test_portable_package_protocol import _definition


RULES = '历史交流约定：保存原始证据，不按新版本重新解释。'
ASSET_PATH = 'assets/maps/historical.png'
ASSET_BYTES = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
)


class HistoricalWorldConfig(WorldConfig):
    """Test-only author contract used to calculate the fixture's original digest."""

    rules: str = ''


@pytest.fixture
def historical(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    author = source / 'author'
    skill = author / 'brain/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: test-brain\ndescription: 归档交流证据\n---\n'
                     '# Brain\n调用 @world-act 发言，成功后结束本轮。\n', encoding='utf-8')
    template = skill.parent / 'templates/message.txt'
    template.parent.mkdir()
    template.write_text('保存每条已提交发言的原始身份。\n', encoding='utf-8')
    definition = _definition()
    definition['world'] = {
        'world_key': 'historical-chat', 'world_name': '历史群聊',
        'definition': {'environment_type': 'social_1d', 'presentation': 'chat', 'description': '历史讨论背景'},
        'assets': [{'logical_path': ASSET_PATH, 'asset_hash': 'sha256:' + sha256(ASSET_BYTES).hexdigest(),
                    'media_type': 'image/png', 'size': len(ASSET_BYTES)}],
    }
    definition['agents'] = [{'agent_key': key, 'name': key.upper(), 'space_id': 'historical-chat',
                             'scratch': {'age': 30}} for key in ('aa', 'bb')]
    experiment = ExperimentPackageBuilder().build_directory(
        author / 'experiment', definition=definition,
        skills=[SkillSource('test-brain', 'brain', skill)], brain_skill='test-brain',
        asset_sources={ASSET_PATH: ASSET_BYTES},
    )
    run = source / 'var/packages/runs/historical'

    def speak(messages, _kwargs):
        context = json.loads(messages[1]['content'].split('output schema):\n', 1)[1])['IterationContext']
        return 'world-act', {'action_type': 'SPEAK',
                             'message': f"历史发言 {context['agent']['agent_key']} 第 {context['step']['number']} 轮"}

    with monkeypatch.context() as runtime:
        runtime.setattr(RuntimeModelRegistry, 'get', lambda *_: ToolModel(speak))
        status = RunService().start(experiment, run, requested_steps=2)
    assert status.status.value == 'COMPLETED'
    with ReplayReader(run) as replay:
        frames = list(replay.iter_steps())
    assert sum(len(item['messages']) for frame in frames for item in frame['conversations']) == 4

    # Create a historical fixture, not a migration: both original experiment
    # copies and their recorded identity are changed only inside this test.
    index_path = experiment / 'resources/index.json'
    index = read_json(index_path)
    record = next(item for item in index['resources'] if item['kind'] == 'map')
    record['definition']['rules'] = RULES
    atomic_write_json(index_path, index)
    with monkeypatch.context() as historical_schema:
        historical_schema.setattr('generative_agents.ga_protocol.packages.resources.WorldConfig', HistoricalWorldConfig)
        resources = read_resource_index(experiment, 'resources/index.json')
        map_record = next(item for item in resources.resources if item.kind == 'map')
        historical_digest = resource_content_hash(map_record, resources)
    for reference in index['roots']:
        if reference['kind'] == 'map':
            reference['expected_sha256'] = historical_digest
    atomic_write_json(index_path, index)
    assembly = read_json(experiment / 'runtime/assembly.json')
    assembly['map']['expected_sha256'] = historical_digest
    atomic_write_json(experiment / 'runtime/assembly.json', assembly)
    experiment_hash = write_integrity_manifest(experiment)['root_sha256']
    for relative in ('resources/index.json', 'runtime/assembly.json', 'integrity/sha256.json'):
        (run / 'experiment' / relative).write_bytes((experiment / relative).read_bytes())
    manifest = read_json(run / 'run.json')
    manifest['experiment']['root_sha256'] = experiment_hash
    atomic_write_json(run / 'run.json', manifest)
    experiment_id = manifest['experiment']['experiment_id']
    archive = seal_directory(experiment, source / 'var/packages/experiments' / f'{experiment_id}.gaexp')
    return {'source': source, 'experiment': experiment, 'archive': archive, 'experiment_id': experiment_id,
            'run': run, 'run_id': status.run_id, 'frames': frames, 'content_sha256': historical_digest}


def test_historical_experiment_and_run_export_import_preserve_exact_evidence(historical, tmp_path):
    case = historical
    experiment_bytes = case['archive'].read_bytes()
    run_before = package_files(case['run'])
    with installation(case['source']) as client:
        response = client.get(f"/api/studio/experiments/{case['experiment_id']}/export")
        response.raise_for_status()
        transfer = response.content
        experiment_only = client.get(f"/api/studio/experiments/{case['experiment_id']}/export?include_runs=false")
        experiment_only.raise_for_status()
        assert experiment_only.content == experiment_bytes
        assert case['archive'].read_bytes() == experiment_bytes
        assert package_files(case['run']) == run_before
        repeated = upload(client, transfer)
        repeated.raise_for_status()
        assert repeated.json()['imported_count'] == 0
        assert repeated.json()['existing_count'] == 2
        assert package_files(case['run']) == run_before
    with ZipFile(BytesIO(transfer)) as bundle:
        assert bundle.read('experiment.gaexp') == experiment_bytes
        run_bytes = bundle.read(f"runs/{case['run_id']}.garun")
        with ZipFile(BytesIO(run_bytes)) as archive:
            assert {name: archive.read(name) for name in run_before} == run_before
            assert archive.read('experiment/' + ASSET_PATH) == ASSET_BYTES
            assert RULES.encode() in archive.read('experiment/resources/index.json')
            assert case['content_sha256'].encode() in archive.read('experiment/runtime/assembly.json')
            assert archive.read('experiment/skills/items/test-brain/templates/message.txt')
    with installation(tmp_path / 'destination') as client:
        response = upload(client, transfer)
        response.raise_for_status()
        assert response.json()['experiment_id'] == case['experiment_id']
        assert response.json()['run_ids'] == [case['run_id']]
        assert response.json()['imported_count'] == 2
        repeated = upload(client, transfer)
        repeated.raise_for_status()
        assert repeated.json()['existing_count'] == 2
        location = client.get(f"/api/studio/packages/run/{case['run_id']}").json()['catalog']['location']
        with ReplayReader(location) as replay:
            assert list(replay.iter_steps()) == case['frames']
            assert replay.experiment_world()['rules'] == RULES
            assert replay.asset(ASSET_PATH)[0] == ASSET_BYTES
        response = client.get(f"/api/studio/runs/{case['run_id']}/results/conversations")
        response.raise_for_status()
        assert '历史发言' in response.text
        assert client.get(f"/api/studio/runs/{case['run_id']}/replay/steps?include_audit=true").json()['available_step'] == 2
        client.post('/api/studio/packages/rebuild').raise_for_status()
        assert client.get(f"/api/studio/experiments/{case['experiment_id']}/runs").json()['total'] == 1


@pytest.mark.parametrize('kind', ['experiment', 'run'])
def test_historical_standalone_packages_import_without_rewriting(historical, tmp_path, kind):
    case = historical
    archive = case['archive'] if kind == 'experiment' else RunService().seal(case['run'], tmp_path / 'historical.garun')
    original = archive.read_bytes()
    with installation(tmp_path / 'standalone') as client:
        response = upload(client, original, archive.name)
        response.raise_for_status()
        expected_kind = 'experiment' if kind == 'experiment' else 'run'
        identity = case['experiment_id'] if kind == 'experiment' else case['run_id']
        downloaded = client.get(f'/api/studio/packages/{expected_kind}/{identity}/download')
        downloaded.raise_for_status()
        assert downloaded.content == original
        assert client.get(f"/api/studio/experiments/{case['experiment_id']}").json()['status'] == 'SEALED'
        assert response.json()['imported_count'] == (1 if kind == 'experiment' else 2)


def test_historical_rules_remain_invalid_for_new_execution(historical, tmp_path):
    case = historical
    with pytest.raises(PackageError, match='rules'):
        validate_experiment_directory(case['experiment'])
    with pytest.raises(PackageError, match='rules'):
        validate_run_directory(case['run'])
    with pytest.raises(PackageError, match='rules'):
        RunService().create(case['archive'], tmp_path / 'new-run')
    assert not (tmp_path / 'new-run').exists()
    before = package_files(case['run'])
    with pytest.raises(PackageError, match='rules'):
        RunService().resume(case['run'])
    assert package_files(case['run']) == before


def test_historical_shape_does_not_bypass_current_draft_export_validation(historical, tmp_path):
    case = historical
    source = tmp_path / 'draft-source'
    draft = source / 'var/packages/experiments/draft'
    copy_package_tree(case['experiment'], draft)
    before = package_files(draft)
    with installation(source) as client:
        response = client.get(f"/api/studio/experiments/{case['experiment_id']}/export?include_runs=false")
        assert response.status_code == 422
        assert 'rules' in response.json()['detail']
        assert package_files(draft) == before


@pytest.mark.parametrize('damage', ['integrity', 'frame', 'recovery'])
def test_historical_import_still_rejects_corrupted_evidence(historical, tmp_path, damage):
    case = historical
    original = RunService().seal(case['run'], tmp_path / 'valid.garun')
    with open_package(original) as package:
        if damage == 'integrity':
            (package / 'experiment' / ASSET_PATH).write_bytes(b'changed asset bytes')
        elif damage == 'frame':
            (package / 'frames/step-000001.json.gz').write_bytes(b'changed frame bytes')
            write_integrity_manifest(package)
        else:
            for folder in ('checkpoints', 'recovery'):
                snapshot = package / folder / 'step-000002/state.json'
                if snapshot.exists():
                    snapshot.write_bytes(b'changed checkpoint bytes')
            write_integrity_manifest(package)
        corrupted = seal_directory(package, tmp_path / f'{damage}.garun')
    with installation(tmp_path / 'destination') as client:
        response = upload(client, corrupted.read_bytes(), corrupted.name)
        assert response.status_code == 422
        assert_empty(client)
        client.post('/api/studio/packages/rebuild').raise_for_status()
        assert_empty(client)
