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

from generative_agents.ga_studio.api import StudioSession
from generative_agents.ga_protocol.schemas.errors import ServiceError
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.adapters.web.routes.portable import create_portable_router

from generative_agents.adapters.web.routes.resources import create_resource_router
from generative_agents.adapters.web.routes.models import create_model_service_router
from generative_agents.adapters.web.routes.experiment_resources import create_experiment_resource_router


def create_studio_app(
    *,
    database_url: str = "sqlite:///var/ga-studio.db",
    var_dir: str | Path = "var",
    migrate: bool = True,
    max_concurrent_runs: int = 2,
) -> FastAPI:
    root = Path(var_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    studio = StudioSession(database_url, root, migrate=migrate)
    database, skills = studio.database, studio.skills

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        studio.initialize()
        app.state.database = database
        app.state.skill_registry = skills
        try:
            yield
        finally:
            studio.close()

    app = FastAPI(
        title="GenerativeAgentsCN Package Studio",
        version="2.0",
        lifespan=lifespan,
    )
    @app.exception_handler(PackageError)
    async def invalid_package(_request, _exception):
        return JSONResponse(status_code=409, content={"detail": "Package integrity or storage boundary validation failed"})

    @app.exception_handler(ServiceError)
    async def service_error(_request, error):
        return JSONResponse(status_code=error.status_code, content={"detail": error.message})

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
    console_static = Path(__file__).resolve().parent / "static"
    # Keep the established management console as the Web presentation layer.
    # The package-first refactor changes its adapters and data sources, not the
    # interface users already rely on.
    app.mount(
        "/static/console",
        StaticFiles(directory=console_static),
        name="console-static",
    )
    from generative_agents.ga_studio.api import BUNDLED_ROOT
    app.mount('/assets/library/village', StaticFiles(directory=BUNDLED_ROOT / 'maps' / 'village'), name='author-map-assets')

    @app.get("/api/studio/health")
    def health():
        return studio.health()

    @app.get("/experiments/{experiment_id}", response_class=FileResponse, include_in_schema=False)
    @app.get("/", response_class=FileResponse, include_in_schema=False)
    def index():
        return FileResponse(console_static / "shell/experiment-console.html")

    return app


__all__ = ["create_studio_app"]
