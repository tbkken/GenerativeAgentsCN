import ctypes
import threading
from pathlib import Path
import pytest
from generative_agents.ga_protocol import read_json,atomic_write_json,write_integrity_manifest
from generative_agents.ga_runtime.service import RunService
from generative_agents.runtime.file_result_projector import FileResultProjector
from tests.test_portable_package_protocol import _experiment


def source(tmp_path):
    root=_experiment(tmp_path/'packages')
    manifest=read_json(root/'manifest.json')
    path=root/manifest['entrypoints']['simulation']
    config=read_json(path);config['checkpoint_interval_steps']=1
    atomic_write_json(path,config);write_integrity_manifest(root)
    return root


@pytest.mark.skipif(__import__('os').name!='nt',reason='Windows file sharing')
def test_projector_retries_real_windows_reader_handle(tmp_path,monkeypatch):
    original=FileResultProjector.commit_step
    held=[]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    def commit(self,result,**kwargs):
        if result.step_no==2:
            handle=kernel.CreateFileW(str(self._path),0x80000000,3,None,3,0,None)
            assert handle not in (None,ctypes.c_void_p(-1).value)
            timer=threading.Timer(.15,lambda:kernel.CloseHandle(handle));timer.start();held.append(timer)
        return original(self,result,**kwargs)
    monkeypatch.setattr(FileResultProjector,'commit_step',commit)
    status=RunService().start(source(tmp_path),tmp_path/'run',requested_steps=3)
    for timer in held:timer.join()
    assert status.status.value=='COMPLETED' and status.committed_step==3
    assert read_json(tmp_path/'run/projection.json')['available_step']==3


@pytest.mark.parametrize("failed_step", [1, 2])
def test_status_failure_resumes_without_rewriting_committed_frame(tmp_path,monkeypatch,failed_step):
    import generative_agents.ga_protocol.io as io
    real_replace=io._replace_file
    def block(source,target):
        if Path(target).name=='status.json':
            document = read_json(Path(source))
            if document['committed_step']==failed_step and document['status']=='RUNNING':
                error=PermissionError('status occupied');error.winerror=5;raise error
        return real_replace(source,target)
    exp=source(tmp_path);run=tmp_path/'run'
    with monkeypatch.context() as patch:
        patch.setattr(io,'_replace_file',block)
        patch.setattr(io.time,'sleep',lambda _:None)
        with pytest.raises(PermissionError):RunService().start(exp,run,requested_steps=3)
    failed=read_json(run/'status.json')
    assert failed['status']=='FAILED' and failed['committed_step']==failed_step-1
    from generative_agents.ga_replay import ReplayReader
    with ReplayReader(run) as replay:
        assert replay.available_steps() == tuple(range(1, failed_step))
    old=(run/'frames/step-000001.json.gz').read_bytes() if failed_step>1 else None
    status=RunService().resume(run)
    assert status.status.value=='COMPLETED' and status.committed_step==3
    if old is not None: assert (run/'frames/step-000001.json.gz').read_bytes()==old
    assert list((run/'orphaned').rglob(f'step-{failed_step:06d}.json.gz'))
    assert len(list((run/'attempts').iterdir()))==2


@pytest.mark.parametrize('blocked_steps', [{1}, {2}, {1, 2, 3}])
def test_projection_denial_does_not_fail_committed_run(tmp_path, monkeypatch, caplog, blocked_steps):
    import generative_agents.ga_protocol.io as io
    from generative_agents.ga_replay import ReplayReader
    real_replace = io._replace_file
    def block(source, target):
        if Path(target).name == 'projection.json' and read_json(Path(source))['available_step'] in blocked_steps:
            error = PermissionError('projection occupied'); error.winerror = 5; raise error
        return real_replace(source, target)
    monkeypatch.setattr(io, '_replace_file', block)
    monkeypatch.setattr(io.time, 'sleep', lambda _: None)
    run = tmp_path / 'run'
    status = RunService().start(source(tmp_path), run, requested_steps=3)
    assert status.status.value == 'COMPLETED' and status.committed_step == 3
    with ReplayReader(run) as replay:
        assert replay.available_steps() == (1, 2, 3)
        assert len(list(replay.iter_steps())) == 3
    assert 'PROJECTION_WRITE_DEFERRED' in caplog.text
    if 3 not in blocked_steps:
        assert read_json(run / 'projection.json')['available_step'] == 3
        assert 'PROJECTION_WRITE_RECOVERED' in caplog.text


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows file sharing')
def test_shared_reader_allows_576_atomic_replacements_while_handle_stays_open(tmp_path):
    from generative_agents.ga_protocol.io import open_shared_reader
    path = tmp_path / 'projection.json'
    atomic_write_json(path, {'step': 0})
    with open_shared_reader(path) as held:
        for step in range(1, 577):
            atomic_write_json(path, {'step': step})
            assert read_json(path) == {'step': step}
        assert __import__('json').loads(held.read()) == {'step': 0}


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows file sharing')
def test_external_reader_held_past_retry_budget_does_not_stop_run(tmp_path, monkeypatch):
    from generative_agents.ga_replay import ReplayReader
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    held = []
    original = FileResultProjector.commit_step
    def commit(self, result, **kwargs):
        if result.step_no == 2:
            handle = kernel.CreateFileW(str(self._path), 0x80000000, 3, None, 3, 0, None)
            assert handle not in (None, ctypes.c_void_p(-1).value)
            held.append(handle)
        return original(self, result, **kwargs)
    monkeypatch.setattr(FileResultProjector, 'commit_step', commit)
    run = tmp_path / 'run'
    try:
        status = RunService().start(source(tmp_path), run, requested_steps=3)
        assert status.status.value == 'COMPLETED' and status.committed_step == 3
        assert read_json(run / 'projection.json')['available_step'] == 1
        with ReplayReader(run) as replay:
            assert replay.available_steps() == (1, 2, 3)
            assert replay.read_step(3)['step_no'] == 3
    finally:
        for handle in held: kernel.CloseHandle(handle)


def test_new_worker_rebuilds_lagging_projection_before_resume(tmp_path, monkeypatch):
    import generative_agents.ga_protocol.io as io
    run = tmp_path / 'run'
    real_replace = io._replace_file
    original = FileResultProjector.commit_step
    def block(source, target):
        if Path(target).name == 'projection.json' and read_json(Path(source))['available_step'] == 2:
            error = PermissionError('projection occupied'); error.winerror = 5; raise error
        return real_replace(source, target)
    def commit(self, result, **kwargs):
        version = original(self, result, **kwargs)
        if result.step_no == 2:
            RunService().request_pause(run)
        return version
    with monkeypatch.context() as patch:
        patch.setattr(io, '_replace_file', block)
        patch.setattr(io.time, 'sleep', lambda _: None)
        patch.setattr(FileResultProjector, 'commit_step', commit)
        status = RunService().start(source(tmp_path), run, requested_steps=3)
    assert status.status.value == 'PAUSED' and status.committed_step == 2
    assert read_json(run / 'projection.json')['available_step'] == 1
    before = {p.name: p.read_bytes() for p in (run / 'frames').glob('step-*.json.gz')}
    status = RunService().resume(run)
    assert status.status.value == 'COMPLETED' and status.committed_step == 3
    assert read_json(run / 'projection.json')['available_step'] == 3
    assert all((run / 'frames' / name).read_bytes() == content for name, content in before.items())


def test_missing_checkpoint_rejects_resume_before_new_attempt(tmp_path):
    from generative_agents.ga_protocol import PackageError
    run=RunService().create(source(tmp_path),tmp_path/'run',requested_steps=3)
    status=read_json(run/'status.json');status.update(status='FAILED',committed_step=1)
    atomic_write_json(run/'status.json',status)
    before={p.relative_to(run):p.read_bytes() for p in run.rglob('*') if p.is_file() and p.name!='worker.lock'}
    with pytest.raises(PackageError,match='complete checkpoint'):
        RunService().resume(run)
    assert {p.relative_to(run):p.read_bytes() for p in run.rglob('*') if p.is_file() and p.name!='worker.lock'}==before
    assert not list((run/'attempts').iterdir())
