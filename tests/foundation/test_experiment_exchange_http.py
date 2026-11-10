"""Experiment and complete Run packages survive transfer between installations."""
from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from generative_agents.adapters.web.app import create_studio_app
from generative_agents.ga_protocol.packages.io import (
    atomic_write_json, open_package, read_json, seal_directory, write_integrity_manifest,
)
from generative_agents.ga_runtime.lifecycle.service import RunService
from tests.test_portable_package_protocol import _experiment


@contextmanager
def installation(root):
    root.mkdir(parents=True, exist_ok=True)
    app = create_studio_app(
        database_url='sqlite:///' + (root / 'studio.db').as_posix(),
        var_dir=root / 'var',
    )
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        yield client


def experiment_in(root):
    experiment = _experiment(root / 'var/packages')
    identity = read_json(experiment / 'manifest.json')['experiment']['experiment_id']
    return experiment, identity


def upload(client, content, filename='renamed.zip'):
    return client.post('/api/studio/experiment-import', files={'file': (filename, content, 'application/zip')})


def package_files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*')
            if p.is_file() and not p.name.endswith('.lock')}


def assert_empty(client):
    assert client.get('/api/studio/experiments').json()['total'] == 0
    assert client.get('/api/studio/packages?kind=run').json()['total'] == 0


def test_complete_history_round_trip_preserves_ids_results_and_source_files(tmp_path):
    source = tmp_path / 'source'
    experiment, identity = experiment_in(source)
    runs = []
    for number in (1, 2):
        run = source / f'var/packages/runs/run-{number}'
        status = RunService().start(experiment, run, requested_steps=number)
        assert status.status.value == 'COMPLETED'
        atomic_write_json(run / 'artifacts' / 'transfer-evidence.json', {'run': status.run_id, 'step': number})
        runs.append((run, status.run_id, number))
    experiment_before = package_files(experiment)
    runs_before = {run_id: package_files(run) for run, run_id, _ in runs}
    url = f'/api/studio/experiments/{identity}'
    with installation(source) as client:
        source_results = {}
        for _, run_id, _ in runs:
            response = client.get(f'/api/studio/runs/{run_id}/replay/steps?include_audit=true')
            response.raise_for_status()
            source_results[run_id] = response.json()
        response = client.get(url + '/export')
        response.raise_for_status()
        assert response.headers['content-type'] == 'application/zip'
        assert response.headers['cache-control'] == 'no-store'
        assert '-resources.zip' in response.headers['content-disposition']
        bundle = response.content
        # Downloading must not seal the original draft, move the Run, or change facts.
        assert client.get(url).json()['status'] == 'DRAFT'
        assert package_files(experiment) == experiment_before
        for run, run_id, _ in runs:
            assert package_files(run) == runs_before[run_id]
    with ZipFile(BytesIO(bundle)) as package:
        assert set(package.namelist()) == {
            'experiment.gaexp', 'integrity/sha256.json',
            *(f'runs/{run_id}.garun' for _, run_id, _ in runs),
        }
        for _, run_id, steps in runs:
            with ZipFile(BytesIO(package.read(f'runs/{run_id}.garun'))) as run_package:
                names = set(run_package.namelist())
                assert f'frames/step-{steps:06d}.json.gz' in names
                assert f'commits/step-{steps:06d}.json' in names
                assert f'checkpoints/step-{steps:06d}/bundle.json' in names
                assert 'artifacts/transfer-evidence.json' in names
                assert 'experiment/skills/items/test-brain/SKILL.md' in names
                assert run_package.read('run.json') == runs_before[run_id]['run.json']
    destination = tmp_path / 'destination'
    with installation(destination) as client:
        assert_empty(client)
        response = upload(client, bundle)
        response.raise_for_status()
        result = response.json()
        assert result['experiment_id'] == identity
        assert set(result['run_ids']) == {run_id for _, run_id, _ in runs}
        assert result['imported_count'] == 3
        assert result['existing_count'] == 0
        assert client.get(url).json()['status'] == 'SEALED'
        assert client.get(url + '/runs').json()['total'] == 2
        for _, run_id, steps in runs:
            summary = client.get(f'/api/studio/runs/{run_id}').json()
            assert summary['status'] == 'COMPLETED'
            assert summary['completed_steps'] == steps
            response = client.get(f'/api/studio/runs/{run_id}/replay/steps?include_audit=true')
            response.raise_for_status()
            assert response.json() == source_results[run_id]
        existing = upload(client, bundle, 'another-name.zip')
        existing.raise_for_status()
        assert existing.json()['imported_count'] == 0
        assert existing.json()['existing_count'] == 3
        # Package files, rather than the database cache, remain sufficient.
        client.post('/api/studio/packages/rebuild').raise_for_status()
        assert client.get(url + '/runs').json()['total'] == 2


def test_experiment_only_export_keeps_draft_and_restores_sealed_package(tmp_path):
    source = tmp_path / 'source'
    experiment, identity = experiment_in(source)
    before = package_files(experiment)
    with installation(source) as client:
        response = client.get(f'/api/studio/experiments/{identity}/export?include_runs=false')
        response.raise_for_status()
        assert '.gaexp' in response.headers['content-disposition']
        assert client.get(f'/api/studio/experiments/{identity}').json()['status'] == 'DRAFT'
        assert package_files(experiment) == before
    with installation(tmp_path / 'destination') as client:
        imported = upload(client, response.content, 'custom-name.gaexp')
        imported.raise_for_status()
        assert imported.json()['experiment_id'] == identity
        assert imported.json()['run_ids'] == []
        detail = client.get(f'/api/studio/experiments/{identity}').json()
        assert detail['status'] == 'SEALED'
        assert detail['editable'] is False
        assert client.post(f'/api/studio/experiments/{identity}/validate').json()['valid']


def test_standalone_run_restores_its_embedded_experiment_and_replay(tmp_path):
    source = tmp_path / 'source'
    experiment, identity = experiment_in(source)
    run = source / 'run'
    status = RunService().start(experiment, run, requested_steps=2)
    archive = RunService().seal(run, tmp_path / 'anything.garun')
    with installation(tmp_path / 'destination') as client:
        response = upload(client, archive.read_bytes(), archive.name)
        response.raise_for_status()
        assert response.json()['experiment_id'] == identity
        assert response.json()['run_ids'] == [status.run_id]
        assert response.json()['imported_count'] == 2
        summary = client.get(f'/api/studio/runs/{status.run_id}/replay').json()
        assert summary['committed_step'] == 2
        assert summary['status'] == 'COMPLETED'
        assert client.get(f'/api/studio/experiments/{identity}').json()['status'] == 'SEALED'


@pytest.mark.parametrize('state', ['QUEUED', 'RUNNING', 'FINALIZING'])
def test_active_run_cannot_enter_transfer_but_experiment_can(tmp_path, monkeypatch, state):
    monkeypatch.setattr('generative_agents.adapters.web.context.WebContext.reconcile_local_runs', lambda self: None)
    source = tmp_path / 'source'
    experiment, identity = experiment_in(source)
    run = RunService().create(experiment, source / 'var/packages/runs/active')
    status = read_json(run / 'status.json')
    status['status'] = state
    atomic_write_json(run / 'status.json', status)
    before = package_files(run)
    with installation(source) as client:
        response = client.get(f'/api/studio/experiments/{identity}/export')
        assert response.status_code == 409
        assert '暂停' in response.json()['detail']
        assert package_files(run) == before
        client.get(f'/api/studio/experiments/{identity}/export?include_runs=false').raise_for_status()


def test_corrupted_nested_run_is_rejected_before_any_package_is_published(tmp_path):
    source = tmp_path / 'source'
    experiment, identity = experiment_in(source)
    run = source / 'var/packages/runs/completed'
    status = RunService().start(experiment, run, requested_steps=1)
    with installation(source) as client:
        response = client.get(f'/api/studio/experiments/{identity}/export')
        response.raise_for_status()
    transfer = tmp_path / 'transfer.zip'
    transfer.write_bytes(response.content)
    with open_package(transfer) as collection:
        # Re-sign the outer transfer to prove nested archive validation is required.
        (collection / 'runs' / f'{status.run_id}.garun').write_bytes(b'not a ZIP')
        write_integrity_manifest(collection)
        corrupted = seal_directory(collection, tmp_path / 'corrupted.zip')
    with installation(tmp_path / 'destination') as client:
        response = upload(client, corrupted.read_bytes())
        assert response.status_code == 422
        assert_empty(client)
        client.post('/api/studio/packages/rebuild').raise_for_status()
        assert_empty(client)


def test_existing_experiment_identity_with_different_content_never_overwrites(tmp_path):
    source = tmp_path / 'source'
    experiment, identity = experiment_in(source)
    original = seal_directory(experiment, tmp_path / 'original.gaexp')
    with open_package(original) as package:
        manifest = read_json(package / 'manifest.json')
        manifest['experiment']['name'] = 'Conflicting experiment name'
        atomic_write_json(package / 'manifest.json', manifest)
        write_integrity_manifest(package)
        changed = seal_directory(package, tmp_path / 'changed.gaexp')
    with installation(tmp_path / 'destination') as client:
        upload(client, original.read_bytes(), original.name).raise_for_status()
        url = f'/api/studio/experiments/{identity}'
        before = client.get(url).json()
        response = upload(client, changed.read_bytes(), changed.name)
        assert response.status_code == 409
        after = client.get(url).json()
        assert after['name'] == before['name']
        assert after['content_sha256'] == before['content_sha256']
        assert client.get('/api/studio/experiments').json()['total'] == 1


@pytest.mark.parametrize('change_artifact', [False, True], ids=['identical-directory-run', 'changed-artifact'])
def test_reimport_to_source_keeps_writable_paths_and_never_overwrites_artifacts(tmp_path, change_artifact):
    source = tmp_path / 'source'
    experiment, identity = experiment_in(source)
    runs = []
    for number in (1, 2):
        run = source / f'var/packages/runs/original-{number}'
        status = RunService().start(experiment, run, requested_steps=number)
        assert status.status.value == 'COMPLETED'
        atomic_write_json(run / 'artifacts/transfer-evidence.json', {'generation': 'original'})
        runs.append((run, status.run_id))
    with installation(source) as client:
        response = client.get(f'/api/studio/experiments/{identity}/export')
        response.raise_for_status()
        exported = response.content
        if change_artifact:
            atomic_write_json(runs[-1][0] / 'artifacts/transfer-evidence.json', {'generation': 'newer'})
        before_files = {root: package_files(root) for root in [experiment, *(run for run, _ in runs)]}
        catalog_urls = [f'/api/studio/packages/experiment/{identity}',
                        *(f'/api/studio/packages/run/{run_id}' for _, run_id in runs)]
        before_catalog = {url: client.get(url).json() for url in catalog_urls}
        imported = upload(client, exported)
        if change_artifact:
            assert imported.status_code == 409
        else:
            imported.raise_for_status()
            assert imported.json()['imported_count'] == 0
            assert imported.json()['existing_count'] == 3
            assert set(imported.json()['run_ids']) == {run_id for _, run_id in runs}
        assert client.get(f'/api/studio/experiments/{identity}').json()['status'] == 'DRAFT'
        assert {url: client.get(url).json() for url in catalog_urls} == before_catalog
        assert {root: package_files(root) for root in before_files} == before_files
        assert all(root.is_dir() for root in before_files)
        client.post('/api/studio/packages/rebuild').raise_for_status()
        assert client.get('/api/studio/experiments').json()['total'] == 1
        assert client.get(f'/api/studio/experiments/{identity}/runs').json()['total'] == 2
