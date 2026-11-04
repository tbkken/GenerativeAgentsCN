"""View remote SimWorld scenes and replay only Run-owned committed facts."""
from __future__ import annotations

import json
import math
import re
import time
from contextlib import asynccontextmanager, contextmanager
from threading import Lock
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from generative_agents.adapters.web.simworld_client import simworld_connection
from generative_agents.ga_replay.api import read_run_scene_step, run_scene_reference
from generative_agents.ga_studio.api import ServiceError


SceneOwner = Literal["scenes", "experiments", "runs"]


class CameraPose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    yaw_deg: FiniteFloat
    pitch_deg: FiniteFloat
    fov_deg: FiniteFloat = Field(gt=0, lt=180)


class CameraPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)


class AgentViewSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_key: str = Field(min_length=1, max_length=120)


class ReplaySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_no: int = Field(ge=1, strict=True)
    playback_ms: int = Field(default=0, ge=0, le=3000, strict=True)


class ReplayCaptureSize(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    width: int
    height: int

    @model_validator(mode="after")
    def check_size(self):
        if (self.width,self.height) not in {(640,360),(960,540),(1280,720)}:
            raise ValueError("不支持该回放取景尺寸")
        return self


class SceneSessionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capture_size: ReplayCaptureSize | None = None


def _actor_pose(fact):
    start, end = fact.get("from_coord"), fact.get("to_coord")
    yaw, target_yaw = fact.get("from_yaw_deg"), fact.get("to_yaw_deg")
    path = fact.get("path", []) if fact.get("path_source", "OBSERVED") == "OBSERVED" else []
    if (not isinstance(path, list) or any(not isinstance(point, list) or len(point) != 3
            or not all(type(value) in (int, float) and math.isfinite(value) for value in point)
            for point in (start, end, *path))
            or not all(type(value) in (int, float) and math.isfinite(value) for value in (yaw, target_yaw))):
        raise ValueError("已提交的三维 Agent 事实缺少有效位置或朝向")
    moved = start != end or any(point != start for point in path)
    clip = (fact.get("action") or {}).get("presentation_clip")
    if clip is None:
        clip = "walk" if moved else "turn" if (target_yaw - yaw) % 360 else "idle"
    return {"id": fact["agent_key"], "position": end, "yaw_deg": target_yaw, "clip": clip,
            "from_position": start, "from_yaw_deg": yaw, "path": path}


def _connection():
    return {**simworld_connection(), "timeout": httpx.Timeout(100, connect=5)}


def _check(response):
    if response.is_success:
        return
    status = response.status_code if 400 <= response.status_code < 600 else 502
    if not response.is_stream_consumed:
        detail = next(response.iter_bytes(2048), b"")
    else:
        detail = response.content[:2048]
    try:
        message = json.loads(detail).get("error")
    except (ValueError, AttributeError):
        message = None
    raise HTTPException(status, str(message or "SimWorld 未完成请求")[:350])


def _json(response):
    _check(response)
    if response.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(502, "SimWorld 未返回 JSON")
    doc = response.json()
    if not isinstance(doc, dict):
        raise HTTPException(502, "SimWorld 响应格式错误")
    return doc


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise HTTPException(422, "无效的场景或会话标识")
    return value




def create_scene_session_router(ctx):
    pool = None
    @asynccontextmanager
    async def lifespan(_app):
        nonlocal pool
        try:
            yield
        finally:
            if pool is not None:
                pool.close()
                pool = None

    @contextmanager
    def _client():
        nonlocal pool
        try:
            with lock:
                if pool is None:
                    pool = httpx.Client(**_connection())
            yield pool
        except httpx.RequestError as exc:
            raise HTTPException(503, "无法连接 SimWorld 场景服务，请检查连接后重试") from exc
        except ServiceError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    router = APIRouter(prefix="/simworld", lifespan=lifespan)
    sessions = {}
    lock = Lock()
    scope = "/{owner_kind}/{owner_id}/sessions"

    def session_for(owner_kind, owner_id, session_id):
        key = _id(session_id)
        with lock:
            entry = sessions.get(key)
            if not entry or entry["owner"] != (owner_kind, owner_id):
                raise HTTPException(404, "当前资源的 UE 预览会话不存在或已关闭")
            entry["last_seen"] = time.monotonic()
        return entry

    def authorize(owner_kind, owner_id, session_id):
        return session_for(owner_kind, owner_id, session_id)["upstream"]

    def remember_session(sid, owner_kind, owner_id, **details):
        with lock:
            now = time.monotonic()
            for key in [key for key, session in sessions.items() if now - session["last_seen"] > 3600]:
                del sessions[key]
            if sid in sessions:
                raise HTTPException(502, "SimWorld 返回了仍在使用的会话身份")
            sessions[sid] = {"owner": (owner_kind, owner_id), "last_seen": now, "upstream": f"sessions/{sid}", **details}

    @router.get("/scenes")
    def scenes():
        with _client() as client:
            maps = _json(client.get("maps")).get("maps")
            if not isinstance(maps, list):
                raise HTTPException(502, "SimWorld 地图目录格式错误")
            scenes = []
            for item in maps:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    raise HTTPException(502, "SimWorld 场景条目格式错误")
                availability = item.get("availability")
                scenes.append({"id": _id(item.get("id")), "name": item["name"], "availability": availability,
                               "previewable": availability in ("unverified", "snapshot_ready")})
            return {"scenes": scenes}


    @router.get("/scenes/{scene_id}/placements")
    def placements(scene_id: str):
        with _client() as client:
            return _json(client.get(f"maps/{_id(scene_id)}/placements"))

    @router.post(scope, status_code=201)
    def open_session(owner_kind: SceneOwner, owner_id: str, options: SceneSessionOptions | None = None):
        size = options.capture_size if options else None
        if size is not None and owner_kind != "runs":
            raise HTTPException(422, "只有 Run 回放会话可调整取景尺寸")
        with _client() as client:
            details = {}
            agent_views = []
            if owner_kind == "runs":
                run_location = ctx.run_location(owner_id)
                reference = run_scene_reference(run_location, owner_id)
                scene_id = _id(reference["scene_id"])
                replay = {"run_id": owner_id, "participants": reference["participants"]}
                if size:
                    replay["capture_size"] = size.model_dump()
                body = {"map_id": scene_id, "replay": replay}
                details = {"run_location": run_location, "experiment_sha256": reference["experiment_sha256"], "step_lock": Lock()}
            else:
                if owner_kind == "experiments":
                    scene_id, actors = ctx.workspaces.simworld_scene_selection(owner_id)
                    scene_id = _id(scene_id)
                    if actors:
                        found = _json(client.post(f"maps/{scene_id}/participant-views", json={"actors": actors}))
                        agent_views = found.get("agent_views", [])
                    details["agents"] = {agent["agent_key"]: agent for agent in agent_views}
                else:
                    scene_id = _id(owner_id)
                body = {"map_id": scene_id}

            result = _json(client.post("sessions", json=body))
            sid = _id(result.get("session_id"))
            if owner_kind == "runs" and (result.get("replay") or {}).get("run_id") != owner_id:
                with lock:
                    if sid in sessions:
                        raise HTTPException(502, "SimWorld 返回了仍在使用的会话身份")
                closed = client.delete(f"sessions/{sid}")
                if closed.status_code != 404:
                    _check(closed)
                raise HTTPException(502, "SimWorld 服务未确认此 Run 的角色回放能力，请更新并重启 SimWorld 服务")
            remember_session(sid, owner_kind, owner_id, **details)
            return {**result, "scene_id": scene_id, "agent_views": agent_views}

    @router.get(scope + "/{session_id}/state")
    def state(owner_kind: SceneOwner, owner_id: str, session_id: str):
        with _client() as client:
            return _json(client.get(authorize(owner_kind, owner_id, session_id) + "/state"))

    @router.post(scope + "/{session_id}/pose")
    def pose(owner_kind: SceneOwner, owner_id: str, session_id: str, body: CameraPose):
        with _client() as client:
            return _json(client.post(authorize(owner_kind, owner_id, session_id) + "/pose", json=body.model_dump()))

    @router.post("/runs/{run_id}/sessions/{session_id}/replay")
    def replay_step(run_id: str, session_id: str, body: ReplaySelection):
        session = session_for("runs", run_id, session_id)
        with session["step_lock"], _client() as client:
            frame = read_run_scene_step(session["run_location"], run_id, session["experiment_sha256"], body.step_no)
            payload = {"run_id": run_id, "attempt_id": frame["attempt_id"], "step_no": frame["step_no"],
                       "actors": [_actor_pose(fact) for fact in frame["agents"]], "playback_ms": body.playback_ms}
            payload["object_states"] = frame["object_states"]
            return _json(client.post(session["upstream"] + "/replay", json=payload))

    @router.post("/runs/{run_id}/sessions/{session_id}/capture-size")
    def resize(run_id: str, session_id: str, size: ReplayCaptureSize):
        with _client() as client:
            endpoint = authorize("runs", run_id, session_id) + "/capture-size"
            return _json(client.post(endpoint, json=size.model_dump()))

    @router.post(scope + "/{session_id}/preset")
    def preset(owner_kind: SceneOwner, owner_id: str, session_id: str, body: CameraPreset):
        with _client() as client:
            return _json(client.post(authorize(owner_kind, owner_id, session_id) + "/preset", json=body.model_dump()))

    @router.post("/experiments/{experiment_id}/sessions/{session_id}/agent-view")
    def agent_view(experiment_id: str, session_id: str, body: AgentViewSelection):
        entry = session_for("experiments",experiment_id,session_id)
        agent = entry["agents"].get(body.agent_key)
        if agent is None:
            raise HTTPException(404, "本实验会话没有该人物的出生视角")
        with _client() as client:
            result = _json(client.post(entry["upstream"]+"/pose",json=agent["pose"]))
        return {**result, "agent_view":agent}


    @router.get(scope + "/{session_id}/snapshot.jpg")
    def frame(owner_kind: SceneOwner, owner_id: str, session_id: str, replay_seq: int | None = Query(None, ge=1),
              after_frame_seq: int | None = Query(None, ge=-1)):
        params = {"replay_seq": replay_seq} if replay_seq is not None else {}
        if after_frame_seq is not None:
            if replay_seq is None:
                raise HTTPException(422, "请指定回放序号")
            params["after_frame_seq"] = after_frame_seq
        with _client() as client:
            response = client.get(authorize(owner_kind, owner_id, session_id) + "/frame", params=params)
            if after_frame_seq is not None and response.status_code == 204:
                return Response(status_code=204, headers={"Cache-Control": "no-store"})
            _check(response)
            if response.headers.get("content-type", "").split(";")[0] != "image/jpeg":
                raise HTTPException(502, "SimWorld 未返回 JPEG 帧")
            headers = {"Cache-Control": "no-store"}
            if replay_seq is not None:
                for key in ("X-Replay-Seq", "X-Replay-Step", "X-Frame-Seq", "X-Replay-Settled"):
                    value = response.headers.get(key, "")
                    if not value.isascii() or not value.isdigit():
                        raise HTTPException(502, "UE 未返回匹配的回放帧身份")
                    headers[key] = value
                if headers["X-Replay-Seq"] != str(replay_seq) or headers["X-Replay-Settled"] not in {"0", "1"}:
                    raise HTTPException(502, "UE 回放帧与所选请求不符")
                if after_frame_seq is not None and int(headers["X-Frame-Seq"]) <= after_frame_seq:
                    raise HTTPException(502, "UE 返回了已显示的帧")
            return Response(response.content, media_type="image/jpeg", headers=headers)

    @router.get(scope + "/{session_id}/stream")
    async def stream(owner_kind: SceneOwner, owner_id: str, session_id: str):
        endpoint = authorize(owner_kind, owner_id, session_id) + "/stream"
        upstream = httpx.AsyncClient(**_connection())
        response = None
        try:
            response = await upstream.send(upstream.build_request("GET", endpoint), stream=True)
            media = response.headers.get("content-type", "")
            if not response.is_success or not media.lower().startswith("multipart/x-mixed-replace;") or "boundary=" not in media.lower():
                raise HTTPException(response.status_code if response.is_error else 502, "UE 实时画面尚不可用")
        except (HTTPException, httpx.RequestError) as exc:
            if response is not None:
                await response.aclose()
            await upstream.aclose()
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(503, "UE 实时画面连接失败") from exc

        class LiveStream(StreamingResponse):
            async def __call__(self, scope, receive, send):
                try:
                    await super().__call__(scope, receive, send)
                finally:
                    await response.aclose()
                    await upstream.aclose()

        return LiveStream(response.aiter_raw(), headers={"Content-Type": media,
                          "Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @router.post(scope + "/{session_id}/close")
    def close(owner_kind: SceneOwner, owner_id: str, session_id: str):
        with _client() as client:
            response = client.delete(authorize(owner_kind, owner_id, session_id))
            if response.status_code != 404:
                _check(response)
        with lock:
            sessions.pop(session_id, None)
        return {"closed": True}

    return router
