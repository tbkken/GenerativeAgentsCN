"""REST and MCP endpoints for the file-backed Skill platform."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from generative_agents.skills import (
    SkillMCPServer,
    SkillRegistry,
    SkillRegistryError,
    SkillRuntime,
    SkillRuntimeError,
)


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSkillRequest(RequestModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=2_000)
    kind: Literal["atomic", "pack", "brain"] = "atomic"


class SaveSkillRequest(RequestModel):
    markdown: str = Field(min_length=1, max_length=200_000)


class RunSkillRequest(RequestModel):
    input_text: str = Field(min_length=1, max_length=100_000)
    context: dict[str, Any] = Field(default_factory=dict)


def create_skill_router(
    registry: SkillRegistry,
    runtime: SkillRuntime,
    mcp: SkillMCPServer,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/skills")
    def list_skills(
        kind: Literal["atomic", "pack", "brain"] | None = None,
        q: str = Query(default="", max_length=200),
    ):
        documents = registry.list(kind=kind, query=q)
        return {
            "items": [document.summary() for document in documents],
            "total": len(documents),
            "counts": {
                item_kind: len(registry.list(kind=item_kind))
                for item_kind in ("atomic", "pack", "brain")
            },
        }

    @router.post("/api/v1/skills", status_code=201)
    def create_skill(body: CreateSkillRequest):
        return _skill_call(
            lambda: registry.create(
                name=body.name,
                description=body.description,
                kind=body.kind,
            ).detail()
        )

    @router.get("/api/v1/skills/{skill_name}")
    def get_skill(skill_name: str):
        return _skill_call(lambda: registry.get(skill_name).detail())

    @router.put("/api/v1/skills/{skill_name}")
    def save_skill(skill_name: str, body: SaveSkillRequest):
        return _skill_call(lambda: registry.save(skill_name, body.markdown).detail())

    @router.get("/api/v1/skills/{skill_name}/dependencies")
    def get_skill_dependencies(skill_name: str):
        return _skill_call(lambda: registry.dependencies(skill_name))

    @router.get("/api/v1/skills/{skill_name}/history")
    def get_skill_history(skill_name: str):
        return _skill_call(lambda: {"items": registry.history(skill_name)})

    @router.post("/api/v1/skills/{skill_name}/run")
    def run_skill(skill_name: str, body: RunSkillRequest):
        return _skill_call(
            lambda: runtime.run(
                skill_name,
                body.input_text,
                context=body.context,
            ).as_dict()
        )

    @router.get("/api/v1/skill-runtime")
    def get_skill_runtime():
        return {
            "base_url": runtime.base_url,
            "chat_completions_url": f"{runtime.base_url}/chat/completions",
            "model": runtime.model,
            "handoff": "natural-language",
            "business_schema_required": False,
        }

    @router.get("/api/v1/mcp/tools")
    def list_mcp_tools():
        return {"items": mcp.tools()}

    @router.post("/mcp")
    def call_mcp(request: dict[str, Any]):
        return mcp.handle(request)

    return router


def _skill_call(operation):
    try:
        return operation()
    except SkillRegistryError as exc:
        message = str(exc)
        status = 404 if "does not exist" in message else 422
        raise HTTPException(status_code=status, detail=message) from exc
    except SkillRuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
