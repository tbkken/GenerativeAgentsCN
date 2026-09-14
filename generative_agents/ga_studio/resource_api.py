"""HTTP editing surface for mutable Map and Skill author resources."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from generative_agents.config.schema import WorldConfig
from generative_agents.config.spatial_assets import SpatialAssetContract
from generative_agents.services.errors import ServiceError
from generative_agents.services.map_importer import fresh_ville_editor_document
from generative_agents.services.maps import WorldMapService, normalize_public_world
from generative_agents.ga_protocol.navigation import collision_preview, grid_path
from generative_agents.services.spatial_assets import SpatialAssetService
from generative_agents.skills import SkillRegistryError
from .skill_trial import SkillTrialError, run_skill_trial


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MapCreate(Request):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    map_key: str | None = None
    source_map_id: str | None = None
    blueprint_key: str | None = None
    width: int = Field(default=48, ge=1, le=240)
    height: int = Field(default=32, ge=1, le=240)
    tile_size: int = Field(default=32, ge=8, le=128)


class MapUpdate(Request):
    row_version: int = Field(ge=1)
    world: WorldConfig


class MapBlueprintStep(Request):
    row_version: int = Field(ge=1)


class NavigationPreview(Request):
    world: WorldConfig
    slice_id: str | None = None
    start: tuple[int, int] | None = None
    end: tuple[int, int] | None = None


class SkillCreate(Request):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=2_000)
    kind: Literal["atomic", "pack", "brain"] = "atomic"


class SkillUpdate(Request):
    markdown: str = Field(min_length=1, max_length=200_000)
    scripts: dict[str, str] | None = None


class SkillTrial(Request):
    input_text: str = Field(min_length=1, max_length=100_000)
    context: dict[str, Any] = Field(default_factory=dict)
    model_preset_id: str | None = None


class SpatialAssetCreate(Request):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    asset_key: str | None = None
    asset_kind: Literal["TILE", "OBJECT", "ZONE", "MARKING", "NETWORK"] = "TILE"
    contract: SpatialAssetContract | None = None


class SpatialAssetUpdate(Request):
    row_version: int = Field(ge=1)
    contract: SpatialAssetContract
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None


def create_resource_router(database, skill_registry) -> APIRouter:
    router = APIRouter(prefix="/api/studio/resources", tags=["studio-resources"])
    maps = WorldMapService(database, skill_registry=skill_registry)
    spatial_assets = SpatialAssetService(database)

    def call(operation):
        try:
            return operation()
        except ServiceError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": exc.message, "details": exc.details},
            ) from exc
        except SkillRegistryError as exc:
            status = 404 if "does not exist" in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @router.get("/map-editor/ville-document")
    def get_ville_map_editor_document():
        """Return the bundled authoring document used by the established editor."""

        return fresh_ville_editor_document().model_dump(mode="json")

    @router.get("/maps")
    def list_maps(
        q: str | None = None,
        archived: Literal["active", "archived", "all"] = "active",
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
    ):
        return call(
            lambda: maps.list_maps(
                query=q,
                archived=archived,
                page=page,
                page_size=page_size,
            )
        )

    @router.post("/map-editor/navigation")
    def preview_navigation(body: NavigationPreview):
        """Read-only check of the current editor buffer; never saves a map."""
        def preview():
            world = normalize_public_world(body.world)
            try:
                result = collision_preview(world.definition, slice_id=body.slice_id)
            except ValueError as exc:
                raise ServiceError("MAP_NAVIGATION_INVALID", str(exc), status_code=422) from exc
            if body.start is not None and body.end is not None:
                width, height = result["width"], result["height"]
                blocked = set(result["blocked"])
                def state(p):
                    x, y = p
                    if not (0 <= x < width and 0 <= y < height):
                        return "OUT_OF_BOUNDS"
                    return "BLOCKED" if y * width + x in blocked else "OPEN"
                start_state, end_state = state(body.start), state(body.end)
                path = grid_path(width, height, lambda x, y: y * width + x in blocked,
                                 body.start, body.end)
                result.update(path=path, distance_tiles=len(path) - 1 if path else None,
                              status=("START_" + start_state if start_state != "OPEN" else
                                      "END_" + end_state if end_state != "OPEN" else
                                      "REACHABLE" if path else "UNREACHABLE"))
            return result
        return call(preview)

    @router.post("/maps", status_code=201)
    def create_map(body: MapCreate):
        return call(
            lambda: maps.create_map(
                name=body.name,
                description=body.description,
                map_key=body.map_key,
                source_map_id=body.source_map_id,
                blueprint_key=body.blueprint_key,
                width=body.width,
                height=body.height,
                tile_size=body.tile_size,
            )
        )

    @router.get("/map-blueprints")
    def list_map_blueprints():
        return {"items": maps.list_blueprints()}

    @router.get("/maps/{map_id}")
    def get_map(map_id: str):
        return call(lambda: maps.get_map(map_id))

    @router.put("/maps/{map_id}")
    def update_map(map_id: str, body: MapUpdate):
        return call(
            lambda: maps.update_map(
                map_id,
                expected_lock_version=body.row_version,
                world=body.world,
            )
        )

    @router.post("/maps/{map_id}/validate")
    def validate_map(map_id: str, row_version: int = Query(ge=1)):
        return call(
            lambda: maps.validate_map(map_id, expected_lock_version=row_version)
        )

    @router.post("/maps/{map_id}/blueprint-steps/{step}")
    def apply_map_blueprint_step(map_id: str, step: int, body: MapBlueprintStep):
        return call(
            lambda: maps.apply_blueprint_step(
                map_id,
                expected_lock_version=body.row_version,
                step=step,
            )
        )

    @router.delete("/maps/{map_id}", status_code=204)
    def delete_map(map_id: str):
        call(lambda: maps.delete_map(map_id))
        return Response(status_code=204)

    @router.post("/maps/{map_id}/archive")
    def archive_map(map_id: str):
        return call(lambda: maps.set_archived(map_id, archived=True))

    @router.post("/maps/{map_id}/restore")
    def restore_map(map_id: str):
        return call(lambda: maps.set_archived(map_id, archived=False))

    @router.get("/spatial-assets")
    def list_spatial_assets(
        q: str | None = None,
        kind: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=100, ge=1, le=100),
    ):
        return call(
            lambda: spatial_assets.list_assets(
                query=q,
                asset_kind=kind,
                page=page,
                page_size=page_size,
            )
        )

    @router.post("/spatial-assets", status_code=201)
    def create_spatial_asset(body: SpatialAssetCreate):
        return call(
            lambda: spatial_assets.create_asset(
                name=body.name,
                description=body.description,
                asset_key=body.asset_key,
                asset_kind=body.asset_kind,
                contract=body.contract,
            )
        )

    @router.get("/spatial-assets/{asset_id}")
    def get_spatial_asset(asset_id: str):
        return call(lambda: spatial_assets.get_asset(asset_id))

    @router.put("/spatial-assets/{asset_id}")
    def update_spatial_asset(asset_id: str, body: SpatialAssetUpdate):
        return call(
            lambda: spatial_assets.update_asset(
                asset_id,
                expected_row_version=body.row_version,
                contract=body.contract,
                name=body.name,
                description=body.description,
            )
        )

    @router.delete("/spatial-assets/{asset_id}", status_code=204)
    def delete_spatial_asset(asset_id: str):
        call(lambda: spatial_assets.delete_asset(asset_id))
        return Response(status_code=204)

    @router.get("/skills")
    def list_skills(
        kind: Literal["atomic", "pack", "brain"] | None = None,
        q: str = Query(default="", max_length=200),
        include_archived: bool = False,
    ):
        documents = call(
            lambda: skill_registry.list(
                kind=kind,
                query=q,
                include_archived=include_archived,
            )
        )
        counts = {
            item_kind: len(
                call(
                    lambda item_kind=item_kind: skill_registry.list(
                        kind=item_kind,
                        query="",
                        include_archived=include_archived,
                    )
                )
            )
            for item_kind in ("atomic", "pack", "brain")
        }
        return {
            "items": [item.summary() for item in documents],
            "total": len(documents),
            "counts": counts,
        }

    @router.post("/skills", status_code=201)
    def create_skill(body: SkillCreate):
        return call(
            lambda: skill_registry.create(
                name=body.name,
                description=body.description,
                kind=body.kind,
            ).detail()
        )

    @router.get("/skills/{skill_name}")
    def get_skill(skill_name: str):
        def detail() -> dict[str, Any]:
            document = skill_registry.get(skill_name)
            return {
                **document.detail(),
                "script_sources": skill_registry.script_sources(skill_name),
            }

        return call(detail)

    @router.post("/skills/{skill_name}/run")
    def trial_skill(skill_name: str, body: SkillTrial):
        try:
            return call(lambda: run_skill_trial(database, skill_registry, skill_name, body.input_text, body.context, model_preset_id=body.model_preset_id))
        except SkillTrialError as exc:
            raise HTTPException(status_code=exc.status, detail={
                "code": exc.code, "message": str(exc), "trace": exc.trace,
            }) from exc

    @router.get("/skills/{skill_name}/dependencies")
    def get_skill_dependencies(skill_name: str):
        return call(lambda: skill_registry.dependencies(skill_name))

    @router.get("/skills/{skill_name}/history")
    def get_skill_history(skill_name: str):
        return {"items": call(lambda: skill_registry.history(skill_name))}

    @router.put("/skills/{skill_name}")
    def update_skill(skill_name: str, body: SkillUpdate):
        return call(
            lambda: skill_registry.save(
                skill_name,
                body.markdown,
                scripts=body.scripts,
            ).detail()
        )

    @router.delete("/skills/{skill_name}", status_code=204)
    def delete_skill(skill_name: str):
        call(lambda: skill_registry.delete(skill_name))
        return Response(status_code=204)

    @router.post("/skills/{skill_name}/archive")
    def archive_skill(skill_name: str):
        return call(lambda: skill_registry.archive(skill_name).detail())

    @router.post("/skills/{skill_name}/restore")
    def restore_skill(skill_name: str):
        return call(lambda: skill_registry.restore(skill_name).detail())

    return router


__all__ = ["create_resource_router"]
