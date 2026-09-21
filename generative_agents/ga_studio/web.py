"""Package-first Studio Web application.

The process owns the author-resource database and a disposable package index.
Run workers receive only Run directories; Replay reads those same files.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from generative_agents.persistence import create_database
from generative_agents.ga_protocol import PackageError
from generative_agents.services.spatial_assets import SpatialAssetService
from generative_agents.skills import DatabaseSkillRegistry, SkillRegistry
from generative_agents.web.portable_api import create_portable_router

from .resource_api import create_resource_router
from .resources import StudioResourceService
from .schema import prepare_studio_database
from .model_services import create_model_service_router
from .experiment_resources import create_experiment_resource_router


def create_studio_app(
    *,
    database_url: str = "sqlite:///var/ga-studio.db",
    var_dir: str | Path = "var",
    migrate: bool = True,
    max_concurrent_runs: int = 2,
) -> FastAPI:
    root = Path(var_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if migrate:
        prepare_studio_database(database_url, backup_dir=root / "backups")
    database = create_database(database_url)
    skills = DatabaseSkillRegistry(database, cache_root=root / "skill-author-cache")
    spatial_assets = SpatialAssetService(database)
    resources = StudioResourceService(database)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        skills.ensure_builtin_skills(SkillRegistry())
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
        app.state.database = database
        app.state.skill_registry = skills
        try:
            yield
        finally:
            database.close()

    app = FastAPI(
        title="GenerativeAgentsCN Package Studio",
        version="2.0",
        lifespan=lifespan,
    )
    @app.exception_handler(PackageError)
    async def invalid_package(_request, _exception):
        return JSONResponse(status_code=409, content={"detail": "Package integrity or storage boundary validation failed"})

    @app.middleware("http")
    async def revalidate_console(request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith(("/experiments/", "/static/console/")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    app.include_router(create_resource_router(database, skills))
    app.include_router(create_model_service_router(database, root))
    app.include_router(create_experiment_resource_router(database, root))
    app.include_router(
        create_portable_router(
            database,
            package_root=root / "packages",
            max_concurrent_runs=max_concurrent_runs,
        )
    )
    console_static = Path(__file__).resolve().parents[1] / "web" / "static"
    # Keep the established management console as the Web presentation layer.
    # The package-first refactor changes its adapters and data sources, not the
    # interface users already rely on.
    app.mount(
        "/static/console",
        StaticFiles(directory=console_static),
        name="console-static",
    )
    village_static = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "static"
        / "assets"
        / "village"
    )
    app.mount(
        "/generative_agents/frontend/static/assets/village",
        StaticFiles(directory=village_static),
        name="legacy-village-assets",
    )

    @app.get("/api/studio/health")
    def health():
        with database.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1").scalar_one()
        return {"status": "ok", "runtime_truth": "files", "database_owner": "ga_studio"}

    @app.get("/experiments/{experiment_id}", response_class=FileResponse, include_in_schema=False)
    @app.get("/", response_class=FileResponse, include_in_schema=False)
    def index():
        return FileResponse(console_static / "experiment-console.html")

    return app


__all__ = ["create_studio_app"]
