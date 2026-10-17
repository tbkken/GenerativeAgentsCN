"""Human Skill labels roundtrip while references and execution retain stable keys."""
import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from generative_agents.adapters.web.routes.resources import create_resource_router
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.resources import read_resource_set, validate_resource_set
from generative_agents.ga_protocol.skills.documents import SkillRegistryError, SnapshotSkillRegistry
from generative_agents.ga_protocol.skills.dependencies import referenced_interfaces, referenced_skills
from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder
from generative_agents.ga_studio.resources.exchange import ResourceExchangeService
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.storage.assets import AssetStore
from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.models import Base
from tests.test_portable_package_protocol import _definition


@pytest.mark.parametrize("newline,bom", [("\n", ""), ("\r\n", "\ufeff")])
def test_create_save_reopen_and_search_display_name_without_changing_key(database, tmp_path, newline, bom):
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    app = FastAPI()
    app.include_router(create_resource_router(database, registry))
    with TestClient(app) as client:
        base = "/api/studio/resources/skills"
        created = client.post(base, json={"name": "schedule-daily", "display_name": "制定全天日程",
                                         "description": "根据今日情境安排活动"})
        assert created.status_code == 201, created.text
        first = created.json()
        assert first["name"] == "schedule-daily" and first["display_name"] == "制定全天日程"
        assert "# 制定全天日程" in first["markdown"]
        changed = bom + first["markdown"].replace("制定全天日程", "安排一天生活").replace("\n", newline)
        changed += newline + "正文含 display_name: 仅正文可见字样，不应成为列表搜索元数据。"
        saved = client.put(f"{base}/schedule-daily", json={"markdown": changed, "scripts": {}})
        assert saved.status_code == 200, saved.text
        assert saved.json()["display_name"] == "安排一天生活"
        assert saved.json()["content_hash"] != first["content_hash"]
        reopened = client.get(f"{base}/schedule-daily").json()
        assert reopened["display_name"] == "安排一天生活" and reopened["name"] == "schedule-daily"
        matches = client.get(base, params={"q": "安排一天生活"}).json()
        assert matches["total"] == 1 and matches["items"][0]["display_name"] == "安排一天生活"
        assert client.get(base, params={"q": "制定全天日程"}).json()["total"] == 0
        assert client.get(base, params={"q": "仅正文可见字样"}).json()["total"] == 0
        assert client.get(base, params={"q": "schedule-daily"}).json()["total"] == 1
        lineage = client.get(f"{base}/schedule-daily/dependencies").json()
        assert lineage["skill"] == "schedule-daily" and lineage["display_name"] == "安排一天生活"
    exchange = ResourceExchangeService(database, asset_store=AssetStore(tmp_path / "assets"))
    catalog = exchange.catalog("skill", query="安排一天生活")
    assert catalog["items"][0]["key"] == "schedule-daily"
    assert catalog["items"][0]["name"] == "安排一天生活"
    assert exchange.catalog("skill", query="仅正文可见字样")["total"] == 0


def test_chinese_labels_survive_complete_closure_exchange_and_experiment_copy(database, tmp_path):
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    child = registry.create(name="observe", display_name="观察周围环境", description="根据感知返回事实")
    brain = registry.create(name="daily-brain", display_name="小镇生活大脑", description="组织生活行为", kind="brain")
    registry.save(brain.name, brain.markdown + "\n调用 $observe，再通过 @world-act 提交本轮动作。")
    source = ResourceExchangeService(database, asset_store=AssetStore(tmp_path / "source"))
    exported = source.export_resource("brain", brain.name, tmp_path / "brain.zip")
    resources = read_resource_set(exported)
    labels = {item.key: item.name for item in resources.resources}
    assert labels == {brain.name: brain.display_name, child.name: child.display_name}
    assert source.preview_export("brain", brain.name)["resources"][0]["name"] == brain.display_name
    target_db = create_database(f"sqlite:///{(tmp_path / 'other.sqlite').as_posix()}")
    Base.metadata.create_all(target_db.engine)
    try:
        target = ResourceExchangeService(target_db, asset_store=AssetStore(tmp_path / "target"))
        target.import_resources(exported, [{"kind": "skill", "key": brain.name}])
        imported = DatabaseSkillRegistry(target_db, cache_root=tmp_path / "imported")
        graph = imported.dependencies(brain.name)
        assert graph["display_name"] == brain.display_name
        assert graph["skills"][0]["name"] == child.name
        assert graph["skills"][0]["display_name"] == child.display_name
        snapshot = imported.snapshot([brain.name])
        assert snapshot[child.name]["display_name"] == child.display_name
        detached = SnapshotSkillRegistry(snapshot, root=tmp_path / "runtime-copy")
        assert detached.get(brain.name).children == (child.name,)
        assert detached.get(child.name).display_name == child.display_name
        returned = target.export_resource("brain", brain.name, tmp_path / "return.zip")
        assert all(item["status"] == "reuse" for item in source.preview_import(returned)["resources"])
        experiment = ExperimentPackageBuilder().build_directory(tmp_path / "experiment",
            definition=_definition(), skills=[], brain_skill=brain.name, resource_set=resources)
        registry.save(child.name, child.markdown.replace(child.display_name, "公共资源改名"))
        copied = read_resource_set(experiment)
        assert {item.key: item.name for item in copied.resources if item.kind == "skill"} == labels
        assert copied.files[f"skills/items/{child.name}/SKILL.md"] == child.markdown.encode()
    finally:
        target_db.close()


def test_package_rejects_display_name_disagreement(database, tmp_path):
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    created = registry.create(name="observe", display_name="观察环境", description="观察")
    exchange = ResourceExchangeService(database, asset_store=AssetStore(tmp_path / "assets"))
    resources = copy.deepcopy(exchange.resource_set("skill", created.name))
    resources.resources[0].name = "矛盾名称"
    with pytest.raises(PackageError, match="display_name must match"):
        validate_resource_set(resources)


@pytest.mark.parametrize("label", ["", " ", "一" * 201, "两行\n名称"])
def test_invalid_display_name_cannot_create_resource(database, tmp_path, label):
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    with pytest.raises(SkillRegistryError, match="display_name"):
        registry.create(name="invalid", display_name=label, description="有效说明")
    assert registry.list_summaries()["total"] == 0


def test_omitted_display_name_is_a_valid_unlabelled_skill(database, tmp_path):
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    created = registry.create(name="unlabelled", description="无单独显示名")
    saved = registry.save(created.name, "---\nname: unlabelled\ndescription: 无单独显示名\n---\n完成任务。")
    assert saved.display_name == "unlabelled"
    assert registry.list_summaries()["items"][0]["display_name"] == "unlabelled"


def test_display_label_is_metadata_and_never_grants_tool_or_child_access():
    markdown = ('\ufeff---\r\nname: labelled\r\ndisplay_name: "展示 $fake-child 和 @world-act"\r\n'
                'description: "演示元数据"\r\n---\r\n仅调用 @world-perceive。')
    assert referenced_skills(markdown) == []
    assert referenced_interfaces(markdown) == ["world-perceive"]
