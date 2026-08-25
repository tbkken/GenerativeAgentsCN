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
    """创建技能`router`。

    参数:
        registry: 按稳定键解析技能、模型或其他组件的注册表。 类型：`SkillRegistry`。
        runtime: 传入当前算法的`runtime`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`SkillRuntime`。
        mcp: 技能调用使用的 MCP 服务端或客户端适配器。 类型：`SkillMCPServer`。

    返回:
        返回 `APIRouter` 类型的处理结果。
    """
    router = APIRouter()

    @router.get("/api/v1/skills")
    def list_skills(
        kind: Literal["atomic", "pack", "brain"] | None = None,
        q: str = Query(default="", max_length=200),
    ):
        """查询`skills`。

        参数:
            kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`Literal['atomic', 'pack', 'brain'] | None`。 默认值：`None`。
            q: 全文搜索关键字的简写；为空时不应用文本筛选。 类型：`str`。

        返回:
            返回函数计算得到的结果。
        """
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
        """创建技能。

        参数:
            body: 已经解析的请求体或命令载荷；字段由对应接口模型定义。 类型：`CreateSkillRequest`。

        返回:
            返回函数计算得到的结果。
        """
        return _skill_call(
            lambda: registry.create(
                name=body.name,
                description=body.description,
                kind=body.kind,
            ).detail()
        )

    @router.get("/api/v1/skills/{skill_name}")
    def get_skill(skill_name: str):
        """获取技能。

        参数:
            skill_name: 需要调用的技能名称，必须能在当前运行的技能快照中解析。 类型：`str`。

        返回:
            返回函数计算得到的结果。
        """
        return _skill_call(lambda: registry.get(skill_name).detail())

    @router.put("/api/v1/skills/{skill_name}")
    def save_skill(skill_name: str, body: SaveSkillRequest):
        """保存技能。

        参数:
            skill_name: 需要调用的技能名称，必须能在当前运行的技能快照中解析。 类型：`str`。
            body: 已经解析的请求体或命令载荷；字段由对应接口模型定义。 类型：`SaveSkillRequest`。

        返回:
            返回函数计算得到的结果。
        """
        return _skill_call(lambda: registry.save(skill_name, body.markdown).detail())

    @router.get("/api/v1/skills/{skill_name}/dependencies")
    def get_skill_dependencies(skill_name: str):
        """获取技能`dependencies`。

        参数:
            skill_name: 需要调用的技能名称，必须能在当前运行的技能快照中解析。 类型：`str`。

        返回:
            返回函数计算得到的结果。
        """
        return _skill_call(lambda: registry.dependencies(skill_name))

    @router.get("/api/v1/skills/{skill_name}/history")
    def get_skill_history(skill_name: str):
        """获取技能`history`。

        参数:
            skill_name: 需要调用的技能名称，必须能在当前运行的技能快照中解析。 类型：`str`。

        返回:
            返回函数计算得到的结果。
        """
        return _skill_call(lambda: {"items": registry.history(skill_name)})

    @router.post("/api/v1/skills/{skill_name}/run")
    def run_skill(skill_name: str, body: RunSkillRequest):
        """执行 的运行技能操作。

        参数:
            skill_name: 需要调用的技能名称，必须能在当前运行的技能快照中解析。 类型：`str`。
            body: 已经解析的请求体或命令载荷；字段由对应接口模型定义。 类型：`RunSkillRequest`。

        返回:
            返回函数计算得到的结果。
        """
        return _skill_call(
            lambda: runtime.run(
                skill_name,
                body.input_text,
                context=body.context,
            ).as_dict()
        )

    @router.get("/api/v1/skill-runtime")
    def get_skill_runtime():
        """获取技能`runtime`。

        返回:
            返回函数计算得到的结果。
        """
        return {
            "base_url": runtime.base_url,
            "chat_completions_url": f"{runtime.base_url}/chat/completions",
            "model": runtime.model,
            "handoff": "natural-language",
            "business_schema_required": False,
        }

    @router.get("/api/v1/mcp/tools")
    def list_mcp_tools():
        """查询`mcp``tools`。

        返回:
            返回函数计算得到的结果。
        """
        return {"items": mcp.tools()}

    @router.post("/mcp")
    def call_mcp(request: dict[str, Any]):
        """执行 的`call``mcp`操作。

        参数:
            request: 待执行、记录或发送到外部模型的请求对象。 类型：`dict[str, Any]`。

        返回:
            返回函数计算得到的结果。
        """
        return mcp.handle(request)

    return router


def _skill_call(operation):
    """执行技能`call`的内部处理，供当前模块或类复用。

    参数:
        operation: 传入当前算法的`operation`；其结构与有效范围由类型注解和调用协议共同限定。

    返回:
        返回函数计算得到的结果。

    异常:
        HTTPException: 当底层操作报告该异常条件时抛出。
    """
    try:
        return operation()
    except SkillRegistryError as exc:
        message = str(exc)
        status = 404 if "does not exist" in message else 422
        raise HTTPException(status_code=status, detail=message) from exc
    except SkillRuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
