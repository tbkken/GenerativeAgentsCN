import pytest

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_replay.reader import ReplayReader
from generative_agents.ga_runtime.lifecycle.service import RunService
from tests.test_portable_package_protocol import _experiment


def test_validation_reused_but_live_status_and_modified_package_are_not(tmp_path, monkeypatch):
    import generative_agents.ga_replay.cache as reader

    root = RunService().create(_experiment(tmp_path / 'packages'), tmp_path / 'run')
    validate = reader.validate_run_integrity
    calls = []

    def tracked(*args, **kwargs):
        calls.append(1)
        return validate(*args, **kwargs)

    monkeypatch.setattr(reader, 'validate_run_integrity', tracked)
    with ReplayReader(root):
        pass
    status = read_json(root / 'status.json')
    status['status'] = 'FAILED'
    atomic_write_json(root / 'status.json', status)
    with ReplayReader(root) as replay:
        assert replay.status.status.value == 'FAILED'
        manifest = replay.manifest
    assert len(calls) == 1
    experiment = root / manifest.experiment.path
    resources = experiment / read_json(experiment / 'manifest.json')['entrypoints']['resources']
    resources.write_bytes(resources.read_bytes() + b' ')
    with pytest.raises(PackageError):
        with ReplayReader(root):
            pass
    assert len(calls) == 2


def test_render_manifest_omits_tile_semantics_and_boundary_poll_omits_world(tmp_path):
    from fastapi.testclient import TestClient
    from generative_agents.adapters.web.app import create_studio_app

    var = tmp_path / 'var'
    root = RunService().create(_experiment(var / 'packages'), var / 'packages/runs/run')
    run_id = read_json(root / 'status.json')['run_id']
    app = create_studio_app(database_url='sqlite:///' + (tmp_path / 'studio.db').as_posix(), var_dir=var)
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        response = client.get(f'/api/studio/runs/{run_id}/replay/manifest')
        response.raise_for_status()
        definition = response.json()['world']['definition']
        assert 'semantic_index' not in definition
        assert definition['tiles'][0]['coord'] == [0, 0]
        assert 'address' not in definition['tiles'][0]
        response = client.get(f'/api/studio/runs/{run_id}/replay/availability')
        response.raise_for_status()
        assert response.json() == {'run_id': run_id, 'available_step': 0, 'partial': True}


def test_quality_cache_is_invalidated_when_commit_evidence_changes(tmp_path, monkeypatch):
    import generative_agents.ga_replay.cache as cache
    from generative_agents.ga_protocol.schemas.manifests import RunManifest, RunStatus
    from tests.committed_frames import write_frame

    root = RunService().create(_experiment(tmp_path / 'packages'), tmp_path / 'run')
    manifest = RunManifest.model_validate(read_json(root / 'run.json'))
    status = RunStatus.model_validate(read_json(root / 'status.json'))
    status.committed_step = 1
    frame = write_frame(root, {
        'run_id': manifest.run_id, 'attempt_id': 'quality-cache-test', 'step_no': 1,
        'virtual_time': '2026-09-03T08:00:00+08:00', 'effects': [],
    })
    project = cache.project_run_quality
    calls = []

    def tracked(*args, **kwargs):
        calls.append(1)
        return project(*args, **kwargs)

    monkeypatch.setattr(cache, 'project_run_quality', tracked)
    report = cache.read_run_quality(root, manifest, status)
    assert cache.read_run_quality(root, manifest, status) == report
    assert len(calls) == 1
    frame_before = frame.read_bytes()
    commit = root / 'commits/step-000001.json'
    document = read_json(commit)
    document['sha256'] = '0' * 64
    atomic_write_json(commit, document)

    with pytest.raises(PackageError, match='cannot read committed quality frame at Step 1'):
        cache.read_run_quality(root, manifest, status)
    # Changed evidence is rejected before constructing another quality report.
    assert len(calls) == 1
    assert frame.read_bytes() == frame_before
