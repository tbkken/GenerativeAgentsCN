"""Demand loading is bounded at the reader, not only at JSON serialization."""
import io
import os
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

from generative_agents.adapters.web.app import create_studio_app
from generative_agents.ga_protocol.packages.io import PackageError, read_json, seal_directory, write_integrity_manifest
from generative_agents.ga_protocol.packages.reading import (
    file_fingerprint, open_readonly_package, read_package_json, validated_experiment,
)
from tests.test_portable_package_protocol import _experiment


def test_selected_zip_member_still_rejects_unsafe_other_members(tmp_path):
    for index, extra in enumerate(('../outside', '/absolute', 'A/../escape', 'manifest.json')):
        path = tmp_path / f'unsafe-{index}.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('manifest.json', '{}')
            archive.writestr(extra, 'bad')
        with pytest.raises((PackageError, ValueError)):
            read_package_json(path, 'manifest.json')
    assert not (tmp_path / 'outside').exists()


def test_readonly_archive_is_reused_and_modified_source_is_not(tmp_path, monkeypatch):
    import generative_agents.ga_protocol.packages.reading as reading
    root = _experiment(tmp_path / 'source')
    archive = seal_directory(root, tmp_path / 'sealed.gaexp')
    calls = []
    extract = reading.extract_archive

    def tracked(*args, **kwargs):
        calls.append(1)
        return extract(*args, **kwargs)

    monkeypatch.setattr(reading, 'extract_archive', tracked)
    with open_readonly_package(archive) as first:
        validated_experiment(first)
    with open_readonly_package(archive) as second:
        assert second == first
        validated_experiment(second)
    assert len(calls) == 1
    replacement = seal_directory(root, tmp_path / 'replacement.gaexp')
    os.replace(replacement, archive)
    with open_readonly_package(archive) as third:
        assert third != first
    assert len(calls) == 2


def test_validation_cache_rejects_same_size_rewrite_with_restored_mtime(tmp_path):
    root = _experiment(tmp_path / 'source')
    validated_experiment(root)
    source = root / 'manifest.json'
    before = source.stat()
    fingerprint = file_fingerprint(source)
    original = source.read_bytes()
    index = original.index(b'Portable')
    changed = original[:index] + b'portable' + original[index + len(b'Portable'):]
    source.write_bytes(changed)
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.stat().st_size == before.st_size
    assert file_fingerprint(source) != fingerprint
    with pytest.raises(PackageError, match='integrity'):
        validated_experiment(root)


def test_overview_and_status_do_not_assemble_world_or_run_history(tmp_path, monkeypatch):
    import generative_agents.ga_protocol.packages.definition as definition
    from generative_agents.ga_studio.api import StudioPackageCatalogService
    var = tmp_path / 'var'
    root = _experiment(var / 'packages')
    identity = read_json(root / 'manifest.json')['experiment']['experiment_id']
    app = create_studio_app(database_url=f'sqlite:///{tmp_path / "studio.db"}', var_dir=var)
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()

        def forbidden(*args, **kwargs):
            raise AssertionError('overview loaded a full world or all catalog rows')

        monkeypatch.setattr(definition, 'assemble_experiment_definition', forbidden)
        monkeypatch.setattr(StudioPackageCatalogService, 'list', forbidden)
        overview = client.get(f'/api/studio/experiments/{identity}')
        overview.raise_for_status()
        assert len(overview.content) < 10_000
        assert 'world' not in overview.json()['definition']
        assert 'definition' not in overview.json()['current_draft']
        client.get('/api/studio/experiments').raise_for_status()
        monkeypatch.setattr(definition, 'read_experiment_documents', forbidden)
        status = client.get(f'/api/studio/experiments/{identity}/status')
        status.raise_for_status()
        assert 'definition' not in status.json()


def test_package_images_support_conditional_preview_and_range(tmp_path):
    from PIL import Image
    var = tmp_path / 'var'
    root = _experiment(var / 'packages')
    image = root / 'assets/test.png'
    image.parent.mkdir(exist_ok=True)
    Image.new('RGB', (1000, 800), '#445566').save(image)
    write_integrity_manifest(root)
    identity = read_json(root / 'manifest.json')['experiment']['experiment_id']
    app = create_studio_app(database_url=f'sqlite:///{tmp_path / "studio.db"}', var_dir=var)
    with TestClient(app) as client:
        client.post('/api/studio/packages/rebuild').raise_for_status()
        url = f'/api/studio/experiments/{identity}/assets/test.png'
        original = client.get(url)
        original.raise_for_status()
        assert original.content == image.read_bytes()
        cached = client.get(url, headers={'If-None-Match': original.headers['etag']})
        assert cached.status_code == 304 and not cached.content
        window = client.get(url, headers={'Range': 'bytes=0-9', 'Accept-Encoding': 'identity'})
        assert window.status_code == 206 and window.content == image.read_bytes()[:10]
        preview = client.get(url + '?width=96')
        preview.raise_for_status()
        assert preview.headers['etag'] != original.headers['etag']
        with Image.open(io.BytesIO(preview.content)) as thumbnail:
            assert max(thumbnail.size) == 96
        assert client.get(url + '?width=999999').status_code == 422


def test_long_operation_returns_before_work_finishes_and_exposes_result(tmp_path, monkeypatch):
    from threading import Event
    from generative_agents.ga_studio.api import StudioPackageCatalogService
    started, release = Event(), Event()

    def slow_rebuild(self, roots):
        started.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr(StudioPackageCatalogService, 'rebuild', slow_rebuild)
    app = create_studio_app(database_url=f'sqlite:///{tmp_path / "studio.db"}', var_dir=tmp_path / 'var')
    try:
        with TestClient(app) as client:
            try:
                accepted = client.post('/api/studio/packages/rebuild', headers={'Prefer': 'respond-async'})
                assert accepted.status_code == 202
                assert started.wait(2)
                status_url = accepted.json()['status_url']
                status = client.get(status_url).json()
                assert status['status'] == 'RUNNING'
                assert client.get('/api/studio/health').json()['status'] == 'ok'
                assert client.delete(status_url).status_code == 409
            finally:
                release.set()
        # Shutdown waits for admitted work, so the persisted result is complete.
        files = list((tmp_path / 'var/studio-operations').glob('*.json'))
        assert len(files) == 1
        result = read_json(files[0])
        assert result['status'] == 'SUCCEEDED'
        assert result['result'] == {'items': []}
    finally:
        release.set()


def test_document_cache_rejects_header_change_before_signature(tmp_path, monkeypatch):
    import generative_agents.ga_protocol.packages.definition as definition
    from generative_agents.ga_protocol.packages.io import atomic_write_json
    root = _experiment(tmp_path / 'source')
    original = definition.file_fingerprint
    count = 0

    def mutate_before_signature(path):
        nonlocal count
        if path == root / 'manifest.json':
            count += 1
            if count == 3:
                header = read_json(path)
                header['experiment']['name'] = 'Changed while reading'
                atomic_write_json(path, header)
        return original(path)

    monkeypatch.setattr(definition, 'file_fingerprint', mutate_before_signature)
    with pytest.raises(PackageError, match='header changed'):
        definition.read_experiment_documents(root)


def test_image_response_keeps_original_snapshot_and_closes_on_send_failure(tmp_path):
    import asyncio
    from starlette.requests import Request
    from generative_agents.adapters.web.media import package_asset_response
    from generative_agents.ga_protocol.packages.io import atomic_write_bytes
    root = _experiment(tmp_path / 'source')
    image = root / 'assets/image.png'
    image.parent.mkdir(exist_ok=True)
    image.write_bytes(b'original-image-content')
    write_integrity_manifest(root)
    request = Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': [], 'query_string': b''})
    response = package_asset_response(root, 'assets/image.png', request)
    atomic_write_bytes(image, b'replacement-content')
    write_integrity_manifest(root)
    closed = []
    close = response._close

    def tracked_close():
        close()
        closed.append(True)

    response._close = tracked_close

    async def verify():
        body = b''.join([chunk async for chunk in response.body_iterator])
        assert body == b'original-image-content'

        async def send(message):
            raise RuntimeError('client disconnected')

        async def receive():
            return {'type': 'http.disconnect'}

        with pytest.raises(RuntimeError, match='client disconnected'):
            await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)

    asyncio.run(verify())
    assert closed == [True]
