from pathlib import Path
import pytest
from filelock import FileLock
from fastapi.testclient import TestClient
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.packages.io import write_integrity_manifest
from generative_agents.ga_runtime.lifecycle.service import RunService
from generative_agents.ga_runtime.lifecycle.executor import _StatusCommitter
from generative_agents.adapters.web.app import create_studio_app
from tests.test_portable_package_protocol import _experiment


def paused_run(tmp_path, monkeypatch):
    var = tmp_path / 'var'
    exp = _experiment(var / 'packages')
    manifest = read_json(exp / 'manifest.json')
    assembly = exp / manifest['entrypoints']['assembly']
    document = read_json(assembly)
    document['simulation']['checkpoint_interval_steps'] = 3
    atomic_write_json(assembly, document)
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
    monkeypatch.setattr('generative_agents.adapters.web.context.FileRunSupervisor.submit', submit)
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'studio.db').as_posix(), var_dir=var)
    url = f'/api/studio/runs/{before.run_id}'
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        rows = client.get(url + '/checkpoints').json()['items']
        assert next(row for row in rows if row['step_no'] == 2)['recovery_check_required']
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
        row = client.get(url + '/checkpoints/2').json()
        assert not row['validated'] and not row['resumable'] and row['status'] == 'INVALID'
        capability = client.get(url + '/recovery').json()
        assert not capability['recoverable'] and 'Step 2' in capability['recovery_reason']
        response = client.post(url + '/resume', json={'checkpoint_step': 2})
        assert response.status_code == 409
    assert len(list((run / 'attempts').glob('*/attempt.json'))) == 1


def test_new_studio_observes_external_lock_then_recovers_first_step(tmp_path, monkeypatch):
    var = tmp_path / 'var'
    run = RunService().create(_experiment(var / 'packages'), var / 'packages/runs/first', requested_steps=1)
    status = read_json(run / 'status.json')
    status['status'] = 'RUNNING'
    atomic_write_json(run / 'status.json', status)
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'studio.db').as_posix(), var_dir=var)
    url = f"/api/studio/runs/{status['run_id']}"
    monkeypatch.setattr('generative_agents.adapters.web.context.FileRunSupervisor.submit', lambda self, root, **kw: RunService().resume(root))
    with FileLock(str(run / 'worker.lock'), timeout=0) as worker, TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        live = client.get(url).json()
        assert live['status'] == 'RUNNING' and not live['process']['owned_by_this_studio']
        assert not client.get(url + '/recovery').json()['recoverable']
        assert client.post(url + '/resume').status_code == 409
        worker.release()
        interrupted = client.get(url).json()
        assert interrupted['status'] == 'FAILED' and interrupted['reason']
        capability = client.get(url + '/recovery').json()
        assert capability['recoverable'] and capability['recovery_action'] == 'restart'
        assert capability['recoverable_step'] == 0
        response = client.post(url + '/resume', json={'checkpoint_step': 0, 'expected_attempt_id': None})
        response.raise_for_status()
        assert response.json()['run_id'] == status['run_id'] and response.json()['completed_steps'] == 1
        assert client.post(url + '/resume', json={'checkpoint_step': 0}).status_code == 409


def test_recovery_of_last_step_offers_only_finalization(tmp_path, monkeypatch):
    var = tmp_path / 'var'
    run = var / 'packages/runs/final'
    before = RunService().start(_experiment(var / 'packages'), run, requested_steps=1)
    atomic_write_json(run / 'status.json', {**read_json(run / 'status.json'), 'status': 'FINALIZING'})
    frame = (run / 'frames/step-000001.json.gz').read_bytes()
    report = run / 'artifacts/quality-report.json'
    report.unlink()
    monkeypatch.setattr('generative_agents.adapters.web.context.FileRunSupervisor.submit', lambda self, root, **kw: RunService().resume(root))
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'studio.db').as_posix(), var_dir=var)
    url = f'/api/studio/runs/{before.run_id}'
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        capability = client.get(url + '/recovery').json()
        assert capability['recoverable'] and capability['recovery_action'] == 'finalize'
        response = client.post(url + '/resume', json={'checkpoint_step': 1, 'expected_attempt_id': before.active_attempt_id})
        response.raise_for_status()
        assert response.json()['status'] == 'COMPLETED'
    assert report.is_file() and len(list((run / 'attempts').glob('*/attempt.json'))) == 1
    assert (run / 'frames/step-000001.json.gz').read_bytes() == frame


def test_run_page_and_recovery_need_only_the_registered_run(tmp_path, monkeypatch):
    var, run, before = paused_run(tmp_path, monkeypatch)
    experiment_id = read_json(run / 'run.json')['experiment']['experiment_id']
    (var / 'packages/renamable-experiment-directory').rename(tmp_path / 'removed-authoring-package')
    monkeypatch.setattr('generative_agents.adapters.web.context.FileRunSupervisor.submit', lambda self, root, **kw: RunService().resume(root))
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'studio.db').as_posix(), var_dir=var)
    url = f'/api/studio/runs/{before.run_id}'
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        assert client.get(f'/api/studio/experiments/{experiment_id}').status_code == 404
        assert 'id="resumeRunModal"' in client.get(f'/runs/{before.run_id}').text
        capability = client.get(url + '/recovery').json()
        assert capability['recoverable'] and capability['recoverable_step'] == 2
        response = client.post(url + '/resume', json={'checkpoint_step': 2, 'expected_attempt_id': before.active_attempt_id})
        response.raise_for_status()
        assert response.json()['run_id'] == before.run_id and response.json()['completed_steps'] == 3
        assert client.get(f'/api/studio/experiments/{experiment_id}').status_code == 404
