"""Rejected packages remain visible without blocking other author resources."""
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from generative_agents.adapters.web.routes import experiments
from generative_agents.ga_protocol.packages.io import PackageError


def test_old_package_is_reported_per_item_and_its_files_are_preserved(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    original = json.dumps({'schema_version': 1, 'protocol': 'ga-experiment'}).encode()
    (old / 'manifest.json').write_bytes(original)
    rows = [SimpleNamespace(package_kind='experiment', experiment_id=identity, location=str(location),
                            display_name=name, content_sha256=None, updated_at=datetime.now(UTC))
            for identity, location, name in [('old', old, '历史实验'), ('new', tmp_path / 'new', '新实验')]]

    def snapshot(identity, **_):
        if identity == 'old':
            raise PackageError('schema_version must be 2')
        return rows[1], {'experiment': {'name': '新实验', 'key': 'new'}}, {'agents': [], 'simulation': {}, 'world': {}}

    ctx = SimpleNamespace(
        jobs=SimpleNamespace(action=lambda _: lambda operation: operation),
        catalog=SimpleNamespace(page=lambda **_: {'items': rows, 'total': len(rows)},
                                recent_runs=lambda _: {},
                                get=lambda kind, identity: next((row for row in rows if row.experiment_id == identity), None)),
        read_presentation=lambda: {}, experiment_snapshot=snapshot,
        serialized_experiment=lambda operation: operation,
    )
    router = APIRouter(prefix='/api/studio')
    experiments.install_routes(router, ctx)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get('/api/studio/experiments')
        assert response.status_code == 200
        items = {item['id']: item for item in response.json()['items']}
        assert '协议版本不支持' in items['old']['package_error']
        assert items['old']['editable'] is False
        assert 'package_error' not in items['new']
        assert response.json()['total'] == 2
        response = client.get('/api/studio/experiments/old')
        assert response.status_code == 422
        assert '协议版本不支持' in response.json()['detail']
    assert (old / 'manifest.json').read_bytes() == original
