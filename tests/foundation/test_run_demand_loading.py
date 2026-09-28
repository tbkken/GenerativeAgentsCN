"""Read budgets are assertions about I/O, not machine-dependent wall times."""
import json
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from generative_agents.adapters.web.app import create_studio_app
from generative_agents.ga_protocol.packages.io import atomic_write_json, read_json, PackageError, write_integrity_manifest
from generative_agents.ga_replay.reader import ReplayReader
from generative_agents.ga_replay.queries import result_index, trace_records, trace_page, trace_record
from generative_agents.ga_runtime.lifecycle.service import RunService
from tests.committed_frames import write_frame
from tests.test_portable_package_protocol import _experiment
from tests.artifact_jobs import wait_artifact


def fixture(tmp_path, steps=5):
    var = tmp_path / 'var'
    experiment = _experiment(var / 'packages')
    assembly_path = experiment / read_json(experiment / 'manifest.json')['entrypoints']['assembly']
    assembly = read_json(assembly_path); assembly['simulation']['max_steps'] = steps+1
    atomic_write_json(assembly_path, assembly)
    write_integrity_manifest(experiment)
    root = RunService().create(experiment, var / 'packages/runs/test', requested_steps=steps+1)
    run_id = read_json(root / 'run.json')['run_id']
    attempt = str(uuid4())
    for step in range(1, steps+1):
        append_frame(root, run_id, attempt, step)
    atomic_write_json(root / 'attempts' / attempt / 'attempt.json', {'attempt_id': attempt, 'ordinal': 1, 'resumed_from_step': 0})
    status = read_json(root / 'status.json')
    status.update(committed_step=steps, active_attempt_id=attempt, status='PAUSED')
    atomic_write_json(root / 'status.json', status)
    (root / 'logs').mkdir()
    (root / 'logs/runtime-process.log').write_text('中文日志\n' * 100, encoding='utf-8')
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'db.sqlite').as_posix(), var_dir=var)
    return root, run_id, attempt, app


def append_frame(root, run_id, attempt, step):
    write_frame(root, {'run_id': run_id, 'attempt_id': attempt, 'step_no': step,
        'virtual_time': '2026-09-03T08:00:00+08:00', 'agents': [], 'effects': [], 'domain_events': [],
        'memory_deltas': [{'agent_key': 'person', 'memory_id': 'memory', 'kind': 'CREATED' if step == 1 else 'ACCESSED', 'description': 'remember'}],
        'conversations': [{'conversation_id': 'conversation', 'participant_agent_keys': ['person'],
                           'messages': [{'message_id': str(step), 'speaker_agent_key': 'person', 'sequence': step, 'content': str(step)}]}]})


def track_frames(monkeypatch):
    reads = []
    original = ReplayReader.read_step
    def tracked(self, step):
        reads.append(step)
        return original(self, step)
    monkeypatch.setattr(ReplayReader, 'read_step', tracked)
    return reads


def test_status_manifest_windows_and_log_do_not_read_unrelated_run_history(tmp_path, monkeypatch):
    root, run_id, attempt, app = fixture(tmp_path)
    (root / 'artifacts').mkdir(exist_ok=True)
    (root / 'artifacts/unrelated.zip').write_bytes(b'x' * 65536)
    reads = track_frames(monkeypatch)
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        reads.clear()
        base = f'/api/studio/runs/{run_id}'
        for route in ('', '/replay/availability', '/replay/manifest', '/attempts', f'/attempts/{attempt}/log?limit_bytes=3'):
            response = client.get(base + route)
            response.raise_for_status()
        assert reads == []
        response = client.get(base + '/results/timeline?from_step=3&limit=2')
        response.raise_for_status()
        assert [item['step_no'] for item in response.json()['steps']] == [3, 4]
        assert reads == [3, 4]
        reads.clear()
        response = client.get(base + '/replay/steps?from_step=1&limit=2')
        response.raise_for_status()
        assert reads == [1, 2]
        assert all('effects' not in item for item in response.json()['steps'])
        reads.clear()
        response = client.get(base + '/replay/steps?from_step=5&limit=1&include_audit=true&include_baseline=false')
        response.raise_for_status()
        assert reads == [5]
        assert 'effects' in response.json()['steps'][0]


def test_result_projection_reads_only_new_frames_and_invalidates_changed_evidence(tmp_path, monkeypatch):
    root, run_id, attempt, _app = fixture(tmp_path)
    reads = track_frames(monkeypatch)
    first = result_index(root)
    assert reads == [1, 2, 3, 4, 5]
    assert first['conversations'][0]['message_count'] == 5
    reads.clear()
    assert result_index(root)['memories'][0]['last_accessed_step'] == 5
    assert reads == []
    append_frame(root, run_id, attempt, 6)
    status = read_json(root / 'status.json'); status['committed_step'] = 6
    atomic_write_json(root / 'status.json', status)
    updated = result_index(root)
    assert reads == [6]
    assert updated['conversations'][0]['message_count'] == 6
    assert updated['memories'][0]['last_accessed_step'] == 6
    assert first['conversations'][0]['message_count'] == 5
    commit = root / 'commits/step-000001.json'
    document = read_json(commit); document['sha256'] = '0' * 64
    atomic_write_json(commit, document)
    with pytest.raises(PackageError):
        result_index(root)


def test_trace_append_reads_only_new_bytes_and_keeps_summary_payload_separate(tmp_path, monkeypatch):
    root, _run_id, attempt, _app = fixture(tmp_path)
    path = root / 'traces/model.jsonl'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'attempt_id': attempt, 'event_seq': 1, 'payload': 'large' * 1000}) + '\n', encoding='utf-8')
    assert len(trace_records(root)) == 1
    appended = json.dumps({'attempt_id': attempt, 'event_seq': 2}) + '\n'
    with path.open('ab') as handle:
        handle.write(appended.encode())
    original = Path.open
    read_bytes = []
    class Reader:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def seek(self, *args): return self.handle.seek(*args)
        def tell(self): return self.handle.tell()
        def readline(self):
            value = self.handle.readline(); read_bytes.append(len(value)); return value
    def tracked(self, *args, **kwargs):
        handle = original(self, *args, **kwargs)
        return Reader(handle) if self == path and args and args[0] == 'rb' else handle
    monkeypatch.setattr(Path, 'open', tracked)
    assert len(trace_records(root)) == 2
    assert sum(read_bytes) == len(appended.encode())


def test_trace_first_page_and_selected_detail_do_not_parse_the_remaining_file(tmp_path, monkeypatch):
    import generative_agents.ga_replay.queries as queries
    root, _run_id, attempt, _app = fixture(tmp_path)
    path = root / 'traces/model.jsonl'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b''.join((json.dumps({'attempt_id': attempt, 'event_seq': sequence,
                                         'payload': f'secret-payload-{sequence}'})+'\n').encode()
                             for sequence in range(1, 501)))
    original = json.loads
    decoded = []
    def tracked(value, *args, **kwargs):
        result = original(value, *args, **kwargs)
        if isinstance(result, dict) and 'event_seq' in result:
            decoded.append(result['event_seq'])
        return result
    monkeypatch.setattr(queries.json, 'loads', tracked)
    first = trace_page(root, limit=2)
    assert [item['event_seq'] for item in first['items']] == [1, 2]
    assert all('payload' not in item for item in first['items'])
    assert decoded == [1, 2, 3]
    decoded.clear()
    assert trace_record(root, f'{attempt}:2')['payload'] == 'secret-payload-2'
    assert decoded == [2]
    decoded.clear()
    following = trace_page(root, cursor=first['next_cursor'], limit=2)
    assert [item['event_seq'] for item in following['items']] == [3, 4]
    assert decoded == [3, 4, 5]


def test_quality_only_reads_new_committed_audit_frames(tmp_path, monkeypatch):
    import generative_agents.ga_protocol.facts.quality as projection
    from generative_agents.ga_protocol.schemas.manifests import RunManifest, RunStatus
    from generative_agents.ga_replay.cache import read_run_quality
    root, run_id, attempt, _app = fixture(tmp_path)
    manifest = RunManifest.model_validate(read_json(root / 'run.json'))
    status = RunStatus.model_validate(read_json(root / 'status.json'))
    original = projection._read_frame
    reads = []
    def tracked(root, run_id, step):
        reads.append(step)
        return original(root, run_id, step)
    monkeypatch.setattr(projection, '_read_frame', tracked)
    read_run_quality(root, manifest, status)
    assert reads == [1, 2, 3, 4, 5]
    reads.clear()
    append_frame(root, run_id, attempt, 6)
    status.committed_step = 6
    read_run_quality(root, manifest, status)
    assert reads == [6]


def test_artifact_job_returns_before_expensive_work_and_retains_captured_boundary(tmp_path, monkeypatch):
    import generative_agents.adapters.web.routes.artifacts as routes
    root, run_id, _attempt, app = fixture(tmp_path)
    started, release = Event(), Event()
    original = routes.read_run_quality
    def paused(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(routes, 'read_run_quality', paused)
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        response = client.post(f'/api/studio/runs/{run_id}/artifact-jobs', json={'job_type': 'RESULT_BUNDLE'})
        try:
            assert response.status_code == 201
            assert response.json()['status'] == 'QUEUED'
            assert started.wait(2)
            job = client.get(f'/api/studio/runs/{run_id}/artifact-jobs/{response.json()["job_id"]}').json()
            assert job['status'] == 'RUNNING'
            assert job['parameters']['source_step'] == 5
            assert client.delete(f'/api/studio/runs/{run_id}').status_code == 409
        finally:
            release.set()
        result = wait_artifact(client, run_id, response).json()
        assert (root / 'artifacts' / result['artifact_name']).is_file()
