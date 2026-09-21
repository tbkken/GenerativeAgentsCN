"""Studio process resources. All database setup and SQL stay in this module."""
from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.schema import prepare_studio_database
from generative_agents.ga_studio.resources.bundled import bundled_skills
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.resources.spatial_assets import SpatialAssetService
from generative_agents.ga_studio.resources.catalog import StudioResourceService

class StudioSession:
    def __init__(self, database_url, root, *, migrate=True):
        if migrate:
            prepare_studio_database(database_url, backup_dir=root / "backups")
        database = create_database(database_url)
        skills = DatabaseSkillRegistry(database, cache_root=root / "skill-author-cache")
        spatial_assets = SpatialAssetService(database)
        resources = StudioResourceService(database)

        self.database, self.skills = database, skills
        self.spatial_assets, self.resources = spatial_assets, resources

    def initialize(self):
        skills, spatial_assets, resources = self.skills, self.spatial_assets, self.resources
        skills.ensure_builtin_skills(bundled_skills())
        spatial_assets.ensure_builtin_assets()
        if not resources.list_model_presets():
            resources.create_model_preset(
                name="本机默认模型",
                preset_key="local-default",
                description="Studio 初始模型连接；可在实验配置中调整后随实验包保存。",
                config={
                    "chat": {
                        "provider": "vllm",
                        "model": "Qwen3.8-27B-UD-Q4_K_XL",
                        "base_url": "http://127.0.0.1:8888/v1",
                    },
                    "embedding": {
                        "provider": "openai_compatible",
                        "model": "auto",
                        "base_url": "http://127.0.0.1:5002/v1",
                    },
                },
            )

    def health(self):
        with self.database.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1").scalar_one()
        return {"status": "ok", "runtime_truth": "files", "database_owner": "ga_studio"}

    def close(self):
        self.database.close()
