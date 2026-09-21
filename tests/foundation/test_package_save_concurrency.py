import ctypes
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from generative_agents.ga_protocol.packages.io import atomic_write_bytes
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_studio.experiments.workspace import ExperimentWorkspaceService
from generative_agents.ga_studio.experiments.workspace import WorkspaceConflictError
from generative_agents.ga_studio.catalog.packages import StudioPackageCatalogService
from generative_agents.ga_protocol.packages.definition import _experiment_definition
from tests.test_portable_package_protocol import _experiment


def test_identical_file_does_not_replace(tmp_path, monkeypatch):
    target = tmp_path/'world.json'
    target.write_bytes(b'unchanged')
    monkeypatch.setattr('generative_agents.ga_protocol.packages.io._replace_file', lambda *_: pytest.fail('unchanged file replaced'))
    atomic_write_bytes(target, b'unchanged')


def test_windows_replace_retry_is_bounded_and_preserves_original(tmp_path, monkeypatch):
    target = tmp_path/'world.json'
    target.write_bytes(b'original')
    attempts = []
    def blocked(*_):
        attempts.append(1)
        error = PermissionError('sharing violation')
        error.winerror = 5
        raise error
    monkeypatch.setattr('generative_agents.ga_protocol.packages.io._replace_file', blocked)
    monkeypatch.setattr('generative_agents.ga_protocol.packages.io.time.sleep', lambda _: None)
    with pytest.raises(PermissionError):
        atomic_write_bytes(target, b'new')
    assert len(attempts) == 7
    assert target.read_bytes() == b'original'
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(os.name != 'nt', reason='Windows sharing semantics')
def test_real_windows_reader_without_delete_sharing_can_release_during_retry(tmp_path):
    target = tmp_path/'world.json'
    target.write_bytes(b'original')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(target), 0x80000000, 1 | 2, None, 3, 0, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    timer = threading.Timer(.15, lambda: kernel.CloseHandle(handle))
    timer.start()
    try:
        atomic_write_bytes(target, b'new')
    finally:
        timer.join()
    assert target.read_bytes() == b'new'


def test_concurrent_saves_reject_stale_input_and_do_not_rebuild_world(database, tmp_path, monkeypatch):
    root = _experiment(tmp_path)
    catalog = StudioPackageCatalogService(database)
    record = catalog.upsert(root)
    service = ExperimentWorkspaceService(database, package_root=tmp_path/'packages', var_dir=tmp_path)
    _, definition = _experiment_definition(root)
    expected = read_json(root/'integrity/sha256.json')['root_sha256']
    before = (root/'world/world.json').stat().st_mtime_ns
    definition['experiment']['goal'] = 'updated'
    monkeypatch.setattr('generative_agents.ga_studio.experiments.builder.build_semantic_index', lambda *_: pytest.fail('unchanged world rebuilt'))
    monkeypatch.setattr('generative_agents.ga_studio.catalog.packages.validate_experiment_integrity', lambda *_: pytest.fail('catalog redundantly revalidated saved package'))
    barrier = threading.Barrier(2)
    def save():
        barrier.wait()
        try:
            service.replace_definition(record.package_id, definition, expected_content_sha256=expected)
            return 'saved'
        except WorkspaceConflictError:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: save(), range(2)))
    assert sorted(results) == ['conflict', 'saved']
    assert (root/'world/world.json').stat().st_mtime_ns == before
    validate_experiment_directory(root)

def test_asset_upload_advances_save_token_and_rejects_stale_upload(database, tmp_path):
    root = _experiment(tmp_path)
    catalog = StudioPackageCatalogService(database)
    record = catalog.upsert(root)
    service = ExperimentWorkspaceService(database, package_root=tmp_path/'packages', var_dir=tmp_path)
    _, definition = _experiment_definition(root)
    expected = read_json(root/'integrity/sha256.json')['root_sha256']
    uploaded = service.add_assets(record.package_id, {'assets/agents/upload.bin': b'image'}, expected_content_sha256=expected)
    with pytest.raises(WorkspaceConflictError):
        service.add_assets(record.package_id, {'assets/agents/stale.bin': b'stale'}, expected_content_sha256=expected)
    assert not (root/'assets/agents/stale.bin').exists()
    definition['experiment']['goal'] = 'after upload'
    service.replace_definition(record.package_id, definition, expected_content_sha256=uploaded['content_sha256'])
    validate_experiment_directory(root)

def test_failed_multi_file_save_rolls_back_valid_package(database, tmp_path, monkeypatch):
    root = _experiment(tmp_path)
    record = StudioPackageCatalogService(database).upsert(root)
    service = ExperimentWorkspaceService(database, package_root=tmp_path/'packages', var_dir=tmp_path)
    _, definition = _experiment_definition(root)
    originals = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    definition['simulation']['max_steps'] = 3
    definition['experiment']['goal'] = 'must roll back'
    def fail_manifest(*_):
        error = PermissionError('manifest locked')
        error.winerror = 5
        raise error
    monkeypatch.setattr('generative_agents.ga_studio.experiments.workspace.atomic_write_json', fail_manifest)
    with pytest.raises(PermissionError):
        service.replace_definition(record.package_id, definition)
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == originals
    validate_experiment_directory(root)


def test_list_and_detail_remain_consistent_during_seal(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from generative_agents.adapters.web.app import create_studio_app
    import generative_agents.adapters.web.routes.experiments as api_module
    var = tmp_path/'var'
    root = _experiment(var/'packages')
    app = create_studio_app(database_url='sqlite:///'+(tmp_path/'studio.db').as_posix(), var_dir=var)
    removed = threading.Event()
    release = threading.Event()
    real_remove = api_module.shutil.rmtree
    def pause_after_remove(path, *args, **kwargs):
        result = real_remove(path, *args, **kwargs)
        if Path(path).resolve() == root.resolve():
            removed.set()
            assert release.wait(8)
        return result
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        identity = read_json(root/'manifest.json')['experiment']['experiment_id']
        url = '/api/studio/experiments/'+identity
        monkeypatch.setattr(api_module.shutil, 'rmtree', pause_after_remove)
        with ThreadPoolExecutor(3) as pool:
            seal = pool.submit(client.post, url+'/seal')
            assert removed.wait(8)
            detail = pool.submit(client.get, url)
            listing = pool.submit(client.get, '/api/studio/experiments')
            release.set()
            assert seal.result().status_code == 200
            assert detail.result().json()['editable'] is False
            assert listing.result().json()['items'][0]['editable'] is False
        # Reading sealed metadata must never extract assets or acquire an archive-wide lock.
        monkeypatch.setattr(api_module, 'open_package', lambda *_: pytest.fail('metadata extracted archive'))
        assert client.get(url).status_code == 200
        assert client.get('/api/studio/experiments').status_code == 200

def test_immutable_run_validation_does_not_take_workspace_save_lock(tmp_path, monkeypatch):
    from generative_agents.ga_runtime.lifecycle.service import RunService
    from generative_agents.ga_protocol.packages.validation import validate_run_directory
    from generative_agents.ga_protocol.packages.io import open_package
    source = _experiment(tmp_path)
    run = tmp_path/'run'
    RunService().start(source, run, requested_steps=2)
    monkeypatch.setattr('generative_agents.ga_protocol.packages.locking._lock', lambda *_: pytest.fail('immutable Run took Studio save lock'))
    validate_run_directory(run)
    with open_package(source) as opened:
        validate_experiment_directory(opened)

def test_run_overview_reads_live_status_without_full_replay_validation(tmp_path, monkeypatch):
    from generative_agents.ga_runtime.lifecycle.service import RunService
    from generative_agents.ga_replay.reader import ReplayReader
    from generative_agents.ga_replay.reader import read_run_overview
    from generative_agents.ga_protocol.packages.io import atomic_write_json
    from generative_agents.ga_protocol.packages.io import PackageError
    source = _experiment(tmp_path)
    root = tmp_path/'run'
    RunService().start(source, root, requested_steps=2)
    with ReplayReader(root) as replay:
        expected = replay.summary()
    monkeypatch.setattr('generative_agents.ga_replay.reader.validate_run_integrity', lambda *_args, **_kw: pytest.fail('poll revalidated full replay'))
    assert read_run_overview(root)[0] == expected
    status = read_json(root/'status.json')
    status['committed_step'] = 1
    atomic_write_json(root/'status.json', status)
    assert read_run_overview(root)[0]['committed_step'] == 1
    status['run_id'] = '00000000-0000-4000-8000-000000000000'
    atomic_write_json(root/'status.json', status)
    with pytest.raises(PackageError, match='another Run'):
        read_run_overview(root)
