"""Minimal experiment-first REST application."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, Header, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError

from generative_agents.config import (
    AgentTemplateDefinition,
    ExperimentDefinition,
    SpatialAssetContract,
    ToolContract,
)
from generative_agents.config.schema import (
    StrictModel,
    WorldConfig,
    WorldOverlayConfig,
)
from generative_agents.persistence import create_database, upgrade_database
from generative_agents.persistence.models import (
    Experiment,
    ExperimentRevision,
    Run,
    RunEvent,
    RunQueue,
)
from generative_agents.services import (
    CrowdService,
    ExperimentService,
    ServiceError,
    SpatialAssetService,
    ToolService,
)
from generative_agents.services.maps import WorldMapService
from generative_agents.services.map_importer import fresh_ville_editor_document
from generative_agents.services.catalog import AssetService, SecretService
from generative_agents.services.results import ResultQueryService
from generative_agents.services.runs import RunService
from generative_agents.runtime.supervisor import LocalProcessSupervisor
from generative_agents.runtime.artifact_scheduler import ArtifactProcessScheduler
from generative_agents.services.artifacts import ArtifactService
from generative_agents.services.byte_windows import read_utf8_handle
from generative_agents.services.checkpoints import CheckpointService
from generative_agents.services.logs import LogService
from generative_agents.services.replay import ReplayService
from generative_agents.services.model_probes import ModelProbeService
from generative_agents.skills import MemoryStream, SkillMCPServer, SkillRegistry, SkillRuntime
from generative_agents.web.skill_api import create_skill_router
from generative_agents.web.observability_schemas import (
    AttemptListResponse,
    CheckpointDetailResponse,
    CheckpointListResponse,
    CheckpointPreviewResponse,
    LogWindowResponse,
    ModelTraceDetailResponse,
    ModelTracePageResponse,
)
from generative_agents.web.replay_schemas import (
    ReplayManifestResponse,
    ReplayStepsResponse,
)
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from pathlib import Path

request_id_var: ContextVar[str] = ContextVar("request_id", default="")


class SourceRequest(StrictModel):
    type: Literal[
        "BUILTIN_DEFAULT",
        "BLANK",
        "REVISION",
    ] = "BUILTIN_DEFAULT"
    revision_id: str | None = None


class CreateExperimentRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(default="", max_length=10_000)
    owner: str = Field(default="", max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source: SourceRequest | None = None
    map_revision_id: str | None = None
    crowd_revision_ids: list[str] = Field(default_factory=list, max_length=50)


class DraftUpdateRequest(StrictModel):
    lock_version: int = Field(ge=1)
    data: dict[str, Any]


class BatchAgentRequest(StrictModel):
    lock_version: int = Field(ge=1)
    agent_keys: list[str] = Field(min_length=1, max_length=500)
    changes: dict[str, Any]
    dry_run: bool = False


class ExperimentMetadataRequest(StrictModel):
    row_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(default="", max_length=10_000)
    owner: str = Field(default="", max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)


class DuplicateExperimentRequest(StrictModel):
    revision_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    goal: str | None = Field(default=None, max_length=10_000)
    copy_metadata: bool = True


class ArchiveExperimentRequest(StrictModel):
    row_version: int | None = Field(default=None, ge=1)


class BatchExperimentRequest(StrictModel):
    experiment_ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["ARCHIVE", "RESTORE", "ADD_TAGS", "SET_OWNER"]
    owner: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)


class CompareExperimentsRequest(StrictModel):
    experiment_ids: list[str] = Field(min_length=2, max_length=12)


class ComparisonGroupRequest(CompareExperimentsRequest):
    name: str = Field(min_length=1, max_length=120)


class SavedViewRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    query: dict[str, Any] = Field(default_factory=dict)


class PublishAndRunRequest(StrictModel):
    draft_revision_id: str
    lock_version: int = Field(ge=1)


class CancelRunRequest(StrictModel):
    force: bool = False


class SecretCreateRequest(StrictModel):
    kind: Literal["OPENAI_API_KEY", "GENERIC_TOKEN"]
    value: str = Field(min_length=1, max_length=20_000)


class ArtifactJobRequest(StrictModel):
    job_type: Literal[
        "BUILD_REPLAY",
        "BUILD_REPORT",
        "RESULT_BUNDLE",
        "FILTERED_MEMORIES",
        "FILTERED_CONVERSATIONS",
        "CHECKPOINT_BUNDLE",
    ]
    parameters: dict[str, Any] = Field(default_factory=dict)


class ModelProbeRequest(StrictModel):
    lock_version: int = Field(ge=1)


class CreateMapRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=10_000)
    source_revision_id: str | None = None
    blueprint_key: str | None = Field(default=None, min_length=1, max_length=80)
    map_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    width: int = Field(default=48, ge=4, le=240)
    height: int = Field(default=32, ge=4, le=240)
    tile_size: int = Field(default=32, ge=8, le=128)


class MapDraftUpdateRequest(StrictModel):
    lock_version: int = Field(ge=1)
    world: dict[str, Any]


class PublishMapRequest(StrictModel):
    draft_revision_id: str
    lock_version: int = Field(ge=1)


class PublishRevisionRequest(StrictModel):
    draft_revision_id: str
    lock_version: int = Field(ge=1)


class MapBlueprintStepRequest(StrictModel):
    lock_version: int = Field(ge=1)


class ExperimentMapSelectionRequest(StrictModel):
    lock_version: int = Field(ge=1)
    map_revision_id: str


class ExperimentMapOverlayRequest(StrictModel):
    lock_version: int = Field(ge=1)
    overlay: dict[str, Any] = Field(default_factory=dict)


class CreateAgentTemplateRequest(StrictModel):
    definition: AgentTemplateDefinition
    description: str = Field(default="", max_length=10_000)
    agent_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")


class UpdateAgentTemplateRequest(StrictModel):
    lock_version: int = Field(ge=1)
    definition: AgentTemplateDefinition
    description: str | None = Field(default=None, max_length=10_000)


class CreateCrowdRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=10_000)
    agent_revision_ids: list[str] = Field(default_factory=list, max_length=500)
    source_revision_id: str | None = None
    crowd_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")


class UpdateCrowdRequest(StrictModel):
    lock_version: int = Field(ge=1)
    agent_revision_ids: list[str] = Field(max_length=500)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=10_000)


class CreateSpatialAssetRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=10_000)
    asset_key: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
    )
    asset_kind: Literal["TILE", "OBJECT", "ZONE", "MARKING", "NETWORK"] = "TILE"
    source_revision_id: str | None = None
    contract: SpatialAssetContract | None = None


class UpdateSpatialAssetRequest(StrictModel):
    lock_version: int = Field(ge=1)
    contract: SpatialAssetContract
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=10_000)


class CreateToolRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=10_000)
    tool_key: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
    )
    tool_kind: Literal[
        "CAR", "BICYCLE", "MOTORCYCLE", "ACCESS_CARD", "DEVICE", "OTHER"
    ] = "OTHER"
    source_revision_id: str | None = None
    contract: ToolContract | None = None


class UpdateToolRequest(StrictModel):
    lock_version: int = Field(ge=1)
    contract: ToolContract
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=10_000)


def _error_response(
    *, status_code: int, code: str, message: str, details: Any, request_id: str
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": jsonable_encoder(details),
                "request_id": request_id,
            }
        },
    )


def create_app(
    *,
    database_url: str = "sqlite:///var/generative-agents.db",
    var_dir: str | None = None,
    migrate: bool = True,
    max_concurrent_runs: int = 2,
    supervisor_enabled: bool = True,
) -> FastAPI:
    if var_dir is None:
        parsed_url = make_url(database_url)
        var_dir = (
            str(Path(parsed_url.database).expanduser().resolve().parent)
            if parsed_url.database and parsed_url.database != ":memory:"
            else "var"
        )
    database = create_database(database_url)
    service = ExperimentService(database)
    map_service = WorldMapService(database)
    skill_registry = SkillRegistry(history_root=Path(var_dir) / "skill-history")
    skill_mcp = SkillMCPServer(MemoryStream(Path(var_dir) / "skill-memory.db"))
    skill_runtime = SkillRuntime(skill_registry, mcp=skill_mcp)
    spatial_asset_service = SpatialAssetService(database)
    tool_service = ToolService(database)
    crowd_service = CrowdService(database)
    result_service = ResultQueryService(database)
    asset_service = AssetService(database, var_dir=var_dir)
    secret_service = SecretService(database, var_dir=var_dir)
    artifact_service = ArtifactService(database, var_dir=var_dir)
    replay_service = ReplayService(database, var_dir=var_dir)
    log_service = LogService(database, var_dir=var_dir)
    checkpoint_service = CheckpointService(database, var_dir=var_dir)
    model_probe_service = ModelProbeService(
        database, experiments=service, secrets=secret_service
    )
    run_service = RunService(
        database,
        var_dir=var_dir,
        model_probes=model_probe_service,
    )
    supervisor = LocalProcessSupervisor(
        database,
        var_dir=var_dir,
        max_concurrent_runs=max_concurrent_runs,
    )
    artifact_scheduler = ArtifactProcessScheduler(database, var_dir=var_dir)
    console_shell = (
        Path(__file__).resolve().parent / "static" / "experiment-console.html"
    ).read_text(encoding="utf-8")
    commute_demo_path = (
        Path(__file__).resolve().parent / "static" / "commute-demo.html"
    )
    map_configuration_demo_path = (
        Path(__file__).resolve().parent / "static" / "map-configuration-demo.html"
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if migrate:
            upgrade_database(database_url)
        map_service.ensure_builtin_map()
        crowd_service.ensure_builtin_resources()
        spatial_asset_service.ensure_builtin_assets()
        map_service.ensure_intersection_map()
        tool_service.ensure_builtin_tools()
        app.state.database = database
        app.state.experiment_service = service
        app.state.world_map_service = map_service
        app.state.skill_registry = skill_registry
        app.state.skill_runtime = skill_runtime
        app.state.skill_mcp = skill_mcp
        app.state.spatial_asset_service = spatial_asset_service
        app.state.tool_service = tool_service
        app.state.crowd_service = crowd_service
        app.state.run_service = run_service
        app.state.result_service = result_service
        app.state.asset_service = asset_service
        app.state.secret_service = secret_service
        app.state.artifact_service = artifact_service
        app.state.log_service = log_service
        app.state.checkpoint_service = checkpoint_service
        app.state.model_probe_service = model_probe_service
        app.state.supervisor = supervisor
        app.state.artifact_scheduler = artifact_scheduler
        if supervisor_enabled:
            supervisor.start()
            try:
                artifact_scheduler.start()
            except Exception:
                supervisor.stop()
                raise
        try:
            yield
        finally:
            if supervisor_enabled:
                artifact_scheduler.stop()
                supervisor.stop()
            database.close()

    app = FastAPI(title="GenerativeAgentsCN Experiment API", version="1.0", lifespan=lifespan)
    app.include_router(create_skill_router(skill_registry, skill_runtime, skill_mcp))
    console_static = Path(__file__).resolve().parent / "static"
    app.mount("/static/console", StaticFiles(directory=console_static), name="console-static")
    village_static = Path(__file__).resolve().parents[1] / "frontend" / "static" / "assets" / "village"
    app.mount(
        "/generative_agents/frontend/static/assets/village",
        StaticFiles(directory=village_static),
        name="legacy-village-assets",
    )
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            if request.url.path == "/" or request.url.path.startswith(
                "/static/console/"
            ):
                # The desktop console and its scripts must be deployed as one
                # coherent UI version.  Revalidate on a normal refresh so a
                # cached workspace script cannot overwrite newer HTML state.
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response
        finally:
            request_id_var.reset(token)

    @app.exception_handler(ServiceError)
    async def service_error_handler(_request: Request, exc: ServiceError):
        return _error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=request_id_var.get(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        return _error_response(
            status_code=422,
            code="REQUEST_VALIDATION_FAILED",
            message="请求参数校验失败",
            details={"errors": exc.errors()},
            request_id=request_id_var.get(),
        )

    @app.exception_handler(ValidationError)
    async def model_validation_error_handler(_request: Request, exc: ValidationError):
        return _error_response(
            status_code=422,
            code="CONFIG_VALIDATION_FAILED",
            message="实验配置结构校验失败",
            details={"errors": exc.errors(include_url=False)},
            request_id=request_id_var.get(),
        )

    @app.get("/api/v1/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/health/ready")
    def ready() -> dict[str, str]:
        with database.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return {"status": "ready"}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def experiment_console():
        return HTMLResponse(
            console_shell,
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    @app.get("/demos/two-day-commute", include_in_schema=False)
    def two_day_commute_demo():
        return FileResponse(
            commute_demo_path,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/demos/map-configuration", include_in_schema=False)
    def map_configuration_demo():
        return FileResponse(
            map_configuration_demo_path,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/v1/health", include_in_schema=False)
    def health():
        with database.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1").scalar_one()
        return {"status": "ok"}

    @app.post("/api/v1/spatial-assets", status_code=201)
    def create_spatial_asset(body: CreateSpatialAssetRequest):
        return spatial_asset_service.create_asset(
            name=body.name,
            description=body.description,
            asset_key=body.asset_key,
            asset_kind=body.asset_kind,
            source_revision_id=body.source_revision_id,
            contract=body.contract,
        )

    @app.get("/api/v1/spatial-assets")
    def list_spatial_assets(
        q: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
    ):
        return spatial_asset_service.list_assets(
            query=q,
            status=status,
            asset_kind=kind,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/v1/spatial-assets/{asset_id}")
    def get_spatial_asset(asset_id: str):
        return spatial_asset_service.get_asset(asset_id)

    @app.get("/api/v1/spatial-assets/{asset_id}/draft")
    def get_spatial_asset_draft(asset_id: str):
        return spatial_asset_service.get_draft(asset_id)

    @app.put("/api/v1/spatial-assets/{asset_id}/draft")
    def update_spatial_asset_draft(asset_id: str, body: UpdateSpatialAssetRequest):
        return spatial_asset_service.update_draft(
            asset_id,
            expected_lock_version=body.lock_version,
            contract=body.contract,
            name=body.name,
            description=body.description,
        )

    @app.post("/api/v1/spatial-assets/{asset_id}/draft/publish")
    def publish_spatial_asset_draft(asset_id: str, body: PublishRevisionRequest):
        return spatial_asset_service.publish_draft(
            asset_id,
            draft_revision_id=body.draft_revision_id,
            expected_lock_version=body.lock_version,
        )

    @app.get("/api/v1/spatial-assets/{asset_id}/revisions")
    def list_spatial_asset_revisions(asset_id: str):
        return {"items": spatial_asset_service.list_revisions(asset_id)}

    @app.get("/api/v1/spatial-assets/{asset_id}/revisions/{revision_id}")
    def get_spatial_asset_revision(asset_id: str, revision_id: str):
        return spatial_asset_service.get_revision(asset_id, revision_id)

    @app.post(
        "/api/v1/spatial-assets/{asset_id}/revisions/{revision_id}/fork",
        status_code=201,
    )
    def fork_spatial_asset_revision(asset_id: str, revision_id: str):
        return spatial_asset_service.fork_revision(asset_id, revision_id)

    @app.post("/api/v1/tools", status_code=201)
    def create_tool(body: CreateToolRequest):
        return tool_service.create_tool(
            name=body.name,
            description=body.description,
            tool_key=body.tool_key,
            tool_kind=body.tool_kind,
            source_revision_id=body.source_revision_id,
            contract=body.contract,
        )

    @app.get("/api/v1/tools")
    def list_tools(
        q: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
    ):
        return tool_service.list_tools(
            query=q, status=status, kind=kind, page=page, page_size=page_size
        )

    @app.get("/api/v1/tools/{tool_id}")
    def get_tool(tool_id: str):
        return tool_service.get_tool(tool_id)

    @app.get("/api/v1/tools/{tool_id}/draft")
    def get_tool_draft(tool_id: str):
        return tool_service.get_draft(tool_id)

    @app.put("/api/v1/tools/{tool_id}/draft")
    def update_tool_draft(tool_id: str, body: UpdateToolRequest):
        return tool_service.update_draft(
            tool_id,
            expected_lock_version=body.lock_version,
            contract=body.contract,
            name=body.name,
            description=body.description,
        )

    @app.post("/api/v1/tools/{tool_id}/draft/publish")
    def publish_tool_draft(tool_id: str, body: PublishRevisionRequest):
        return tool_service.publish_draft(
            tool_id,
            draft_revision_id=body.draft_revision_id,
            expected_lock_version=body.lock_version,
        )

    @app.get("/api/v1/tools/{tool_id}/revisions")
    def list_tool_revisions(tool_id: str):
        return {"items": tool_service.list_revisions(tool_id)}

    @app.get("/api/v1/tools/{tool_id}/revisions/{revision_id}")
    def get_tool_revision(tool_id: str, revision_id: str):
        return tool_service.get_revision(tool_id, revision_id)

    @app.post(
        "/api/v1/tools/{tool_id}/revisions/{revision_id}/fork",
        status_code=201,
    )
    def fork_tool_revision(tool_id: str, revision_id: str):
        return tool_service.fork_revision(tool_id, revision_id)

    @app.post("/api/v1/agent-templates", status_code=201)
    def create_agent_template(body: CreateAgentTemplateRequest):
        return crowd_service.create_agent(
            definition=body.definition,
            description=body.description,
            agent_key=body.agent_key,
        )

    @app.get("/api/v1/agent-templates")
    def list_agent_templates(
        q: str | None = None,
        status: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=100, ge=1, le=500),
    ):
        return crowd_service.list_agents(
            query=q, status=status, page=page, page_size=page_size
        )

    @app.get("/api/v1/agent-templates/{agent_id}")
    def get_agent_template(agent_id: str):
        return crowd_service.get_agent(agent_id)

    @app.get("/api/v1/agent-templates/{agent_id}/draft")
    def get_agent_template_draft(agent_id: str):
        return crowd_service.get_agent_draft(agent_id)

    @app.put("/api/v1/agent-templates/{agent_id}/draft")
    def update_agent_template_draft(agent_id: str, body: UpdateAgentTemplateRequest):
        return crowd_service.update_agent_draft(
            agent_id,
            expected_lock_version=body.lock_version,
            definition=body.definition,
            description=body.description,
        )

    @app.post("/api/v1/agent-templates/{agent_id}/draft/publish")
    def publish_agent_template_draft(agent_id: str, body: PublishRevisionRequest):
        return crowd_service.publish_agent_draft(
            agent_id,
            draft_revision_id=body.draft_revision_id,
            expected_lock_version=body.lock_version,
        )

    @app.get("/api/v1/agent-templates/{agent_id}/revisions")
    def list_agent_template_revisions(agent_id: str):
        return {"items": crowd_service.list_agent_revisions(agent_id)}

    @app.get("/api/v1/agent-templates/{agent_id}/revisions/{revision_id}")
    def get_agent_template_revision(agent_id: str, revision_id: str):
        return crowd_service.get_agent_revision(agent_id, revision_id)

    @app.post(
        "/api/v1/agent-templates/{agent_id}/revisions/{revision_id}/fork",
        status_code=201,
    )
    def fork_agent_template_revision(agent_id: str, revision_id: str):
        return crowd_service.fork_agent_revision(agent_id, revision_id)

    @app.post("/api/v1/crowds", status_code=201)
    def create_crowd(body: CreateCrowdRequest):
        return crowd_service.create_crowd(
            name=body.name,
            description=body.description,
            agent_revision_ids=body.agent_revision_ids,
            source_revision_id=body.source_revision_id,
            crowd_key=body.crowd_key,
        )

    @app.get("/api/v1/crowds")
    def list_crowds(
        q: str | None = None,
        status: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=5, ge=1, le=100),
    ):
        return crowd_service.list_crowds(
            query=q, status=status, page=page, page_size=page_size
        )

    @app.get("/api/v1/crowds/{crowd_id}")
    def get_crowd(crowd_id: str):
        return crowd_service.get_crowd(crowd_id)

    @app.get("/api/v1/crowds/{crowd_id}/draft")
    def get_crowd_draft(crowd_id: str):
        return crowd_service.get_crowd_draft(crowd_id)

    @app.put("/api/v1/crowds/{crowd_id}/draft")
    def update_crowd_draft(crowd_id: str, body: UpdateCrowdRequest):
        return crowd_service.update_crowd_draft(
            crowd_id,
            expected_lock_version=body.lock_version,
            agent_revision_ids=body.agent_revision_ids,
            name=body.name,
            description=body.description,
        )

    @app.post("/api/v1/crowds/{crowd_id}/draft/publish")
    def publish_crowd_draft(crowd_id: str, body: PublishRevisionRequest):
        return crowd_service.publish_crowd_draft(
            crowd_id,
            draft_revision_id=body.draft_revision_id,
            expected_lock_version=body.lock_version,
        )

    @app.get("/api/v1/crowds/{crowd_id}/revisions")
    def list_crowd_revisions(crowd_id: str):
        return {"items": crowd_service.list_crowd_revisions(crowd_id)}

    @app.get("/api/v1/crowds/{crowd_id}/revisions/{revision_id}")
    def get_crowd_revision(crowd_id: str, revision_id: str):
        return crowd_service.get_crowd_revision(crowd_id, revision_id)

    @app.post(
        "/api/v1/crowds/{crowd_id}/revisions/{revision_id}/fork",
        status_code=201,
    )
    def fork_crowd_revision(crowd_id: str, revision_id: str):
        return crowd_service.fork_crowd_revision(crowd_id, revision_id)

    @app.post("/api/v1/maps", status_code=201)
    def create_map(body: CreateMapRequest):
        return map_service.create_map(
            name=body.name,
            description=body.description,
            source_revision_id=body.source_revision_id,
            blueprint_key=body.blueprint_key,
            map_key=body.map_key,
            width=body.width,
            height=body.height,
            tile_size=body.tile_size,
        )

    @app.get("/api/v1/map-blueprints")
    def list_map_blueprints():
        return {"items": map_service.list_blueprints()}

    @app.get("/api/v1/maps")
    def list_maps(
        q: str | None = None,
        status: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=5, ge=1, le=100),
    ):
        return map_service.list_maps(query=q, status=status, page=page, page_size=page_size)

    @app.get("/api/v1/map-editor/ville-document")
    def get_ville_map_editor_document():
        """Return the deterministic authoring document built from bundled Tiled data."""

        return fresh_ville_editor_document().model_dump(mode="json")

    @app.get("/api/v1/maps/{map_id}")
    def get_map(map_id: str):
        return map_service.get_map(map_id)

    @app.get("/api/v1/maps/{map_id}/draft")
    def get_map_draft(map_id: str):
        return map_service.get_draft(map_id)

    @app.put("/api/v1/maps/{map_id}/draft")
    def update_map_draft(map_id: str, body: MapDraftUpdateRequest):
        return map_service.update_draft(
            map_id,
            expected_lock_version=body.lock_version,
            world=WorldConfig.model_validate(body.world),
        )

    @app.post("/api/v1/maps/{map_id}/draft/publish")
    def publish_map_draft(map_id: str, body: PublishMapRequest):
        return map_service.publish_draft(
            map_id,
            draft_revision_id=body.draft_revision_id,
            expected_lock_version=body.lock_version,
        )

    @app.post("/api/v1/maps/{map_id}/draft/blueprint-steps/{step}")
    def apply_map_blueprint_step(
        map_id: str, step: int, body: MapBlueprintStepRequest
    ):
        return map_service.apply_blueprint_step(
            map_id,
            expected_lock_version=body.lock_version,
            step=step,
        )

    @app.get("/api/v1/maps/{map_id}/revisions")
    def list_map_revisions(map_id: str):
        return {"items": map_service.list_revisions(map_id)}

    @app.get("/api/v1/maps/{map_id}/revisions/{revision_id}")
    def get_map_revision(map_id: str, revision_id: str):
        return map_service.get_revision(map_id, revision_id)

    @app.post("/api/v1/maps/{map_id}/revisions/{revision_id}/fork", status_code=201)
    def fork_map_revision(map_id: str, revision_id: str):
        return map_service.fork_revision(map_id, revision_id)

    @app.post("/api/v1/experiments", status_code=201)
    def create_experiment(body: CreateExperimentRequest):
        if body.source is None and not body.crowd_revision_ids:
            raise ServiceError(
                "CROWD_REQUIRED",
                "请至少选择一个已发布人群",
                status_code=422,
            )
        source_type = body.source.type if body.source else "BUILTIN_DEFAULT"
        source_revision_id = body.source.revision_id if body.source else None
        map_revision_id = body.map_revision_id
        if body.source is None and not map_revision_id:
            map_revision_id = map_service.default_revision_id()
        return service.create_experiment(
            name=body.name,
            goal=body.goal,
            source_type=source_type,
            source_revision_id=source_revision_id,
            owner=body.owner,
            tags=body.tags,
            map_revision_id=map_revision_id,
            crowd_revision_ids=body.crowd_revision_ids,
        )

    @app.get("/api/v1/experiments")
    def list_experiments(
        status: str | None = None,
        q: str | None = None,
        owner: str | None = None,
        tag: str | None = None,
        model: str | None = None,
        map_key: str | None = None,
        archived: Literal["active", "archived", "all"] = "active",
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=5),
        sort: str = "-updated_at",
    ):
        return service.list_experiments(
            status=status,
            query=q,
            owner=owner,
            tag=tag,
            model=model,
            map_key=map_key,
            archived=archived,
            page=page,
            page_size=page_size,
            sort=sort,
        )

    @app.get("/api/v1/experiments/{experiment_id}")
    def get_experiment(experiment_id: str):
        return service.get_experiment(experiment_id)

    @app.patch("/api/v1/experiments/{experiment_id}")
    def update_experiment(experiment_id: str, body: ExperimentMetadataRequest):
        return service.update_metadata(
            experiment_id,
            expected_row_version=body.row_version,
            name=body.name,
            goal=body.goal,
            owner=body.owner,
            tags=body.tags,
        )

    @app.post("/api/v1/experiments/{experiment_id}/duplicate", status_code=201)
    def duplicate_experiment(experiment_id: str, body: DuplicateExperimentRequest):
        return service.duplicate_experiment(
            experiment_id,
            revision_id=body.revision_id,
            name=body.name,
            goal=body.goal,
            copy_metadata=body.copy_metadata,
        )

    @app.post("/api/v1/experiments/{experiment_id}/archive")
    def archive_experiment(experiment_id: str, body: ArchiveExperimentRequest):
        return service.set_archived(
            experiment_id, archived=True, expected_row_version=body.row_version
        )

    @app.post("/api/v1/experiments/{experiment_id}/restore")
    def restore_experiment(experiment_id: str, body: ArchiveExperimentRequest):
        return service.set_archived(
            experiment_id, archived=False, expected_row_version=body.row_version
        )

    @app.post("/api/v1/experiments/batch")
    def batch_experiments(body: BatchExperimentRequest):
        return service.batch_manage(
            body.experiment_ids,
            action=body.action,
            owner=body.owner,
            tags=body.tags,
        )

    @app.post("/api/v1/experiments/compare")
    def compare_experiments(body: CompareExperimentsRequest):
        return service.compare_experiments(body.experiment_ids)

    @app.post("/api/v1/experiment-comparison-groups", status_code=201)
    def create_comparison_group(body: ComparisonGroupRequest):
        return service.save_comparison_group(body.name, body.experiment_ids)

    @app.get("/api/v1/experiment-comparison-groups")
    def list_comparison_groups():
        return {"items": service.list_comparison_groups()}

    @app.post("/api/v1/experiment-saved-views", status_code=201)
    def create_saved_view(body: SavedViewRequest):
        return service.save_view(body.name, body.query)

    @app.get("/api/v1/experiment-saved-views")
    def list_saved_views():
        return {"items": service.list_views()}

    @app.get("/api/v1/experiment-saved-views/shared/{share_key}")
    def get_shared_view(share_key: str):
        return service.get_view_by_share_key(share_key)

    @app.get("/api/v1/experiments/{experiment_id}/run-estimate")
    def get_run_estimate(experiment_id: str):
        return service.estimate_run(experiment_id)

    @app.get("/api/v1/experiments/{experiment_id}/draft")
    def get_draft(experiment_id: str):
        return service.get_draft(experiment_id)

    @app.put("/api/v1/experiments/{experiment_id}/draft")
    def replace_draft(experiment_id: str, body: DraftUpdateRequest):
        definition = ExperimentDefinition.model_validate(body.data)
        return service.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=body.lock_version,
            definition=definition,
        )

    @app.patch("/api/v1/experiments/{experiment_id}/draft/{section}")
    def patch_draft(experiment_id: str, section: str, body: DraftUpdateRequest):
        return service.patch_draft_section(
            experiment_id=experiment_id,
            section=section,
            expected_lock_version=body.lock_version,
            data=body.data,
        )

    @app.put("/api/v1/experiments/{experiment_id}/draft/world")
    def replace_world(experiment_id: str, body: DraftUpdateRequest):
        return service.patch_draft_section(
            experiment_id=experiment_id,
            section="world",
            expected_lock_version=body.lock_version,
            data=body.data,
        )

    @app.put("/api/v1/experiments/{experiment_id}/draft/map")
    def select_experiment_map(experiment_id: str, body: ExperimentMapSelectionRequest):
        return map_service.select_for_experiment(
            experiment_id,
            expected_lock_version=body.lock_version,
            map_revision_id=body.map_revision_id,
        )

    @app.put("/api/v1/experiments/{experiment_id}/draft/map-overlay")
    def update_experiment_map_overlay(
        experiment_id: str, body: ExperimentMapOverlayRequest
    ):
        return map_service.update_experiment_overlay(
            experiment_id,
            expected_lock_version=body.lock_version,
            overlay=WorldOverlayConfig.model_validate(body.overlay),
        )

    @app.put("/api/v1/experiments/{experiment_id}/draft/agents/{agent_key}")
    def replace_agent(experiment_id: str, agent_key: str, body: DraftUpdateRequest):
        return service.put_draft_agent(
            experiment_id,
            agent_key,
            expected_lock_version=body.lock_version,
            data=body.data,
        )

    @app.post("/api/v1/experiments/{experiment_id}/draft/agents/batch")
    def batch_update_agents(experiment_id: str, body: BatchAgentRequest):
        return service.batch_update_agents(
            experiment_id,
            expected_lock_version=body.lock_version,
            agent_keys=body.agent_keys,
            changes=body.changes,
            dry_run=body.dry_run,
        )

    @app.patch("/api/v1/experiments/{experiment_id}/draft/agents/{agent_key}")
    def patch_agent(experiment_id: str, agent_key: str, body: DraftUpdateRequest):
        return service.put_draft_agent(
            experiment_id,
            agent_key,
            expected_lock_version=body.lock_version,
            data=body.data,
            partial=True,
        )

    @app.delete("/api/v1/experiments/{experiment_id}/draft/agents/{agent_key}")
    def delete_agent(experiment_id: str, agent_key: str, body: DraftUpdateRequest):
        return service.delete_draft_agent(
            experiment_id,
            agent_key,
            expected_lock_version=body.lock_version,
        )

    @app.post("/api/v1/experiments/{experiment_id}/draft/validate")
    def validate_draft(experiment_id: str):
        report = service.validate_draft(experiment_id)
        model_status = model_probe_service.status_summary(experiment_id)
        # Publish-and-run performs one authoritative probe for every configured
        # model service and pins ``auto`` to the discovered model IDs before the
        # immutable Revision is created. An untested model is therefore pending
        # automatic work, not a task the user must complete model by model.
        report["errors"] = [
            issue
            for issue in report.get("errors", [])
            if issue.get("code") != "MODEL_NOT_RESOLVED"
        ]
        report["valid"] = not report["errors"]
        automatic_model_checks = len(model_status["items"])
        model_status["auto_probe_on_publish"] = True
        report["model_status"] = model_status
        report["auto_model_probe"] = {
            "enabled": True,
            "purposes": [item["purpose"] for item in model_status["items"]],
            "count": automatic_model_checks,
        }
        report["counts"] = {
            "blocking": len(report["errors"]),
            "warning": len(report.get("warnings", [])),
            "automatic": automatic_model_checks,
            "passed": max(
                0,
                8
                - len(report["errors"])
                - len(report.get("warnings", []))
                - automatic_model_checks,
            ),
        }
        return report

    @app.post("/api/v1/experiments/{experiment_id}/draft/models/{purpose}/test")
    def test_model_connection(
        experiment_id: str,
        purpose: Literal["chat", "embedding"],
        body: ModelProbeRequest,
    ):
        return model_probe_service.probe(
            experiment_id, purpose, expected_lock_version=body.lock_version
        )

    @app.get("/api/v1/experiments/{experiment_id}/draft/models/status")
    def model_connection_status(experiment_id: str):
        return model_probe_service.status_summary(experiment_id)

    @app.get("/api/v1/experiments/{experiment_id}/revisions")
    def list_revisions(experiment_id: str):
        return {"items": service.list_revisions(experiment_id)}

    @app.get("/api/v1/experiments/{experiment_id}/revisions/{revision_id}")
    def get_revision(experiment_id: str, revision_id: str):
        return service.get_revision(experiment_id, revision_id)

    @app.post("/api/v1/experiments/{experiment_id}/revisions/{revision_id}/fork")
    def fork_revision(experiment_id: str, revision_id: str):
        return service.fork_revision(experiment_id, revision_id)

    @app.post(
        "/api/v1/experiments/{experiment_id}/actions/publish-and-run",
        status_code=202,
    )
    def publish_and_run(experiment_id: str, body: PublishAndRunRequest):
        return run_service.publish_and_run(
            experiment_id,
            draft_revision_id=body.draft_revision_id,
            expected_lock_version=body.lock_version,
        )

    @app.post(
        "/api/v1/experiments/{experiment_id}/revisions/{revision_id}/runs",
        status_code=202,
    )
    def run_published_revision(experiment_id: str, revision_id: str):
        return run_service.create_from_published(experiment_id, revision_id)

    @app.get("/api/v1/experiments/{experiment_id}/runs")
    def list_runs(
        experiment_id: str,
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return run_service.list_runs(experiment_id, cursor=cursor, limit=limit)

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str):
        return run_service.get_run(run_id)

    @app.post("/api/v1/runs/{run_id}/pause")
    def pause_run(run_id: str):
        return run_service.pause(run_id)

    @app.post("/api/v1/runs/{run_id}/resume")
    def resume_run(run_id: str):
        return run_service.resume_paused(run_id)

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel_run(run_id: str, body: CancelRunRequest):
        return run_service.cancel(run_id, force=body.force)

    def global_event_page(*, after_id: int, limit: int, tail: bool = False):
        """Return the global RunEvent cursor without duplicating runtime state."""

        with database.session_factory() as session:
            if tail:
                cursor = session.scalar(select(func.max(RunEvent.id))) or after_id
                return {"items": [], "next_after_id": max(after_id, cursor)}
            rows = list(
                session.execute(
                    select(RunEvent, Run.experiment_id)
                    .join(Run, Run.id == RunEvent.run_id)
                    .where(RunEvent.id > after_id)
                    .order_by(RunEvent.id)
                    .limit(limit)
                )
            )
            return {
                "items": [
                    {
                        "id": event.id,
                        "event_type": event.event_type,
                        "experiment_id": experiment_id,
                        "run_id": event.run_id,
                        "payload": event.payload_json,
                        "created_at": event.created_at.isoformat(),
                    }
                    for event, experiment_id in rows
                ],
                "next_after_id": rows[-1][0].id if rows else after_id,
            }

    @app.get("/api/v1/events")
    def global_events(
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        tail: bool = False,
    ):
        return global_event_page(after_id=after_id, limit=limit, tail=tail)

    @app.get("/api/v1/events/stream")
    async def stream_global_events(
        request: Request,
        after_id: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        if last_event_id:
            try:
                after_id = max(after_id, int(last_event_id))
            except ValueError as exc:
                raise ServiceError(
                    "INVALID_EVENT_CURSOR",
                    "Last-Event-ID 必须是非负整数",
                    status_code=422,
                ) from exc

        async def events():
            cursor = after_id
            sync_at = asyncio.get_running_loop().time()
            yield "retry: 1000\n\n"
            yield (
                "event: sync\n"
                f"data: {json.dumps({'cursor': cursor}, separators=(',', ':'))}\n\n"
            )
            while not await request.is_disconnected():
                page = global_event_page(after_id=cursor, limit=100)
                if page["items"]:
                    for item in page["items"]:
                        cursor = item["id"]
                        yield (
                            f"id: {cursor}\n"
                            "event: activity\n"
                            f"data: {json.dumps(item, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        )
                    sync_at = asyncio.get_running_loop().time()
                    continue
                now = asyncio.get_running_loop().time()
                if now - sync_at >= 10:
                    yield (
                        "event: sync\n"
                        f"data: {json.dumps({'cursor': cursor}, separators=(',', ':'))}\n\n"
                    )
                    sync_at = now
                await asyncio.sleep(0.5)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/v1/runs/{run_id}/events")
    def run_events(
        run_id: str,
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        run_service.get_run(run_id)
        with database.session_factory() as session:
            events = list(
                session.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.id > after_id)
                    .order_by(RunEvent.id)
                    .limit(limit)
                )
            )
            return {
                "items": [
                    {
                        "id": event.id,
                        "event_type": event.event_type,
                        "payload": event.payload_json,
                        "created_at": event.created_at.isoformat(),
                    }
                    for event in events
                ],
                "next_after_id": events[-1].id if events else after_id,
            }

    @app.get("/api/v1/runs/{run_id}/events/stream")
    async def stream_run_events(
        run_id: str,
        request: Request,
        after_id: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        run_service.get_run(run_id)
        if last_event_id:
            try:
                after_id = max(after_id, int(last_event_id))
            except ValueError as exc:
                raise ServiceError(
                    "INVALID_EVENT_CURSOR",
                    "Last-Event-ID 必须是非负整数",
                    status_code=422,
                ) from exc

        async def events():
            cursor = after_id
            heartbeat_at = asyncio.get_running_loop().time()
            yield "retry: 1000\n\n"
            while not await request.is_disconnected():
                with database.session_factory() as session:
                    rows = list(
                        session.scalars(
                            select(RunEvent)
                            .where(RunEvent.run_id == run_id, RunEvent.id > cursor)
                            .order_by(RunEvent.id)
                            .limit(100)
                        )
                    )
                if rows:
                    for event in rows:
                        cursor = event.id
                        payload = {
                            "id": event.id,
                            "event_type": event.event_type,
                            "payload": event.payload_json,
                            "created_at": event.created_at.isoformat(),
                        }
                        yield (
                            f"id: {event.id}\n"
                            f"event: {event.event_type}\n"
                            f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
                        )
                    heartbeat_at = asyncio.get_running_loop().time()
                    continue
                now = asyncio.get_running_loop().time()
                if now - heartbeat_at >= 15:
                    yield f": heartbeat {cursor}\n\n"
                    heartbeat_at = now
                await asyncio.sleep(0.5)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/v1/runtime/capacity")
    def runtime_capacity():
        with database.session_factory() as session:
            active = session.scalar(
                select(func.count()).select_from(Run).where(Run.slot_no.is_not(None))
            )
            queued = session.scalar(select(func.count()).select_from(RunQueue))
        max_runs = max_concurrent_runs
        return {
            "max_concurrent_runs": max_runs,
            "active_runs": active,
            "available_slots": max(0, max_runs - active),
            "queued_runs": queued,
        }

    @app.post("/api/v1/assets", status_code=201)
    def upload_asset(file: UploadFile = File(...)):
        return asset_service.upload(
            file.file,
            logical_name=file.filename or "asset",
            media_type=file.content_type,
        )

    @app.post("/api/v1/agent-images", status_code=201)
    def upload_agent_images(
        portrait: UploadFile | None = File(None),
        sprite: UploadFile | None = File(None),
    ):
        images = {}
        if portrait is not None:
            images["portrait"] = (portrait.file, portrait.filename or "portrait.png")
        if sprite is not None:
            images["sprite"] = (sprite.file, sprite.filename or "sprite-4x4.png")
        return asset_service.upload_database_images(images)

    @app.get("/api/v1/agent-images/{asset_id}/content")
    def get_agent_image_content(asset_id: str, request: Request):
        asset, content = asset_service.database_image_content(asset_id)
        etag = f'"{asset.sha256}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            content=content,
            media_type=asset.media_type,
            headers={
                "ETag": etag,
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )

    @app.get("/api/v1/assets/{asset_id}")
    def get_asset(asset_id: str):
        return asset_service.get(asset_id)

    @app.get("/api/v1/assets/{asset_id}/content")
    def get_asset_content(asset_id: str, request: Request):
        asset, path = asset_service.content(asset_id)
        etag = f'"{asset.sha256}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return FileResponse(
            path,
            media_type=asset.media_type,
            filename=asset.logical_name,
            content_disposition_type=(
                "inline" if asset.media_type.startswith("image/") else "attachment"
            ),
            headers={"ETag": etag, "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/api/v1/secrets", status_code=201)
    def create_secret(body: SecretCreateRequest):
        return secret_service.create(kind=body.kind, value=body.value)

    @app.post("/api/v1/secrets/{secret_id}/replacement", status_code=201)
    def replace_secret(secret_id: str, body: SecretCreateRequest):
        return secret_service.create(
            kind=body.kind, value=body.value, supersedes_id=secret_id
        )

    @app.get("/api/v1/runs/{run_id}/results/summary")
    def result_summary(run_id: str):
        return result_service.summary(run_id)

    @app.get("/api/v1/runs/{run_id}/results/timeline")
    def result_timeline(
        run_id: str,
        from_step: int = Query(default=1, ge=1),
        to_step: int | None = Query(default=None, ge=1),
        limit: int = Query(default=200, ge=1, le=500),
    ):
        return result_service.timeline(
            run_id, from_step=from_step, to_step=to_step, limit=limit
        )

    @app.get(
        "/api/v1/runs/{run_id}/replay/manifest",
        response_model=ReplayManifestResponse,
    )
    def replay_manifest(run_id: str):
        return replay_service.manifest(run_id)

    @app.get(
        "/api/v1/runs/{run_id}/replay/steps",
        response_model=ReplayStepsResponse,
    )
    def replay_steps(
        run_id: str,
        from_step: int = Query(default=1, ge=1),
        limit: int = Query(default=100, ge=1, le=100),
    ):
        return replay_service.steps(run_id, from_step=from_step, limit=limit)

    @app.get("/api/v1/runs/{run_id}/results/agents")
    def result_agents(run_id: str, limit: int = Query(default=100, ge=1, le=500)):
        return result_service.agents(run_id, limit=limit)

    @app.get("/api/v1/runs/{run_id}/results/agents/{agent_key}")
    def result_agent(run_id: str, agent_key: str):
        return result_service.agent(run_id, agent_key)

    @app.get("/api/v1/runs/{run_id}/results/conversations")
    def result_conversations(
        run_id: str,
        agent_key: str | None = None,
        q: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return result_service.conversations(
            run_id, agent_key=agent_key, query=q, offset=offset, limit=limit
        )

    @app.get("/api/v1/runs/{run_id}/results/conversations/{conversation_id}")
    def result_conversation(run_id: str, conversation_id: str):
        return result_service.conversation(run_id, conversation_id)

    @app.get("/api/v1/runs/{run_id}/results/memories")
    def result_memories(
        run_id: str,
        agent_key: str | None = None,
        memory_type: str | None = None,
        state: str | None = None,
        q: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return result_service.memories(
            run_id,
            agent_key=agent_key,
            memory_type=memory_type,
            state=state,
            query=q,
            offset=offset,
            limit=limit,
        )

    @app.get("/api/v1/runs/{run_id}/results/operations")
    def result_operations(run_id: str):
        return result_service.operations(run_id)

    @app.get(
        "/api/v1/runs/{run_id}/attempts",
        response_model=AttemptListResponse,
    )
    def list_run_attempts(run_id: str):
        return log_service.list_attempts(run_id)

    @app.get(
        "/api/v1/runs/{run_id}/attempts/{attempt_id}/log",
        response_model=LogWindowResponse,
    )
    def read_attempt_log(
        run_id: str,
        attempt_id: str,
        cursor: int = Query(default=0, ge=0),
        limit_bytes: int = Query(default=65_536, ge=1, le=262_144),
        tail: bool = False,
        file_id: str | None = None,
    ):
        return log_service.read_attempt_log(
            run_id,
            attempt_id,
            cursor=cursor,
            limit_bytes=limit_bytes,
            tail=tail,
            file_id=file_id,
        )

    def _stream_cursor(
        cursor: int, file_id: str | None, last_event_id: str | None
    ) -> tuple[int, str | None]:
        if last_event_id is None:
            return cursor, file_id
        try:
            identity, raw_cursor = last_event_id.rsplit(":", 1)
            value = int(raw_cursor)
            if not identity or any(character not in "0123456789abcdef" for character in identity):
                raise ValueError
        except ValueError as exc:
            raise ServiceError(
                "INVALID_LOG_EVENT_ID",
                "Last-Event-ID 必须是服务端签发的日志游标",
                status_code=422,
            ) from exc
        if value < 0:
            raise ServiceError(
                "INVALID_BYTE_CURSOR", "Last-Event-ID 不能为负数", status_code=422
            )
        return value, identity

    async def _tail_log(reader, *, cursor: int, file_id: str | None):
        current = cursor
        identity = file_id
        heartbeat = 0
        while True:
            try:
                page = await asyncio.to_thread(
                    reader,
                    cursor=current,
                    limit_bytes=65_536,
                    tail=False,
                    file_id=identity,
                )
            except ServiceError as exc:
                payload = json.dumps(
                    {
                        "error": {
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details,
                        }
                    },
                    ensure_ascii=False,
                )
                yield f"event: error\ndata: {payload}\n\n"
                return
            identity = page["file_id"]
            consumed_cursor = (
                page["size_bytes"] if page["next_cursor"] is None else page["next_cursor"]
            )
            if consumed_cursor > current:
                current = consumed_cursor
                payload = json.dumps(page, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {identity}:{current}\nevent: log\ndata: {payload}\n\n"
                heartbeat = 0
            if page["eof"] and page["terminal"]:
                yield (
                    f"id: {identity}:{current}\nevent: eof\n"
                    f"data: {{\"cursor\":{current},\"file_id\":\"{identity}\"}}\n\n"
                )
                return
            if not page["eof"]:
                # Drain an already available backlog without tail-poll latency.
                continue
            await asyncio.sleep(0.5)
            heartbeat += 1
            if heartbeat >= 20:
                yield ": keepalive\n\n"
                heartbeat = 0

    @app.get("/api/v1/runs/{run_id}/attempts/{attempt_id}/log/stream")
    async def stream_attempt_log(
        run_id: str,
        attempt_id: str,
        cursor: int = Query(default=0, ge=0),
        file_id: str | None = None,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        start, stream_file_id = _stream_cursor(cursor, file_id, last_event_id)
        # Resolve ownership and the first page before response headers are sent.
        log_service.read_attempt_log(
            run_id, attempt_id, cursor=start, limit_bytes=1, file_id=stream_file_id
        )
        return StreamingResponse(
            _tail_log(
                lambda **kwargs: log_service.read_attempt_log(
                    run_id, attempt_id, **kwargs
                ),
                cursor=start,
                file_id=stream_file_id,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/runs/{run_id}/attempts/{attempt_id}/log/download")
    def download_attempt_log(run_id: str, attempt_id: str):
        attempt, path = log_service.attempt_log_content(run_id, attempt_id)
        return FileResponse(
            path,
            media_type="text/plain; charset=utf-8",
            filename=f"attempt-{attempt.attempt_no:03d}.log",
            content_disposition_type="attachment",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get(
        "/api/v1/runs/{run_id}/artifact-jobs/{job_id}/log",
        response_model=LogWindowResponse,
    )
    def read_artifact_job_log(
        run_id: str,
        job_id: str,
        cursor: int = Query(default=0, ge=0),
        limit_bytes: int = Query(default=65_536, ge=1, le=262_144),
        tail: bool = False,
        file_id: str | None = None,
    ):
        return log_service.read_artifact_log(
            run_id,
            job_id,
            cursor=cursor,
            limit_bytes=limit_bytes,
            tail=tail,
            file_id=file_id,
        )

    @app.get("/api/v1/runs/{run_id}/artifact-jobs/{job_id}/log/stream")
    async def stream_artifact_job_log(
        run_id: str,
        job_id: str,
        cursor: int = Query(default=0, ge=0),
        file_id: str | None = None,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        start, stream_file_id = _stream_cursor(cursor, file_id, last_event_id)
        log_service.read_artifact_log(
            run_id, job_id, cursor=start, limit_bytes=1, file_id=stream_file_id
        )
        return StreamingResponse(
            _tail_log(
                lambda **kwargs: log_service.read_artifact_log(run_id, job_id, **kwargs),
                cursor=start,
                file_id=stream_file_id,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/runs/{run_id}/artifact-jobs/{job_id}/log/download")
    def download_artifact_job_log(run_id: str, job_id: str):
        _job, path = log_service.artifact_log_content(run_id, job_id)
        return FileResponse(
            path,
            media_type="text/plain; charset=utf-8",
            filename=f"artifact-job-{job_id}.log",
            content_disposition_type="attachment",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get(
        "/api/v1/runs/{run_id}/model-traces",
        response_model=ModelTracePageResponse,
    )
    def list_model_traces(
        run_id: str,
        attempt_id: str,
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
        purpose: str | None = None,
        status: str | None = None,
        event_type: str | None = None,
    ):
        return log_service.model_traces(
            run_id,
            attempt_id,
            cursor=cursor,
            limit=limit,
            purpose=purpose,
            status=status,
            event_type=event_type,
        )

    @app.get(
        "/api/v1/runs/{run_id}/model-traces/{trace_id}",
        response_model=ModelTraceDetailResponse,
    )
    def get_model_trace(
        run_id: str,
        trace_id: str,
        cursor: int = Query(default=0, ge=0),
        limit_bytes: int = Query(default=16_384, ge=1, le=65_536),
    ):
        return log_service.trace_detail(
            run_id, trace_id, cursor=cursor, limit_bytes=limit_bytes
        )

    @app.get(
        "/api/v1/runs/{run_id}/checkpoints",
        response_model=CheckpointListResponse,
    )
    def list_run_checkpoints(run_id: str):
        return checkpoint_service.list_checkpoints(run_id)

    @app.get(
        "/api/v1/runs/{run_id}/checkpoints/{step_no}",
        response_model=CheckpointDetailResponse,
    )
    def get_run_checkpoint(run_id: str, step_no: int):
        if step_no < 1:
            raise ServiceError(
                "INVALID_CHECKPOINT_STEP", "检查点步数必须为正数", status_code=422
            )
        return checkpoint_service.detail(run_id, step_no)

    @app.get(
        "/api/v1/runs/{run_id}/checkpoints/{step_no}/preview",
        response_model=CheckpointPreviewResponse,
    )
    def preview_run_checkpoint(
        run_id: str,
        step_no: int,
        section: str,
        cursor: int = Query(default=0, ge=0),
        limit_bytes: int = Query(default=32_768, ge=1, le=262_144),
        file_id: str | None = None,
    ):
        return checkpoint_service.preview(
            run_id,
            step_no,
            section,
            cursor=cursor,
            limit_bytes=limit_bytes,
            file_id=file_id,
        )

    @app.post(
        "/api/v1/runs/{run_id}/checkpoints/{step_no}/artifact-job",
        status_code=202,
    )
    def create_checkpoint_artifact_job(run_id: str, step_no: int):
        checkpoint_service.validate_for_export(run_id, step_no)
        return artifact_service.create_job(
            run_id,
            job_type="CHECKPOINT_BUNDLE",
            parameters={"checkpoint_step": step_no},
        )

    @app.post("/api/v1/runs/{run_id}/artifact-jobs", status_code=202)
    def create_artifact_job(run_id: str, body: ArtifactJobRequest):
        return artifact_service.create_job(
            run_id, job_type=body.job_type, parameters=body.parameters
        )

    @app.get("/api/v1/artifact-jobs/{job_id}")
    def get_artifact_job(job_id: str):
        return artifact_service.get_job(job_id)

    @app.get("/api/v1/runs/{run_id}/artifacts")
    def list_artifacts(run_id: str):
        return artifact_service.list_artifacts(run_id)

    @app.get("/api/v1/runs/{run_id}/artifacts/{artifact_id}")
    def get_artifact(run_id: str, artifact_id: str):
        return artifact_service.get_artifact(run_id, artifact_id)

    @app.get("/api/v1/runs/{run_id}/artifacts/{artifact_id}/preview")
    def preview_artifact(
        run_id: str,
        artifact_id: str,
        cursor: int = Query(default=0, ge=0),
        limit_bytes: int = Query(default=65_536, ge=1, le=262_144),
    ):
        with artifact_service.open_content(run_id, artifact_id) as (
            artifact,
            _path,
            handle,
        ):
            if artifact.media_type not in {"application/json", "text/plain", "text/markdown"}:
                raise ServiceError(
                    "ARTIFACT_NOT_PREVIEWABLE", "该制品类型不支持文本预览", status_code=415
                )
            window = read_utf8_handle(
                handle,
                cursor=cursor,
                limit_bytes=limit_bytes,
                truncated_code="ARTIFACT_CONTENT_TRUNCATED",
                encoding_code="ARTIFACT_ENCODING_INVALID",
            )
        return {
            "artifact_id": artifact.id,
            "cursor": window.start_cursor,
            "content": window.content,
            "next_cursor": None if window.eof else window.next_cursor,
            "size_bytes": window.size_bytes,
            "file_id": window.file_id,
            "eof": window.eof,
        }

    @app.get("/api/v1/runs/{run_id}/artifacts/{artifact_id}/download")
    def download_artifact(run_id: str, artifact_id: str):
        opened = artifact_service.open_content(run_id, artifact_id)
        artifact, _path, handle = opened.__enter__()

        def verified_chunks():
            try:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    yield block
            finally:
                opened.__exit__(None, None, None)

        return StreamingResponse(
            verified_chunks(),
            media_type=artifact.media_type,
            headers={
                "ETag": f'"{artifact.sha256}"',
                "X-Content-Type-Options": "nosniff",
                "Content-Length": str(artifact.size_bytes),
                "Content-Disposition": (
                    "attachment; filename*=UTF-8''"
                    + quote(artifact.logical_name, safe="")
                ),
            },
        )

    return app
