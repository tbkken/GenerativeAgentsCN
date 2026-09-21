import gzip
import hashlib
import json
from uuid import uuid4
from fastapi.testclient import TestClient
from generative_agents.ga_protocol import read_json, atomic_write_json
from generative_agents.ga_runtime.service import RunService
from generative_agents.ga_studio.web import create_studio_app
from tests.test_portable_package_protocol import _experiment


def test_trace_usage_tools_and_chinese_log_are_read_from_run_files(tmp_path):
    var = tmp_path/'var'
    experiment = _experiment(var/'packages')
    run = var/'packages/runs/sample'
    RunService().start(experiment, run, requested_steps=2)
    run_id = read_json(run/'run.json')['run_id']
    attempt = str(uuid4())
    atomic_write_json(run/'attempts'/attempt/'attempt.json', {'attempt_id': attempt, 'ordinal': 1})
    status = read_json(run/'status.json'); status['committed_step'] = 1
    atomic_write_json(run/'status.json', status)
    frame = dict(run_id=run_id, attempt_id=attempt, step_no=1, virtual_time='2026-09-07T06:00:00+08:00',
                 agents=[], domain_events=[], memory_deltas=[], conversations=[],
                 effects=[{'agent_keys':['teacher'], 'payload':{'trace':[
                     {'event':'mcp.call','tool':'world-perceive','input_text':'{"radius":2}', 'output_text':'附近地点'}]}}])
    (run/'frames/step-000001.json.gz').write_bytes(gzip.compress(json.dumps({'result':frame}).encode()))
    projection = read_json(run/'projection.json')
    projection['steps']['1']['frame_sha256'] = hashlib.sha256((run/'frames/step-000001.json.gz').read_bytes()).hexdigest()
    atomic_write_json(run/'projection.json', projection)
    common = dict(attempt_id=attempt, agent_key='teacher', step_no=1, call_id='call', purpose='skill_runtime', provider='vllm', resolved_model='model')
    records = [{**common,'event_seq':1,'event_type':'PHYSICAL_START','attempt_no':1},
               {**common,'event_seq':2,'event_type':'PHYSICAL_ATTEMPT','attempt_no':1,'prompt_tokens':10,'completion_tokens':4},
               {**common,'event_seq':3,'event_type':'LOGICAL_END'}]
    (run/'traces').mkdir(exist_ok=True)
    (run/'traces/model.jsonl').write_text('\n'.join(json.dumps(x) for x in records), encoding='utf-8')
    (run/'logs').mkdir(exist_ok=True)
    # FileRunSupervisor explicitly sets PYTHONUTF8/PYTHONIOENCODING for workers.
    (run/'logs/runtime-process.log').write_bytes('教师 陈明远 在溪谷大学\n'.encode('utf-8'))
    app=create_studio_app(database_url='sqlite:///'+(tmp_path/'test.db').as_posix(),var_dir=var)
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        base='/api/studio/runs/'+run_id
        detail=client.get(base+'/model-traces/'+attempt+':2').json()
        assert not detail['payload_available']
        assert detail['iteration_tools'][0]['tool']=='world-perceive'
        usage=client.get(base+'/results/operations').json()['model_usage'][0]
        assert (usage['logical_calls'],usage['physical_attempts'],usage['input_tokens'])==(1,1,10)
        cursor=0; text=''
        while True:
            page=client.get(base+f'/attempts/{attempt}/log?cursor={cursor}&limit_bytes=2').json()
            text+=page['content'];cursor=page['next_cursor']
            if page['eof']:break
        assert text=='教师 陈明远 在溪谷大学\n'

def test_failure_keeps_latest_committed_status(tmp_path, monkeypatch):
    import pytest
    from generative_agents.ga_runtime.executor import execute_run_directory
    experiment = _experiment(tmp_path/'packages')
    root = RunService().create(experiment, tmp_path/'run', requested_steps=2)
    def fail_after_commit(**kwargs):
        latest = read_json(kwargs['status_path'])
        latest['committed_step'] = 1
        atomic_write_json(kwargs['status_path'], latest)
        raise PermissionError('projection occupied')
    monkeypatch.setattr('generative_agents.ga_runtime.executor._execute_attempt', fail_after_commit)
    with pytest.raises(PermissionError):
        execute_run_directory(root)
    latest=read_json(root/'status.json')
    assert latest['status']=='FAILED'
    assert latest['committed_step']==1
