"""HTTP uploads keep preview identity stable and commits idempotent."""
from pathlib import Path
from types import SimpleNamespace
from zipfile import BadZipFile
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from generative_agents.adapters.web.routes import resource_exchange


@pytest.fixture
def exchange_client(tmp_path, monkeypatch):
    operations = []

    class Service:
        def __init__(self, database, *, asset_store):
            pass

        def catalog(self, *, kind):
            return {'items': [{'kind': kind, 'key': 'home', 'id': 'resource-id', 'name': 'Home'}]}

        def export_resource(self, kind, resource_id, destination, *, include_dependencies):
            operations.append(('export', kind, resource_id, include_dependencies))
            destination.write_bytes(b'archive')
            return destination

        def preview_import(self, package):
            operations.append(('preview', package.read_bytes()))
            return {'source_kind': 'exp', 'resources': [{'kind': 'map', 'key': 'home', 'name': 'Home', 'status': 'new'}]}

        def preview_export(self, kind, resource_id, *, include_dependencies):
            operations.append(('export-preview', kind, resource_id, include_dependencies))
            return {'roots': [{'kind': 'skill', 'key': 'main'}], 'resources': [{'kind': 'skill', 'key': 'main', 'skill_kind': 'brain'}]}

        def import_resources(self, package, *, selections, include_dependencies):
            operations.append(('import', selections, include_dependencies))
            return {'imported': [{'kind': 'map', 'key': 'home', 'id': 'new-id'}], 'reused': [], 'pending_dependencies': []}

    monkeypatch.setattr(resource_exchange, 'ResourceExchangeService', Service)
    ctx = SimpleNamespace(database=object(), asset_service=SimpleNamespace(store=object()), package_root=tmp_path / 'packages')
    router = APIRouter(prefix='/api/studio')
    resource_exchange.install_routes(router, ctx)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, operations, tmp_path


def _preview(client):
    return client.post('/api/studio/resource-exchange/preview', data={'kind': 'map'}, files={'file': ('experiment.gaexp', b'archive', 'application/zip')})


def test_import_uses_the_previewed_package_and_is_idempotent(exchange_client):
    client, operations, root = exchange_client
    preview = _preview(client)
    assert preview.status_code == 200
    body = {'token': preview.json()['token'], 'selected': [{'kind': 'map', 'key': 'home'}]}
    first = client.post('/api/studio/resource-exchange/imports', json=body)
    retry = client.post('/api/studio/resource-exchange/imports', json=body)
    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()
    assert len([item for item in operations if item[0] == 'import']) == 1
    assert not list((root / 'resource-exchange').glob('preview-*/upload.zip'))
    body['selected'] = [{'kind': 'skill', 'key': 'changed'}]
    assert client.post('/api/studio/resource-exchange/imports', json=body).status_code == 409


def test_modified_upload_cannot_be_imported_after_preview(exchange_client):
    client, operations, root = exchange_client
    preview = _preview(client).json()
    next((root / 'resource-exchange').glob('preview-*/upload.zip')).write_bytes(b'changed')
    result = client.post('/api/studio/resource-exchange/imports', json={'token': preview['token'], 'selected': [{'kind': 'map', 'key': 'home'}]})
    assert result.status_code == 409
    assert not any(item[0] == 'import' for item in operations)


def test_export_is_downloaded_and_temporary_archive_is_removed(exchange_client):
    client, operations, root = exchange_client
    result = client.get('/api/studio/resource-exchange/export/brain/brain-id?include_dependencies=true')
    assert result.status_code == 200
    assert result.content == b'archive'
    assert result.headers['content-type'] == 'application/zip'
    assert operations == [('export', 'brain', 'brain-id', True)]
    assert not list((root / 'resource-exchange').glob('export-*'))


def test_unknown_token_and_oversized_upload_do_not_reach_studio(exchange_client, monkeypatch):
    client, operations, root = exchange_client
    result = client.post('/api/studio/resource-exchange/imports', json={'token': '0' * 32, 'selected': [{'kind': 'map', 'key': 'home'}]})
    assert result.status_code == 410
    monkeypatch.setattr(resource_exchange, 'MAX_UPLOAD_BYTES', 4)
    assert _preview(client).status_code == 413
    assert operations == []
    assert not list((root / 'resource-exchange').glob('preview-*'))


def test_export_preview_and_default_import_policy_reach_studio(exchange_client):
    client, operations, _ = exchange_client
    preview = client.get('/api/studio/resource-exchange/export-preview/brain/main')
    assert preview.status_code == 200 and preview.json()['resources'][0]['skill_kind'] == 'brain'
    assert operations[-1] == ('export-preview', 'brain', 'main', False)
    token = _preview(client).json()['token']
    response = client.post('/api/studio/resource-exchange/imports', json={'token': token, 'selected': [{'kind': 'crowd', 'key': 'class'}]})
    assert response.status_code == 200
    assert operations[-1] == ('import', [{'kind': 'crowd', 'key': 'class'}], None)


def test_malformed_zip_returns_a_visible_validation_error_and_removes_upload(exchange_client, monkeypatch):
    client, _, root = exchange_client
    def malformed(*_):
        raise BadZipFile('invalid central directory')
    monkeypatch.setattr(resource_exchange.ResourceExchangeService, 'preview_import', malformed)
    result = _preview(client)
    assert result.status_code == 422
    assert 'ZIP' in result.json()['detail']
    assert not list((root / 'resource-exchange').glob('preview-*'))


def test_discarding_preview_releases_upload_and_capacity(exchange_client, monkeypatch):
    client, _, root = exchange_client
    monkeypatch.setattr(resource_exchange, 'MAX_PENDING_PREVIEWS', 1)
    token = _preview(client).json()['token']
    assert _preview(client).status_code == 429
    assert client.delete('/api/studio/resource-exchange/preview/' + token).status_code == 204
    assert not list((root / 'resource-exchange').glob('preview-*'))
    assert client.post('/api/studio/resource-exchange/imports', json={
        'token': token, 'selected': [{'kind': 'map', 'key': 'home'}]}).status_code == 410
    assert _preview(client).status_code == 200


def test_discarding_claimed_preview_waits_until_import_finishes(exchange_client, monkeypatch):
    client, _, root = exchange_client
    token = _preview(client).json()['token']
    claimed, finish = Event(), Event()
    def blocked_import(self, package, *, selections, include_dependencies):
        claimed.set()
        assert finish.wait(5)
        assert package.read_bytes() == b'archive'
        return {'imported': [{'kind': 'map', 'key': 'home'}], 'reused': [], 'pending_dependencies': []}
    monkeypatch.setattr(resource_exchange.ResourceExchangeService, 'import_resources', blocked_import)
    with ThreadPoolExecutor(2) as pool:
        importing = pool.submit(client.post, '/api/studio/resource-exchange/imports', json={
            'token': token, 'selected': [{'kind': 'map', 'key': 'home'}]})
        assert claimed.wait(5)
        try:
            assert client.delete('/api/studio/resource-exchange/preview/' + token).status_code == 202
            assert list((root / 'resource-exchange').glob('preview-*/upload.zip'))
        finally:
            finish.set()
        assert importing.result(timeout=5).status_code == 200
    assert not list((root / 'resource-exchange').glob('preview-*'))
