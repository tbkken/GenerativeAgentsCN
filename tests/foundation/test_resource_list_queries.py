from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import Base
from generative_agents.ga_studio.storage.models import WorldMap
from generative_agents.ga_studio.resources.maps import WorldMapService
from generative_agents.ga_studio.resources.maps import normalize_public_world
from tests.foundation.test_navigation import navigation_world


def test_map_description_search_uses_the_same_predicate_for_rows_and_page_totals(tmp_path):
    database = create_database(f"sqlite:///{(tmp_path / 'lists.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    world = normalize_public_world(navigation_world()).model_dump(mode='json')
    with database.session_factory.begin() as session:
        for index in range(7):
            session.add(WorldMap(map_key=f'map-{index}', name=f'地图 {index}',
                                 description='用于河岸阅读观察' if index < 6 else '住宅空间',
                                 world_json=world, world_hash='0' * 64))
    service = WorldMapService(database, skill_registry=object())
    first = service.list_maps(query='河岸阅读', page=1, page_size=5)
    second = service.list_maps(query='河岸阅读', page=2, page_size=5)
    assert first['total'] == second['total'] == 6
    assert len(first['items']) == 5
    assert len(second['items']) == 1
    assert not ({item['id'] for item in first['items']} & {item['id'] for item in second['items']})
    database.close()
