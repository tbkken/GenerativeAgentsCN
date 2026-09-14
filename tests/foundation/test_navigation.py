"""Collision authoring, server previews and Agent-bound navigation contracts."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from generative_agents.config.map_editor import MapEditorDocumentV2
from generative_agents.ga_protocol.navigation import collision_preview, grid_path
from generative_agents.ga_studio.builder import build_semantic_index
from generative_agents.ga_studio.resource_api import create_resource_router
from generative_agents.modules.maze import Maze
from generative_agents.runtime.capabilities import SimulationMCPServer
from generative_agents.runtime.iteration import IterationContext
from generative_agents.services.maps import normalize_public_world


def navigation_world():
    width, height = 7, 5
    nodes = []
    for i, (kind, name) in enumerate(zip(
            ['WORLD', 'SECTOR', 'ARENA', 'GAME_OBJECT'], ['Campus', 'Sector', 'Room', 'Floor'])):
        nodes.append(dict(id=name, name=name, kind=kind,
                          parent_id=nodes[-1]['id'] if i else None,
                          bounds=dict(x=0, y=0, width=width, height=height)))
    return dict(world_key='navigation-test', world_name='Campus', assets=[], definition={
        'world': 'Campus', 'size': [height, width], 'tile_size': 32,
        'tile_address_keys': ['world', 'sector', 'arena', 'game_object'],
        'tiles': [dict(coord=[x, y], collision=x == 3, address=['Campus', 'Sector', 'Room', 'Floor'])
                  for y in range(height) for x in range(width)],
        'editor_v2': dict(schema_version='ga-map-editor/v2', root_node_id='Campus',
                          import_metadata=dict(width=width, height=height), hierarchy_nodes=nodes),
    })


def test_save_refresh_preview_and_package_collision_are_consistent(database):
    app = FastAPI(); app.include_router(create_resource_router(database, None))
    with TestClient(app) as client:
        prefix = '/api/studio/resources'
        created = client.post(prefix+'/maps', json={'name': '通路测试', 'width': 7, 'height': 5}).json()
        world = navigation_world()
        body = {'world': world, 'start': [1, 2], 'end': [5, 2]}
        assert client.post(prefix+'/map-editor/navigation', json=body).json()['status'] == 'UNREACHABLE'
        normalized = normalize_public_world(world).model_dump(mode='json')
        normalized['definition']['editor_v2']['navigation']['overrides']['17'] = False
        response = client.put(prefix+'/maps/'+created['id'], json={'world': normalized, 'row_version': created['row_version']})
        assert response.status_code == 200, response.text
        saved = response.json()
        loaded = client.get(prefix+'/maps/'+created['id']).json()['world']
        assert loaded['definition']['editor_v2']['navigation']['overrides']['17'] is False
        body['world'] = loaded
        checked = client.post(prefix+'/map-editor/navigation', json=body).json()
        assert checked['status'] == 'REACHABLE' and checked['distance_tiles'] == 4
        assert [3, 2] in checked['path']
        # Preview never increments the public map row version.
        assert client.get(prefix+'/maps/'+created['id']).json()['row_version'] == saved['row_version']
        packaged, _ = build_semantic_index(loaded)
        assert not next(t for t in packaged['definition']['tiles'] if t['coord'] == [3, 2])['collision']
        # Removing the correction restores the original wall, including after a save.
        del loaded['definition']['editor_v2']['navigation']['overrides']['17']
        closed = normalize_public_world(loaded)
        assert next(t for t in closed.definition['tiles'] if t['coord'] == [3, 2])['collision']
        assert not next(t for t in packaged['definition']['tiles'] if t['coord'] == [3, 2])['collision']


def test_material_rotation_and_map_correction_compose_without_visual_layer_order():
    world = navigation_world(); definition = world['definition']; editor = definition['editor_v2']
    editor['navigation'] = {'base_blocked': [], 'overrides': {}}
    editor['material_sources'] = [dict(id='image', name='wall', kind='GENERATED_COLOR',
        generated_color='#123456', media_type='image/png', width_px=64, height_px=96,
        tile_width=32, tile_height=32, columns=2, rows=3, tile_count=6)]
    editor['material_slices'] = [dict(id='wall', name='wall', source_id='image', kind='STAMP',
        grid_rect=dict(x=0,y=0,width=2,height=3), pixel_rect=dict(x=0,y=0,width=64,height=96),
        rotation_degrees=90, collision_cells={'0': True})]
    editor['hierarchy_nodes'][0]['material_slice_id'] = 'wall'
    normalized = normalize_public_world(world)
    assert collision_preview(normalized.definition)['blocked'] == [2]
    assert collision_preview(normalized.definition, slice_id='wall')['blocked'] == [0]
    normalized.definition['editor_v2']['navigation']['overrides']['2'] = False
    assert collision_preview(normalized.definition)['blocked'] == []
    assert collision_preview(normalized.definition, slice_id='wall')['blocked'] == [0]


@pytest.mark.parametrize('overrides', [{'35': True}, {'-1': False}, {'1': 'false'}])
def test_invalid_navigation_data_fails_before_saving(overrides):
    editor = navigation_world()['definition']['editor_v2']
    editor['navigation'] = dict(base_blocked=[], overrides=overrides)
    with pytest.raises(ValueError):
        MapEditorDocumentV2.model_validate(editor)


def navigation_server():
    maze = Maze.__new__(Maze); maze.width_tiles = 7; maze.height_tiles = 5
    maze.tiles = [[SimpleNamespace(collision=x == 3, spatial_semantics=(),
        get_address=lambda: ['Campus', 'Sector', 'Room']) for x in range(7)] for _ in range(5)]
    maze.address_tiles = {'Campus:Sector:Room': {(5, 2)}}
    agent = SimpleNamespace(coord=(1, 2), percept_config={'vision_r': 5},
                            spatial=SimpleNamespace(tree={'Campus': {'Sector': {'Room': []}}}))
    game = SimpleNamespace(maze=maze, get_agent=lambda key: agent)
    iteration = IterationContext(uuid4(), uuid4(), 'agent-1', 'Agent', 1, 3,
        datetime.now(timezone.utc), 1, (1, 2), ('Campus', 'Sector', 'Outside'))
    return SimulationMCPServer(game, iteration), agent


def unpack(response):
    assert not response['isError'], response
    return json.loads(response['content'][0]['text'])


def test_navigation_is_readonly_and_move_cannot_cross_wall_or_lake():
    server, agent = navigation_server()
    assert not unpack(server.call('world-navigate', {'target_coord': [5, 2]}))['reachable']
    assert server.call('world-act', {'action_type': 'MOVE', 'target_coord': [5, 2]})['isError']
    assert server.action is None and agent.coord == (1, 2)
    server.game.maze.tiles[2][3].collision = False
    result = unpack(server.call('world-navigate', {'target_coord': [5, 2]}))
    assert result == {'reachable': True, 'distance_tiles': 4, 'next_coord': [2, 2], 'movement_required': True,
                      'requested_target': {'target_coord': [5, 2]},
                      'next_coord_role': 'FIRST_PATH_TILE_NOT_DESTINATION'}
    assert server.action is None
    unpack(server.call('world-act', {'action_type': 'MOVE', 'target_coord': [5, 2]}))
    assert server.action.path == ((2, 2), (3, 2), (4, 2), (5, 2))
    assert server.call('world-act', {'action_type': 'WAIT'})['isError']
    assert not server.call('world-navigate', {'target_coord': [5, 2]})['isError']


def test_navigation_identity_visibility_and_remembered_destination():
    server, agent = navigation_server(); agent.percept_config['vision_r'] = 1
    for args in [{'target_coord': [5, 2]}, {'target_coord': [2, 2], 'agent_key': 'other'},
                 {'target_coord': [True, 2]}, {'target_coord': [1.5, 2]}]:
        assert server.call('world-navigate', args)['isError']
    assert not unpack(server.call('world-navigate', {'target_address': ['Campus', 'Sector', 'Room']}))['reachable']
    assert server.call('world-act', {'action_type': 'MOVE', 'target_coord': [2.5, 2]})['isError']
    assert unpack(server.call('world-navigate', {'target_coord': [1, 2]}))['distance_tiles'] == 0
    assert 'world-navigate' in {tool['name'] for tool in server.tools()}


def test_paths_never_cut_diagonal_corners_and_blocked_start_cannot_escape():
    assert not grid_path(2, 2, lambda x,y: (x,y) in {(1,0),(0,1)}, (0,0), (1,1))
    assert not grid_path(2, 2, lambda x,y: (x,y) == (0,0), (0,0), (0,0))
    assert not grid_path(2, 2, lambda x,y: (x,y) == (0,0), (0,0), (1,1))
