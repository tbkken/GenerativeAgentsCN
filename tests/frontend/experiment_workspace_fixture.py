"""Disposable Studio for browser verification; never reads production resources."""
from pathlib import Path
import tempfile

from generative_agents.ga_studio.web import create_studio_app
from generative_agents.ga_studio.workspace import ExperimentWorkspaceService, ExperimentSelection
from generative_agents.ga_studio.resources import StudioResourceService
from generative_agents.persistence import create_database
from generative_agents.persistence.models import WorldMap
from generative_agents.services.maps import normalize_public_world
from generative_agents.skills import DatabaseSkillRegistry
from generative_agents.ga_protocol import seal_directory
from tests.foundation.test_navigation import navigation_world

var = Path(tempfile.mkdtemp(prefix="ga-experiment-workspace-ui-"))
database_url = f"sqlite:///{(var / 'studio.db').as_posix()}"
app = create_studio_app(database_url=database_url, var_dir=var)
database = create_database(database_url)
resources = StudioResourceService(database)
skills = DatabaseSkillRegistry(database, cache_root=var / "skills")
world = normalize_public_world(navigation_world()).model_dump(mode="json")
with database.session_factory.begin() as session:
    item = WorldMap(map_key="workspace-map", name="基础测试地图", world_json=world, world_hash="0" * 64)
    session.add(item)
    session.flush()
    map_id = item.id
child = skills.create(name="workspace-observe", description="观察当前地点", kind="atomic")
brain = skills.create(name="workspace-brain", description="实验工作区验证大脑", kind="brain")
brain = skills.save(brain.name, brain.markdown + "\n调用 $workspace-observe 后等待。\n")
agent = resources.create_agent({"agent_key": "workspace-agent", "name": "测试智能体", "scratch": {"age": 30}})
crowd = resources.create_crowd(name="基础测试人群", agent_ids=[agent["id"]])
models = resources.create_model_preset(name="测试模型", config={
    "chat": {"provider": "vllm", "model": "test", "base_url": "http://127.0.0.1:8888/v1"},
    "embedding": {"provider": "openai_compatible", "model": "test", "base_url": "http://127.0.0.1:5002/v1"},
})
workspace = ExperimentWorkspaceService(database, package_root=var / "packages", var_dir=var, skill_registry=skills)
created = workspace.create(ExperimentSelection(name="实验副本编辑验收", goal="验证独立工作区", map_id=map_id,
    brain_skill_id=brain.resource_id, model_preset_id=models["id"], crowd_ids=(crowd["id"],)))
sealed = workspace.duplicate(created["experiment_id"])
archive = seal_directory(Path(sealed["location"]), var / "packages" / "sealed.gaexp")
workspace.catalog.upsert(archive)
print(f"DISPOSABLE WORKSPACE: {var}\nDRAFT: /experiments/{created['experiment_id']}\nSEALED: /experiments/{sealed['experiment_id']}", flush=True)
database.close()
