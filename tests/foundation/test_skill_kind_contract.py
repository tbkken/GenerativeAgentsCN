"""Unified Skill composition and the separate Brain entrypoint contract."""
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.schema import CreateIndex, CreateTable

from generative_agents.adapters.web.routes.resources import SkillCreate
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.resources import (
    ResourceRecord, ResourceSet, read_resource_set, validate_resource_set,
)
from generative_agents.ga_protocol.skills.documents import (
    SkillRegistry, SkillRegistryError, SnapshotSkillRegistry,
)
from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder, SkillSource
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.resources.exchange import ResourceExchangeService
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.storage.assets import AssetStore
from generative_agents.ga_studio.storage.database import create_database, upgrade_database
from generative_agents.ga_studio.storage.models import StudioSkill
from generative_agents.ga_studio.storage.schema import prepare_studio_database
from tests.test_portable_package_protocol import _definition


def markdown(key, body="完成任务并返回结果。"):
    return f"---\nname: {key}\ndescription: {key}\n---\n\n{body}\n"


@pytest.mark.parametrize("kind", ["atomic", "pack"])
def test_removed_skill_kinds_are_rejected_at_all_authoring_boundaries(database, tmp_path, kind):
    with pytest.raises(ValidationError):
        SkillCreate(name="removed-kind", description="obsolete", kind=kind)
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    with pytest.raises(SkillRegistryError, match="Unsupported Skill kind"):
        registry.create(name="removed-kind", description="obsolete", kind=kind)
    with pytest.raises(SkillRegistryError, match="Unsupported Skill kind"):
        registry.list_summaries(kind=kind)
    with pytest.raises(SkillRegistryError, match="Unsupported Skill kind"):
        SkillRegistry(tmp_path)._parse(markdown("removed-kind"), Path("removed-kind/SKILL.md"), kind,
                                      file_contents={})
    with pytest.raises(SkillRegistryError, match="Unsupported Skill kind"):
        SnapshotSkillRegistry({"removed-kind": {"kind": kind, "markdown": markdown("removed-kind")}},
                              root=tmp_path / "snapshot")
    path = "skills/items/removed-kind/SKILL.md"
    resources = ResourceSet(resources=[ResourceRecord(kind="skill", key="removed-kind", name="removed-kind",
        definition={"skill_kind": kind, "entrypoint": path}, attachments=[path])],
        files={path: markdown("removed-kind").encode()})
    with pytest.raises(PackageError, match="invalid Skill type"):
        validate_resource_set(resources)


def test_default_skill_composes_children_and_roundtrips_complete_closure(database, tmp_path):
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    child = registry.create(name="observe", description="观察环境")
    parent = registry.create(name="decide", description="组织判断")
    registry.save(parent.name, markdown(parent.name, "调用 $observe，以返回的事实组织判断。"))
    assert child.kind == parent.kind == SkillCreate(name="new", description="new").kind == "skill"
    assert registry.get(parent.name).children == (child.name,)
    assert registry.dependencies(parent.name)["skills"][0]["name"] == child.name
    assert registry.list_summaries(kind="skill")["counts"] == {"skill": 2, "brain": 0}
    assert registry.get(parent.name).path.parts[-3:] == ("skills", parent.name, "SKILL.md")
    source = ResourceExchangeService(database, asset_store=AssetStore(tmp_path / "source"))
    archive = source.export_resource("skill", parent.name, tmp_path / "skill.zip")
    resources = read_resource_set(archive)
    assert {record.key for record in resources.resources} == {parent.name, child.name}
    assert {record.definition["skill_kind"] for record in resources.resources} == {"skill"}
    assert set(resources.files) == {f"skills/items/{key}/SKILL.md" for key in (parent.name, child.name)}
    url = f"sqlite:///{(tmp_path / 'target.sqlite').as_posix()}"
    upgrade_database(url)
    target_database = create_database(url)
    try:
        target = ResourceExchangeService(target_database, asset_store=AssetStore(tmp_path / "target"))
        target.import_resources(archive, [{"kind": "skill", "key": parent.name}])
        imported = DatabaseSkillRegistry(target_database, cache_root=tmp_path / "target-cache")
        assert imported.get(parent.name).children == (child.name,)
        assert imported.get(child.name).kind == "skill"
        snapshot = SnapshotSkillRegistry(imported.snapshot([parent.name]), root=tmp_path / "snapshot")
        assert snapshot.get(parent.name).children == (child.name,)
        assert snapshot.get(child.name).path.parts[-3:] == ("skills", child.name, "SKILL.md")
        reexport = target.export_resource("skill", parent.name, tmp_path / "return.zip")
        assert all(item["status"] == "reuse" for item in source.preview_import(reexport)["resources"])
    finally:
        target_database.close()


def test_brain_remains_the_single_experiment_entrypoint(tmp_path):
    sources = []
    for key, kind, body in (("test-brain", "brain", "调用 $decide。"),
                            ("decide", "skill", "调用 $observe。"),
                            ("observe", "skill", "读取 @world-perceive 并返回观察。")):
        path = tmp_path / "author" / f"{kind}s" / key / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(markdown(key, body), encoding="utf-8")
        sources.append(SkillSource(key, "brain" if kind == "brain" else "sub_skill", path, editor_kind=kind))
    registry = SkillRegistry(tmp_path / "author")
    assert {item.kind for item in registry.list()} == {"skill", "brain"}
    assert registry.get("test-brain").path.parts[-3:] == ("brains", "test-brain", "SKILL.md")
    builder = ExperimentPackageBuilder()
    archive = builder.build_archive(tmp_path / "experiment.gaexp", staging_directory=tmp_path / "experiment",
        definition=_definition(), skills=sources, brain_skill="test-brain")
    resources = read_resource_set(archive)
    assert {item.key for item in resources.resources if item.kind == "skill"} == {"test-brain", "decide", "observe"}
    invalid = [SkillSource(item.skill_id, item.kind, item.skill_file, editor_kind="skill") for item in sources]
    with pytest.raises(PackageError, match="Brain selection must identify"):
        builder.build_directory(tmp_path / "invalid", definition=_definition(), skills=invalid, brain_skill="test-brain")
    extra_brain = [sources[0], SkillSource("decide", "sub_skill", sources[1].skill_file, editor_kind="brain"), sources[2]]
    with pytest.raises(PackageError, match="exactly one configured Brain"):
        builder.build_directory(tmp_path / "extra-brain", definition=_definition(), skills=extra_brain, brain_skill="test-brain")


def install_previous_empty_skill_constraint(database):
    table = StudioSkill.__table__
    with database.engine.begin() as connection:
        table.drop(connection)
        ddl = str(CreateTable(table).compile(database.engine)).replace("('skill','brain')", "('atomic','pack','brain')")
        connection.exec_driver_sql(ddl)
        for index in table.indexes:
            connection.execute(CreateIndex(index))


def test_empty_skill_constraint_refresh_preserves_other_public_resources(database, database_url, tmp_path):
    service = StudioResourceService(database)
    person = service.create_agent({"agent_key": "keep-me", "name": "保留", "scratch": {"age": 20}})
    install_previous_empty_skill_constraint(database)
    result = prepare_studio_database(database_url, backup_dir=tmp_path / "must-not-exist")
    assert not result.rebuilt and not (tmp_path / "must-not-exist").exists()
    assert service.get_agent(person["id"])["agent_key"] == "keep-me"
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    assert registry.create(name="new-skill", description="新技能").kind == "skill"
    assert {index["name"] for index in inspect(database.engine).get_indexes("studio_skills")} == {
        "ix_studio_skills_archived", "ix_studio_skills_kind_updated"}


def test_populated_incompatible_skill_table_is_not_changed(database, database_url, tmp_path):
    person = StudioResourceService(database).create_agent({"agent_key": "keep-me", "name": "保留", "scratch": {"age": 20}})
    install_previous_empty_skill_constraint(database)
    with database.session_factory.begin() as session:
        session.add(StudioSkill(skill_key="keep-skill", description="旧内容", kind="pack", markdown=markdown("keep-skill"),
                                content_hash="a" * 64))
    with pytest.raises(RuntimeError, match="must be empty"):
        prepare_studio_database(database_url, backup_dir=tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()
    with database.session_factory() as session:
        row = session.scalar(select(StudioSkill).where(StudioSkill.skill_key == "keep-skill"))
        assert row.kind == "pack" and row.markdown == markdown("keep-skill")
    assert StudioResourceService(database).get_agent(person["id"])["agent_key"] == "keep-me"


def test_failed_empty_table_refresh_rolls_back_ddl(database, database_url, monkeypatch):
    install_previous_empty_skill_constraint(database)
    before = inspect(database.engine).get_check_constraints("studio_skills")
    def fail_create(*args, **kwargs):
        raise RuntimeError("injected DDL failure")
    monkeypatch.setattr(StudioSkill.__table__, "create", fail_create)
    with pytest.raises(RuntimeError, match="injected DDL failure"):
        upgrade_database(database_url)
    assert inspect(database.engine).get_check_constraints("studio_skills") == before
    assert {index["name"] for index in inspect(database.engine).get_indexes("studio_skills")} == {
        "ix_studio_skills_archived", "ix_studio_skills_kind_updated"}
