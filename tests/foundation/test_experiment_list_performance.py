"""Listing cost is bounded by the visible page, never by historical Run frames."""
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_replay.reader import read_run_status
from generative_agents.ga_runtime.lifecycle.service import RunService
from generative_agents.adapters.web.app import create_studio_app
from tests.test_portable_package_protocol import _experiment


def forbidden(*args, **kwargs):
    raise AssertionError('list attempted to read quality, frames or recovery data')


def test_filter_and_pagination_happen_before_any_package_read(tmp_path, monkeypatch):
    import generative_agents.adapters.web.context as api
    import generative_agents.ga_replay.reader as reader
    import generative_agents.ga_protocol.facts.recovery as recovery

    var = tmp_path / 'var'
    roots = [_experiment(var / f'packages/source-{index}') for index in range(7)]
    for index, root in enumerate(roots):
        RunService().create(root, var / f'packages/runs/run-{index}')
    app = create_studio_app(database_url=f"sqlite:///{(tmp_path / 'studio.db').as_posix()}", var_dir=var)
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        catalog = client.get('/api/studio/packages?kind=experiment').json()['items']
        catalog.sort(key=lambda item: (item['updated_at'], item['experiment_id']), reverse=True)
        hidden_id = catalog[0]['experiment_id']
        atomic_write_json(var / 'studio-presentation.json', {
            'schema_version': 1, 'experiments': {hidden_id: {'archived_at': '2026-09-13T00:00:00Z'}},
        })
        # Hidden resources may be unreadable without affecting this page.
        expected = catalog[1:6]
        Path(catalog[0]['location'], 'manifest.json').write_text('invalid', encoding='utf-8')
        Path(catalog[6]['location'], 'manifest.json').write_text('invalid', encoding='utf-8')
        definitions, runs = [], []
        read_definition, read_status = api._experiment_definition, api.read_run_status

        def tracked_definition(root, **options):
            definitions.append(str(root))
            assert options.get('summary_only') is True
            return read_definition(root, **options)

        def tracked_status(root):
            runs.append(str(root))
            return read_status(root)

        monkeypatch.setattr(api, '_experiment_definition', tracked_definition)
        monkeypatch.setattr(api, 'read_run_status', tracked_status)
        monkeypatch.setattr(api, 'read_run_overview', forbidden)
        monkeypatch.setattr(reader, 'read_run_quality', forbidden)
        monkeypatch.setattr(recovery, 'boundary_snapshot', forbidden)
        response = client.get('/api/studio/experiments?page=1')
        response.raise_for_status()
        page = response.json()
        assert page['total'] == 6 and page['total_pages'] == 2
        assert {item['id'] for item in page['items']} == {item['experiment_id'] for item in expected}
        assert set(definitions) == {item['location'] for item in expected}
        assert len(definitions) == len(runs) == 5
        assert all('quality' not in item['latest_run'] and 'recoverable' not in item['latest_run'] for item in page['items'])
        definitions.clear(); runs.clear()
        empty = client.get('/api/studio/experiments?page=20').json()
        assert empty['items'] == [] and empty['total'] == 6
        assert definitions == runs == []


def test_live_list_status_reads_no_frames_and_archive_is_not_extracted(tmp_path, monkeypatch):
    import generative_agents.ga_replay.reader as reader
    root = RunService().create(_experiment(tmp_path / 'packages'), tmp_path / 'run')
    status = read_json(root / 'status.json')
    # The navigation reader can show a committed boundary even while its full
    # recovery/quality material is unavailable; those checks belong to detail.
    status.update(status='FINALIZING', committed_step=3)
    atomic_write_json(root / 'status.json', status)
    monkeypatch.setattr(reader, 'open_package', forbidden)
    monkeypatch.setattr(reader, 'read_run_quality', forbidden)
    monkeypatch.setattr(reader, '_run_summary', forbidden)
    manifest, observed = read_run_status(root)
    assert observed.status.value == 'FINALIZING' and observed.committed_step == 3
    status['status'] = 'COMPLETED'
    atomic_write_json(root / 'status.json', status)
    archive = tmp_path / 'renamed.garun'
    with zipfile.ZipFile(archive, 'w') as target:
        for relative in ('run.json', 'status.json', 'experiment/manifest.json'):
            target.write(root / relative, relative)
    zipped_manifest, zipped_status = read_run_status(archive)
    assert zipped_manifest.run_id == manifest.run_id
    assert zipped_status.status.value == 'COMPLETED'
    status['run_id'] = str(uuid4())
    atomic_write_json(root / 'status.json', status)
    with pytest.raises(PackageError, match='another Run'):
        read_run_status(root)


def test_list_summary_does_not_open_model_skill_or_evaluation_documents(tmp_path, monkeypatch):
    import generative_agents.ga_protocol.packages.definition as api
    root = _experiment(tmp_path / 'packages')
    original = api.read_json
    seen = []

    def tracked(path):
        seen.append(Path(path).relative_to(root).as_posix())
        return original(path)

    monkeypatch.setattr(api, 'read_json', tracked)
    manifest, summary = api._experiment_definition(root, summary_only=True)
    assert set(seen) == {'manifest.json', *(manifest['entrypoints'][key] for key in ('world', 'simulation', 'agents'))}
    assert set(summary) == {'world', 'agents', 'simulation'}
