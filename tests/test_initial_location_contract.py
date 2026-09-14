"""A package-owned spawn uses its real Arena or Game Object address."""

import copy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from generative_agents.ga_protocol import (
    PackageError, atomic_write_json, open_package, read_json,
    validate_experiment_directory, write_integrity_manifest,
)
from generative_agents.ga_studio import ExperimentPackageBuilder, SkillSource
from generative_agents.ga_studio.resources import StudioAgentDefinition, StudioResourceService
from generative_agents.ga_studio.web import create_studio_app
from generative_agents.ga_studio.workspace import AgentPlacement, ExperimentWorkspaceService
from generative_agents.persistence import create_database
from generative_agents.persistence.models import Base
from tests.test_portable_package_protocol import _definition


ARENA = ["test-world", "test-sector", "test-arena"]
OBJECT = [*ARENA, "test-object"]


def definition_with_floor():
    definition = _definition()
    world = definition["world"]["definition"]
    world["size"] = [2, 3]
    world["tiles"] = [
        dict(coord=[0, 0], collision=False, address=OBJECT),
        dict(coord=[1, 0], collision=False, address=ARENA),
        dict(coord=[2, 0], collision=True, address=ARENA),
        dict(coord=[0, 1], collision=False, address=ARENA),
        dict(coord=[1, 1], collision=False, address=ARENA[:2]),
        dict(coord=[2, 1], collision=False, address=ARENA[:1]),
    ]
    definition["agents"] = [dict(
        agent_key="spawn-test-agent", name="测试角色", coord=[0, 0],
        scratch={"age": 21},
        spatial={"address": {"initial_location": OBJECT},
                 "tree": {ARENA[0]: {ARENA[1]: {ARENA[2]: [OBJECT[3]]}}}},
    )]
    return definition


def build_package(tmp_path, definition=None):
    skill = tmp_path / "author" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("---\nname: test-brain\ndescription: test\n---\nUse world-act.\n", encoding="utf-8")
    root = tmp_path / "var" / "packages" / "experiments" / "spawn-test"
    ExperimentPackageBuilder().build_directory(
        root, definition=definition or definition_with_floor(),
        skills=[SkillSource("test-brain", "brain", skill)], brain_skill="test-brain",
    )
    return root


@pytest.fixture
def workspace_client(tmp_path):
    root = build_package(tmp_path)
    experiment_id = validate_experiment_directory(root).experiment.experiment_id
    app = create_studio_app(
        database_url=f"sqlite:///{(tmp_path / 'studio.sqlite').as_posix()}",
        var_dir=tmp_path / "var",
    )
    with TestClient(app) as client:
        assert client.post("/api/studio/packages/rebuild").status_code == 200
        yield client, f"/api/studio/experiments/{experiment_id}", root


@pytest.mark.parametrize("coord,address", [([1, 0], ARENA), ([0, 0], OBJECT)])
def test_agent_save_reopen_preflight_and_seal_share_actual_address_contract(workspace_client, coord, address):
    client, endpoint, _ = workspace_client
    detail = client.get(endpoint).json()
    definition = detail["definition"]
    agent = definition["agents"][0]
    agent["coord"] = coord
    agent["spatial"]["address"]["initial_location"] = address
    # An Arena's knowledge branch may be empty; no fake object is required.
    agent["spatial"]["tree"] = {ARENA[0]: {ARENA[1]: {ARENA[2]: address[3:]}}}
    saved = client.put(endpoint, json={
        "definition": definition, "expected_content_sha256": detail["content_sha256"],
    })
    assert saved.status_code == 200, saved.text
    reopened = client.get(endpoint).json()["definition"]["agents"][0]
    assert reopened["coord"] == coord
    assert reopened["spatial"] == agent["spatial"]
    preflight = client.post(endpoint + "/validate").json()
    assert preflight["valid"] and preflight["counts"]["blocking"] == 0, preflight
    sealed = client.post(endpoint + "/seal")
    assert sealed.status_code == 200, sealed.text
    assert sealed.json()["status"] == "SEALED"
    with open_package(Path(sealed.json()["location"])) as root:
        validate_experiment_directory(root)
        packaged = read_json(root / "agents/index.json")["agents"][0]
        assert packaged["coord"] == coord
        assert packaged["spatial"] == agent["spatial"]


@pytest.mark.parametrize("coord,address,missing_tree", [
    ([1, 0], OBJECT, False),  # The real floor has no Game Object.
    ([0, 0], ARENA, False),  # An Arena prefix cannot replace the object's real address.
    ([1, 0], [*ARENA[:2], "unknown-arena"], False),
    ([0, 0], [*ARENA, "unknown-object"], False),
    ([1, 1], ARENA[:2], False),
    ([1, 0], [*OBJECT, "extra-level"], False),
    ([1, 0], [*ARENA[:2], ""], False),
    ([1, 0], ARENA, True),
    ([2, 0], ARENA, False),
    ([3, 0], ARENA, False),
])
def test_invalid_spawn_save_is_rejected_and_keeps_existing_package(workspace_client, coord, address, missing_tree):
    client, endpoint, root = workspace_client
    detail = client.get(endpoint).json()
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    definition = detail["definition"]
    agent = definition["agents"][0]
    agent["coord"] = coord
    agent["spatial"]["address"]["initial_location"] = address
    if missing_tree:
        agent["spatial"]["tree"] = {ARENA[0]: {ARENA[1]: {"other-arena": []}}}
    response = client.put(endpoint, json={
        "definition": definition, "expected_content_sha256": detail["content_sha256"],
    })
    assert response.status_code == 422, response.text
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert client.post(endpoint + "/validate").json()["valid"]


@pytest.mark.parametrize("coord,address", [([1, 0], ARENA), ([0, 0], OBJECT)])
def test_author_agent_can_be_materialized_at_arena_or_object(tmp_path, coord, address):
    database = create_database(f"sqlite:///{(tmp_path / 'authors.sqlite').as_posix()}")
    Base.metadata.create_all(database.engine)
    try:
        resources = StudioResourceService(database)
        public = resources.create_agent(StudioAgentDefinition.model_validate({
            "agent_key": "materialized-agent", "name": "导入角色", "scratch": {"age": 21},
        }))
        workspaces = ExperimentWorkspaceService(database, package_root=tmp_path / "packages", var_dir=tmp_path)
        definition = definition_with_floor()
        with database.session_factory() as session:
            agents, assets = workspaces._materialize_agents(
                session, [public["id"]], world=definition["world"],
                placements=[AgentPlacement(public["id"], tuple(coord))],
            )
        assert assets == {}
        assert agents[0]["coord"] == coord
        assert agents[0]["spatial"]["address"]["initial_location"] == address
        assert agents[0]["spatial"]["tree"] == {ARENA[0]: {ARENA[1]: {ARENA[2]: address[3:]}}}
        definition["agents"] = agents
        validate_experiment_directory(build_package(tmp_path, definition))
    finally:
        database.close()


@pytest.mark.parametrize("damage", ["unknown-node", "wrong-kind", "wrong-parent"])
def test_three_level_spawn_does_not_bypass_semantic_node_validation(tmp_path, damage):
    definition = definition_with_floor()
    definition["agents"][0]["coord"] = [1, 0]
    definition["agents"][0]["spatial"]["address"]["initial_location"] = ARENA
    root = build_package(tmp_path, definition)
    world = read_json(root / "world/world.json")
    semantic = copy.deepcopy(world["definition"]["semantic_index"])
    arena_node = next(node for node in semantic["nodes"] if node["address"] == ARENA)
    if damage == "unknown-node":
        semantic["coordinate_paths"]["1,0"][-1] = "unknown-node-id"
    elif damage == "wrong-kind":
        arena_node["kind"] = "GAME_OBJECT"
    else:
        arena_node["parent_id"] = next(node["id"] for node in semantic["nodes"] if node["kind"] == "WORLD")
    world["definition"]["semantic_index"] = semantic
    atomic_write_json(root / "world/world.json", world)
    atomic_write_json(root / "world/semantic-index.json", semantic)
    write_integrity_manifest(root)
    with pytest.raises(PackageError, match="semantic"):
        validate_experiment_directory(root)
