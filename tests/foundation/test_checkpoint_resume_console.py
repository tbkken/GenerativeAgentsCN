from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from generative_agents.ga_protocol import read_json, atomic_write_json, write_integrity_manifest
from generative_agents.ga_runtime.service import RunService
from generative_agents.ga_runtime.executor import _StatusCommitter
from generative_agents.ga_studio.web import create_studio_app
from tests.test_portable_package_protocol import _experiment


def paused_run(tmp_path, monkeypatch):
    var = tmp_path / 'var'
    exp = _experiment(var / 'packages')
    manifest = read_json(exp / 'manifest.json')
    simulation = exp / manifest['entrypoints']['simulation']
    document = read_json(simulation)
    document['checkpoint_interval_steps'] = 3
    atomic_write_json(simulation, document)
    write_integrity_manifest(exp)
    run = var / 'packages/runs/run'
    original = _StatusCommitter.commit
    def commit(self, result, **kwargs):
        receipt = original(self, result, **kwargs)
        if result.step_no == 2:
            RunService().request_pause(run)
        return receipt
    with monkeypatch.context() as patch:
        patch.setattr(_StatusCommitter, 'commit', commit)
        status = RunService().start(exp, run, requested_steps=3)
    assert status.status.value == 'PAUSED' and status.committed_step == 2
    assert not (run / 'checkpoints/step-000002').exists()
    assert (run / 'recovery/step-000002/bundle.json').exists()
    return var, run, status


def test_console_resumes_same_run_with_new_attempt_and_preserves_facts(tmp_path, monkeypatch):
    var, run, before = paused_run(tmp_path, monkeypatch)
    frames = {p.name: p.read_bytes() for p in (run / 'frames').glob('step-*.json.gz')}
    submissions = []
    def submit(self, root, **kwargs):
        submissions.append(root)
        RunService().resume(root)
    monkeypatch.setattr('generative_agents.web.portable_api.FileRunSupervisor.submit', submit)
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'studio.db').as_posix(), var_dir=var)
    url = f'/api/studio/runs/{before.run_id}'
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        rows = client.get(url + '/checkpoints').json()['items']
        assert next(row for row in rows if row['step_no'] == 2)['resumable']
        assert not next(row for row in rows if row['step_no'] == 1)['resumable']
        detail = client.get(url + '/checkpoints/2').json()
        assert detail['validated'] and detail['snapshot_kind'] == 'recovery'
        assert client.post(url + '/resume', json={'checkpoint_step': 1}).status_code == 409
        assert client.post(url + '/resume', json={'checkpoint_step': 2, 'expected_attempt_id': 'stale'}).status_code == 409
        response = client.post(url + '/resume', json={'checkpoint_step': 2, 'expected_attempt_id': before.active_attempt_id})
        response.raise_for_status()
        assert response.json()['run_id'] == before.run_id
        assert response.json()['active_attempt_id'] != before.active_attempt_id
        assert response.json()['completed_steps'] == 3
        assert client.post(url + '/resume', json={'checkpoint_step': 2}).status_code == 409
    assert len(submissions) == 1
    assert all((run / 'frames' / name).read_bytes() == value for name, value in frames.items())
    attempts = [read_json(p) for p in (run / 'attempts').glob('*/attempt.json')]
    assert len(attempts) == 2 and max(a['resumed_from_step'] for a in attempts) == 2


def test_corrupt_snapshot_is_not_valid_or_resumable_and_dispatch_is_rejected(tmp_path, monkeypatch):
    var, run, before = paused_run(tmp_path, monkeypatch)
    (run / 'recovery/step-000002/state.json').write_bytes(b'{}')
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'studio.db').as_posix(), var_dir=var)
    url = f'/api/studio/runs/{before.run_id}'
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        row = next(item for item in client.get(url + '/checkpoints').json()['items'] if item['step_no'] == 2)
        assert not row['validated'] and not row['resumable'] and row['status'] == 'INVALID'
        response = client.post(url + '/resume', json={'checkpoint_step': 2})
        assert response.status_code == 409
    assert len(list((run / 'attempts').glob('*/attempt.json'))) == 1
