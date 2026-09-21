from pathlib import Path
from fastapi.testclient import TestClient
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_runtime.lifecycle.service import RunService
from generative_agents.adapters.web.app import create_studio_app
from tests.test_portable_package_protocol import _experiment


def test_sealed_experiment_creates_distinct_run_and_keeps_failed_history(tmp_path,monkeypatch):
    var=tmp_path/'var';exp=_experiment(var/'packages');old=RunService().create(exp,var/'packages/runs/old',requested_steps=2)
    old_status=read_json(old/'status.json');old_status['status']='FAILED';old_status['reason']='projection occupied';atomic_write_json(old/'status.json',old_status)
    original={p.relative_to(old):p.read_bytes() for p in old.rglob('*') if p.is_file()}
    submitted=[]
    def submit(self, root, **kwargs):
        submitted.append(Path(root));status=read_json(root/'status.json');status['status']='QUEUED';atomic_write_json(root/'status.json',status)
    monkeypatch.setattr('generative_agents.adapters.web.context.FileRunSupervisor.submit',submit)
    app=create_studio_app(database_url='sqlite:///'+(tmp_path/'studio.db').as_posix(),var_dir=var)
    identity=read_json(exp/'manifest.json')['experiment']['experiment_id'];url='/api/studio/experiments/'+identity
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        client.post(url+'/seal').raise_for_status()
        assert client.post(url+'/validate').json()['valid']
        created=client.post(url+'/runs');created.raise_for_status();new=created.json()
        assert new['run_id']!=old_status['run_id'] and new['status']=='QUEUED'
        ids={r['run_id'] for r in client.get(url+'/runs').json()['items']}
        assert ids=={new['run_id'],old_status['run_id']}
        assert len(submitted)==1
        assert {p.relative_to(old):p.read_bytes() for p in old.rglob('*') if p.is_file()}==original
