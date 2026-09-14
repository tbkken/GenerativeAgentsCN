"""Disposable author catalogs with enough entries to exercise five-item paging."""
from tests.frontend.experiment_workspace_fixture import *  # noqa: F403

database = create_database(database_url)
resources = StudioResourceService(database)
skills = DatabaseSkillRegistry(database, cache_root=var / 'skills')
for index in range(1, 8):
    with database.session_factory.begin() as session:
        session.add(WorldMap(map_key=f'list-map-{index}', name=f'列表验收地图 {index}',
                             world_json=world, world_hash='0' * 64))
    member = resources.create_agent({'agent_key': f'list-agent-{index}', 'name': f'列表验收智能体 {index}',
                                     'scratch': {'age': 20 + index}, 'currently': '检查统一列表与编辑返回。'})
    resources.create_crowd(name=f'列表验收人群 {index}', agent_ids=[member['id']])
    skills.create(name=f'list-skill-{index}', description=f'列表验收技能 {index}', kind='atomic' if index % 2 else 'pack')
    skills.create(name=f'list-brain-{index}', description=f'列表验收大脑 {index}', kind='brain')
    resources.create_model_preset(name=f'列表验收模型 {index}', config={
        'chat': {'provider': 'vllm', 'model': f'chat-{index}', 'base_url': 'http://127.0.0.1:9999/v1'},
        'embedding': {'provider': 'openai_compatible', 'model': f'embedding-{index}', 'base_url': 'http://127.0.0.1:9999/v1'},
    })
    workspace.duplicate(created['experiment_id'])
database.close()
