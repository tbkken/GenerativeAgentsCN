import copy
import hashlib

import pytest

from generative_agents.ga_studio.resources import StudioResourceError
from generative_agents.ga_studio.workspace import ExperimentWorkspaceService
from generative_agents.persistence.models import Asset


@pytest.fixture
def uploaded(database, tmp_path):
    content = b'uploaded-map-image'
    digest = hashlib.sha256(content).hexdigest()
    with database.session_factory.begin() as session:
        asset = Asset(sha256=digest, logical_name='campus.jpg', media_type='image/jpeg',
                      size_bytes=len(content), relative_path='unused', content_blob=content)
        session.add(asset)
        session.flush()
        asset_id = asset.id
    source = dict(id='source', name='campus', kind='UPLOADED', asset_id=asset_id, asset_hash=digest)
    world = {'assets': [], 'definition': {'editor_v2': {'material_sources': [source]}}}
    service = ExperimentWorkspaceService(database, package_root=tmp_path/'packages', var_dir=tmp_path)
    return service, world, content


def test_uploads_without_manifest_are_registered_copied_and_detached(database, uploaded, tmp_path):
    service, original, content = uploaded
    world = copy.deepcopy(original)
    world['definition']['editor_v2']['material_sources'].append(dict(world['definition']['editor_v2']['material_sources'][0], id='duplicate'))
    with database.session_factory() as session:
        assets = service._world_assets(session, world)
    assert len(assets) == len(world['assets']) == 1
    service._replace_uploaded_world_asset_references(world)
    service.builder._copy_assets(tmp_path/'experiment', assets)
    for source in world['definition']['editor_v2']['material_sources']:
        assert source['kind'] == 'BUNDLED'
        assert 'asset_id' not in source
        assert (tmp_path/'experiment'/source['bundled_path']).read_bytes() == content
    assert original['assets'] == []
    assert original['definition']['editor_v2']['material_sources'][0]['kind'] == 'UPLOADED'


@pytest.mark.parametrize('failure', ['missing', 'wrong_hash', 'corrupt_bytes'])
def test_invalid_uploaded_asset_fails_before_package_copy(database, uploaded, failure):
    service, world, _ = uploaded
    source = world['definition']['editor_v2']['material_sources'][0]
    if failure == 'missing':
        source['asset_id'] = 'missing'
    elif failure == 'wrong_hash':
        source['asset_hash'] = 'a' * 64
    else:
        with database.session_factory.begin() as session:
            session.get(Asset, source['asset_id']).content_blob = b'corrupt'
    with database.session_factory() as session, pytest.raises(StudioResourceError):
        service._world_assets(session, world)


def test_existing_manifest_path_is_preserved(database, uploaded):
    service, world, content = uploaded
    digest = hashlib.sha256(content).hexdigest()
    world['assets'] = [{'logical_path':'assets/custom/original.jpg', 'asset_hash':f'sha256:{digest}',
                        'media_type':'image/jpeg', 'size':len(content)}]
    with database.session_factory() as session:
        assert service._world_assets(session, world) == {'assets/custom/original.jpg':content}
    assert len(world['assets']) == 1
