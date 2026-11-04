"""A 3D experiment carries only its service reference; GA facts stay portable."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from generative_agents.ga_protocol.packages.io import read_json, seal_directory
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_protocol.schemas.experiment import WorldConfig
from generative_agents.ga_replay.api import run_scene_reference
from generative_agents.ga_runtime.api import RunService
from generative_agents.ga_studio.api import ExperimentSelection, ExperimentWorkspaceService, StudioSession, StudioAgentDefinition
from tests.test_portable_package_protocol import _definition


def test_scene_selection_sealing_and_run_keep_only_service_id(tmp_path):
    session = StudioSession("sqlite:///" + (tmp_path/"studio.db").as_posix(), tmp_path)
    brain = session.skills.create(name="scene-brain", description="test", kind="brain")
    agent = session.resources.create_agent(StudioAgentDefinition(agent_key="walker", name="Walker", scratch={"age":28}))
    model = session.resources.create_model_preset(name="test", config=_definition()["models"])
    wk = ExperimentWorkspaceService(session.database, package_root=tmp_path/"packages", var_dir=tmp_path,
                                   skill_registry=session.skills)
    selected = wk.create(ExperimentSelection(name="Online map", goal="", map_id=None, simworld_scene_id="map-15",
        brain_skill_id=brain.resource_id, model_preset_id=model["id"], agent_ids=(agent["id"],)))
    root = Path(selected["location"])
    validate_experiment_directory(root)
    record = next(r for r in read_json(root/"resources/index.json")["resources"] if r["kind"] == "map")
    assert record["definition"]["definition"] == {"environment_type":"spatial_3d","simworld_scene_id":"map-15"}
    assert record["attachments"] == [] and not record["definition"].get("assets")
    assert wk.simworld_scene_id(selected["experiment_id"]) == "map-15"
    assert wk.simworld_scene_participants(selected["experiment_id"])[0]["agent_key"] == "walker"
    archive = seal_directory(root, tmp_path/"scene.gaexp")
    run = RunService().create(archive, tmp_path/"run", requested_steps=2)
    ref = run_scene_reference(run, read_json(run/"run.json")["run_id"])
    assert ref["scene_id"] == "map-15" and ref["participants"] == {"walker": None}
    assert not any(f.suffix in {".pak",".glb",".jpg",".umap"} for f in run.rglob("*"))


@pytest.mark.parametrize("injected", [{"roads":[]},{"anchors":[]},{"prebuilt_scene":{"files":[]}},
                                       {"simworld_scene_id":"../map-15"}])
def test_simworld_definition_cannot_smuggle_assets_or_geometry(injected):
    with pytest.raises(ValidationError):
        WorldConfig(world_key="scene", world_name="scene", definition={
            "environment_type":"spatial_3d", "simworld_scene_id":"map-15", **injected})
