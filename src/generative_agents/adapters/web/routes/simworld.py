"""Studio's host-configured bridge to the independently deployed SimWorld service."""
from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, HTTPException, Response

from generative_agents.adapters.web.simworld_client import simworld_connection


def service_response(operation):
    try:
        with httpx.Client(timeout=httpx.Timeout(90, connect=4), **simworld_connection()) as client:
            with client.stream("GET", operation) as upstream:
                if not upstream.is_success:
                    detail = ""
                    try:
                        part = next(upstream.iter_bytes(2048), b"")
                        detail = str(json.loads(part).get("error", ""))[:240]
                    except (UnicodeError, ValueError, AttributeError):
                        pass
                    raise HTTPException(upstream.status_code if 400 <= upstream.status_code < 600 else 502,
                                        detail or f"SimWorld 服务未完成操作 {operation}（{upstream.status_code}）")
                if upstream.headers.get("content-type", "").split(";")[0] != "application/json":
                    raise HTTPException(502, "SimWorld 返回了不匹配的内容类型")
                content = bytearray()
                for chunk in upstream.iter_bytes(65536):
                    content.extend(chunk)
                    if len(content) > 1024 * 1024:
                        raise HTTPException(413, "SimWorld 能力响应过大")
                return Response(bytes(content), media_type="application/json", headers={"Cache-Control": "no-store"})
    except httpx.RequestError as exc:
        raise HTTPException(503, "无法连接独立 SimWorld 服务，请检查服务状态与主机服务地址") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(502, f"SimWorld 返回的素材无效：{exc}") from exc


def create_simworld_router():
    router = APIRouter(prefix="/simworld")

    @router.get("/capabilities")
    def capabilities():
        return service_response("capabilities")

    return router
