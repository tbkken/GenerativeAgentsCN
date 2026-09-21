"""Dependency cards identify concrete tools available to simulation Skills."""

import pytest

from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import Base
from generative_agents.ga_runtime.capabilities.server import SimulationMCPServer
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_protocol.skills.dependencies import SIMULATION_MCP_TOOLS
from generative_agents.ga_protocol.skills.dependencies import referenced_mcp_tools
from tests.skill_files import write_skill
from generative_agents.ga_protocol.skills.documents import SkillRegistry


def test_dependency_catalog_matches_runtime_registration():
    server = SimulationMCPServer(None, None, memory_stream=object())
    assert set(SIMULATION_MCP_TOOLS) == {tool['name'] for tool in server.tools()}


def test_tool_references_are_unique_exact_names():
    assert referenced_mcp_tools(
        '调用world-perceive，然后 `world-act`；再次 world-perceive。\n'
        'memory-stream-searching x-world-navigate world-act-extra memory-stream unknown-tool'
    ) == ['world-perceive', 'world-act']
    assert referenced_mcp_tools('普通自然语言 SOP，没有工具调用。') == []


@pytest.mark.parametrize('storage', ['file', 'database'])
def test_saved_brain_dependencies_reload_all_referenced_tools(tmp_path, storage):
    database = create_database(f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    try:
        registry = (
            DatabaseSkillRegistry(database, cache_root=tmp_path / 'cache')
            if storage == 'database'
            else SkillRegistry(root=tmp_path / 'skills')
        )
        brain = (registry.create(name='dependency-brain', description='测试依赖', kind='brain') if storage == 'database' else write_skill(registry, 'dependency-brain', '测试依赖', kind='brain'))
        markdown = brain.markdown + '\n' + '\n'.join(f'调用 `{name}`。' for name in SIMULATION_MCP_TOOLS)
        if storage == 'database':
            registry.save(brain.name, markdown)
        else:
            brain.path.write_text(markdown, encoding='utf-8')
        dependencies = registry.dependencies(brain.name)
        assert dependencies['mcp'] == list(SIMULATION_MCP_TOOLS)
        assert dependencies['skills'] == []
        assert dependencies['scripts'] == []
        changed = brain.markdown + '\n仅调用 `world-perceive`。'
        if storage == 'database':
            registry.save(brain.name, changed)
        else:
            brain.path.write_text(changed, encoding='utf-8')
        assert registry.dependencies(brain.name)['mcp'] == ['world-perceive']
    finally:
        database.close()
