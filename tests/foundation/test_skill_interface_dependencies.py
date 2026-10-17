"""Skill lineage shares explicit references and the runtime interface catalog."""

import pytest

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.resources import ResourceRecord, ResourceSet, validate_resource_set
from generative_agents.ga_protocol.skills.dependencies import (
    SIMULATION_INTERFACES, referenced_interfaces, referenced_skills, validate_interface_references,
)
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_runtime.capabilities.server import SimulationAPI
from generative_agents.ga_runtime.skills.objects import ObjectAPI
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import Base
from tests.skill_files import write_skill


def test_dependency_catalog_matches_runtime_registration():
    api = SimulationAPI(None, None, memory_stream=object())
    assert tuple(item['name'] for item in api.descriptors()) == SIMULATION_INTERFACES
    assert len(SIMULATION_INTERFACES) == 7


def test_explicit_interface_references_preserve_unknown_names_and_ignore_metadata():
    markdown = '''---
name: dependency-brain
description: example @world-act
---
调用@world-perceive，然后 `@world-act`；再次 @world-perceive。
world-navigate memory-stream-search name@world-act.com /@world-act @@world-act
@world-perceive-extra @world_act @World-act @unknown
'''
    assert referenced_interfaces(markdown) == [
        'world-perceive', 'world-act', 'world-perceive-extra', 'world_act', 'World-act', 'unknown',
    ]
    with pytest.raises(ValueError, match='@unknown'):
        validate_interface_references(markdown)
    validate_interface_references('先调用 @world-perceive，再调用 @world-act。')
    assert referenced_interfaces('普通自然语言 world-perceive，没有显式接口引用。') == []


def test_child_references_are_exact_and_self_references_remain_visible():
    assert referenced_skills('调用$self-skill，再调用 $child-skill。 $child-skill_extra /$hidden $$hidden') == [
        'self-skill', 'child-skill',
    ]


@pytest.fixture(params=['file', 'database'])
def registry(request, tmp_path):
    database = create_database(f"sqlite:///{(tmp_path / 'studio.db').as_posix()}")
    Base.metadata.create_all(database.engine)
    instance = (DatabaseSkillRegistry(database, cache_root=tmp_path / 'cache')
                if request.param == 'database' else SkillRegistry(root=tmp_path / 'skills'))
    yield instance
    database.close()


def save(registry, name, body, *, kind='skill', scripts=None):
    if isinstance(registry, DatabaseSkillRegistry):
        document = registry.create(name=name, description='依赖测试', kind=kind)
        return registry.save(name, document.markdown + '\n' + body, scripts=scripts or {})
    document = write_skill(registry, name, '依赖测试', kind=kind, body=body)
    for relative, source in (scripts or {}).items():
        target = document.path.parent / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding='utf-8')
    return registry.get(name)


def test_saved_lineage_is_recursive_and_enumerates_real_scripts(registry):
    save(registry, 'leaf', '调用 @memory-stream-search。正文提到 scripts/nonexistent.py。',
         scripts={'scripts/real.py': "print('real')"})
    save(registry, 'middle', '使用 $leaf，再 @world-perceive。', kind='skill')
    brain = save(registry, 'dependency-brain', '使用 $middle。然后 @world-act。', kind='brain')
    lineage = registry.dependencies(brain.name)
    assert lineage['interfaces'] == ['world-act']
    assert lineage['scripts'] == []
    assert lineage['diagnostics'] == []
    middle, = lineage['skills']
    assert middle['name'] == 'middle' and middle['interfaces'] == ['world-perceive']
    leaf, = middle['skills']
    assert leaf['interfaces'] == ['memory-stream-search']
    assert leaf['scripts'] == ['scripts/real.py']
    assert [item['name'] for item in lineage['interface_catalog']] == list(SIMULATION_INTERFACES)
    assert all(item['input_schema']['type'] == 'object' for item in lineage['interface_catalog'])
    object_interfaces = {item['name']: item for item in ObjectAPI(None, None, binding=None, memory_stream=object()).descriptors()}
    for item in lineage['interface_catalog']:
        assert item['display_name'] and item['description_zh']
        assert item['root_only'] == (item['name'] == 'world-act')
        assert item['object_available'] == (item['name'] in object_interfaces)
        if item['name'] in object_interfaces:
            assert item['object_description_zh']
            assert item['object_input_schema'] == object_interfaces[item['name']]['parameters']
            assert item['object_description'] == object_interfaces[item['name']]['description']
        else:
            assert 'object_input_schema' not in item
    assert 'mcp' not in lineage


def test_missing_cycles_and_unknown_interfaces_are_visible(registry):
    save(registry, 'child', '执行 $dependency-brain，再调用 @unknown-api。')
    save(registry, 'dependency-brain', '使用 $child 和 $missing-child。', kind='brain')
    lineage = registry.dependencies('dependency-brain')
    assert lineage['skills'][0]['skills'][0]['cycle'] is True
    assert lineage['skills'][1]['missing'] is True
    assert {item['code'] for item in lineage['diagnostics']} == {
        'SKILL_DEPENDENCY_MISSING', 'SKILL_DEPENDENCY_CYCLE', 'SKILL_INTERFACE_UNKNOWN',
    }
    assert next(item for item in lineage['diagnostics'] if item['code'] == 'SKILL_DEPENDENCY_CYCLE')['path'] == [
        'dependency-brain', 'child', 'dependency-brain',
    ]


def test_interface_changes_reload_from_saved_markdown(registry):
    document = save(registry, 'dependency-brain', '使用 @world-perceive。', kind='brain')
    assert registry.dependencies(document.name)['interfaces'] == ['world-perceive']
    changed = document.markdown.replace('@world-perceive', '@world-act')
    if isinstance(registry, DatabaseSkillRegistry):
        registry.save(document.name, changed)
    else:
        document.path.write_text(changed, encoding='utf-8')
    assert registry.dependencies(document.name)['interfaces'] == ['world-act']


@pytest.mark.parametrize('require_dependencies', [False, True])
def test_portable_resources_reject_unknown_interfaces_before_sealing(require_dependencies):
    path = 'skills/items/unknown-brain/SKILL.md'
    resources = ResourceSet([
        ResourceRecord(kind='skill', key='unknown-brain', name='unknown-brain',
                       definition={'skill_kind': 'brain', 'entrypoint': path}, attachments=[path]),
    ], {path: b'---\nname: unknown-brain\ndescription: test\n---\nCall @world-act-typo.\n'})
    with pytest.raises(PackageError, match='Unknown simulation interface reference: @world-act-typo'):
        validate_resource_set(resources, require_dependencies=require_dependencies)
