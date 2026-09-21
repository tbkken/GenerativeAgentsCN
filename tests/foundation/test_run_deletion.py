from pathlib import Path
import json
import pytest
from fastapi.testclient import TestClient
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_runtime.lifecycle.service import RunService
from generative_agents.adapters.web.app import create_studio_app
from tests.test_portable_package_protocol import _experiment


@pytest.fixture
def scenario(tmp_path):
    var = tmp_path / 'var'
    run = RunService().create(_experiment(var / 'packages'), var / 'packages/runs/run')
    status = read_json(run / 'status.json')
    status['status'] = 'FAILED'
    atomic_write_json(run / 'status.json', status)
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'studio.db').as_posix(), var_dir=var)
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        yield client, var, run, '/api/studio/runs/' + status['run_id']


def contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_delete_recycles_complete_run_and_is_idempotent(scenario):
    client, var, run, url = scenario
    before = contents(run)
    assert client.delete(url).status_code == 204
    assert not run.exists()
    payload = next((var / 'run-recycle-bin').glob('*/payload'))
    assert contents(payload) == before
    assert client.get(url).status_code == 404
    assert client.delete(url).status_code == 204
    # Catalog rebuild must not resurrect recycled packages.
    client.post('/api/studio/packages/rebuild').raise_for_status()
    assert client.get(url).status_code == 404


def test_occupied_directory_fails_without_partial_deletion(scenario, monkeypatch):
    client, var, run, url = scenario
    before = contents(run)
    import generative_agents.ga_studio.catalog.deletion as deletion
    rename = deletion.os.rename
    def blocked(source, target):
        if Path(source) == run:
            error = PermissionError('occupied'); error.winerror = 32; raise error
        return rename(source, target)
    monkeypatch.setattr(deletion.os, 'rename', blocked)
    monkeypatch.setattr(deletion.time, 'sleep', lambda _: None)
    response = client.delete(url)
    assert response.status_code == 409 and '占用' in response.json()['detail']
    assert contents(run) == before
    assert client.get(url).status_code == 200


def test_damaged_run_remains_selectable_and_deletable(scenario):
    client, var, run, url = scenario
    (run / 'experiment/manifest.json').unlink()
    document = client.get(url)
    assert document.status_code == 200 and document.json()['package_error']
    assert client.get(url + '/results/timeline').status_code == 409
    before = contents(run)
    assert client.delete(url).status_code == 204
    assert contents(next((var / 'run-recycle-bin').glob('*/payload'))) == before


def test_index_failure_after_move_can_be_retried(scenario, monkeypatch):
    client, var, run, url = scenario
    from generative_agents.ga_studio.catalog.packages import StudioPackageCatalogService
    before = contents(run)
    with monkeypatch.context() as patch:
        patch.setattr(StudioPackageCatalogService, 'delete', lambda *_: (_ for _ in ()).throw(RuntimeError('database unavailable')))
        assert client.delete(url).status_code == 409
        assert not run.exists()
        assert client.get(url).status_code == 200
    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404
    assert contents(next((var / 'run-recycle-bin').glob('*/payload'))) == before


def test_generic_package_delete_cannot_bypass_active_run_guard(scenario):
    client, var, run, url = scenario
    status = read_json(run / 'status.json'); status['status'] = 'RUNNING'
    atomic_write_json(run / 'status.json', status)
    before = contents(run)
    assert client.delete('/api/studio/packages/run/' + status['run_id']).status_code == 409
    assert contents(run) == before


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows reader handle')
def test_real_open_file_never_causes_partial_tree_deletion(scenario):
    client, var, run, url = scenario
    held_path = run / 'logs/held.log'
    held_path.parent.mkdir(exist_ok=True)
    with held_path.open('wb') as held:
        held.write(b'reader holds this file'); held.flush()
        before = contents(run)
        response = client.delete(url)
        assert response.status_code in {204, 409}
        if response.status_code == 409:
            assert contents(run) == before
        else:
            assert contents(next((var / 'run-recycle-bin').glob('*/payload'))) == before
    assert client.delete(url).status_code == 204
    assert contents(next((var / 'run-recycle-bin').glob('*/payload'))) == before
