"""A Run-scoped client. The provider owns its scene; GA owns decisions and commits."""
from __future__ import annotations

import asyncio
import math
import os
from urllib.parse import urlsplit

import httpx

from generative_agents.ga_runtime.models.retry import check_cancelled


class SceneUnavailable(RuntimeError):
    """The uncommitted Step must be retried against the same Run."""


class SceneActionRejected(ValueError):
    """A definitive refusal: no remote action was applied."""


def point(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or any(type(n) not in (int, float) or not math.isfinite(n) for n in value)):
        raise ValueError("SimWorld 须返回有限的米制 XYZ 坐标")
    return tuple(float(n) for n in value)


def angle(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("SimWorld 未返回有效朝向")
    return float(value)


class SimWorldRun:
    def __init__(self, run_id, scene_id, *, control=None):
        self.run_id = str(run_id)
        self.scene_id = scene_id
        self.control = control
        endpoint = os.environ.get("GA_SIMWORLD_URL", "http://127.0.0.1:8786").rstrip("/")
        url = urlsplit(endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("GA_SIMWORLD_URL 须为 SimWorld 服务地址")
        self.endpoint = f"{endpoint}/v1/runtime/{self.run_id}"

    def call(self, operation, body):
        token = os.environ.get("GA_SIMWORLD_TOKEN")
        timeout = 120 if operation == "open" else 90 if operation in {"navigate", "act"} else 30
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = asyncio.run(self._request(operation, body, headers, timeout))
        except (TimeoutError, httpx.RequestError) as exc:
            raise SceneUnavailable(f"SimWorld {operation} 连接中断，可从已提交步骤续跑") from exc
        if response.status_code == 409:
            raise SceneUnavailable(f"SimWorld {operation} 无法确认原 Run、场景内容或上次物理动作。请在场景服务核对后续跑")
        if response.status_code in {404, 408, 423, 429} or response.status_code >= 500:
            raise SceneUnavailable(f"SimWorld {operation} 未确认（HTTP {response.status_code}），请核对场景或操作后续跑")
        if not response.is_success:
            # Do not place upstream error bodies (which may include UE data) in Run logs.
            if operation == "act" and response.status_code in {400, 422}:
                raise SceneActionRejected(f"SimWorld 拒绝该动作（HTTP {response.status_code}）")
            if operation == "open" or response.status_code in {401, 403}:
                raise SceneUnavailable(f"SimWorld 拒绝 {operation}（HTTP {response.status_code}），请核对场景、出生点及服务配置")
            raise ValueError(f"SimWorld 拒绝 {operation}（HTTP {response.status_code}）")
        try:
            result = response.json()
        except ValueError as exc:
            raise SceneUnavailable(f"SimWorld {operation} 返回无效 JSON") from exc
        if not isinstance(result, dict):
            raise SceneUnavailable(f"SimWorld {operation} 未返回有效应答")
        return result

    async def _request(self, operation, body, headers, timeout):
        check_cancelled(self.control)
        async with asyncio.timeout(timeout), httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=4), trust_env=False,
                verify=self.endpoint.startswith("https:")) as client:
            pending = asyncio.create_task(client.post(f"{self.endpoint}/{operation}", json=body, headers=headers))
            try:
                while not pending.done():
                    check_cancelled(self.control)
                    await asyncio.wait({pending}, timeout=0.1)
                check_cancelled(self.control)
                return pending.result()
            finally:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    def open(self, definition, checkpoint, committed_step, attempt_id, *, require_existing, origin_run_id):
        actors = [a for a in definition.agents if a.enabled]
        restored = {key: {"position": item["coord"], "yaw_deg": item["yaw_deg"]}
                    for key, item in (checkpoint or {}).get("agents", {}).items()}
        reply = self.call("open", {"scene_id": self.scene_id, "attempt_id": str(attempt_id),
            "committed_step": committed_step, "committed_actors": restored,
            "require_existing": require_existing, "origin_run_id": origin_run_id,
            "committed_objects": (checkpoint or {}).get("simworld_object_states", {}),
            "actors": [{"id": a.agent_key, "spawn_anchor": a.spawn_anchor, "yaw_deg": a.yaw_deg} for a in actors]})
        poses = reply.get("actors")
        if reply.get("run_id") != self.run_id or reply.get("scene_id") != self.scene_id or not isinstance(poses, dict) or set(poses) != {a.agent_key for a in actors}:
            raise SceneUnavailable("SimWorld 打开的 Run、场景或参与者与请求不符")
        try:
            self.poses = {key: (point(item["position"]), angle(item["yaw_deg"])) for key, item in poses.items()}
        except (KeyError, TypeError, ValueError) as exc:
            raise SceneUnavailable("SimWorld 未确认全部人物的实际出生姿态") from exc
        if any(math.dist(self.poses[key][0], point(item["position"])) > 0.01
               or abs((self.poses[key][1] - item["yaw_deg"] + 180) % 360 - 180) > 0.01
               for key, item in restored.items()):
            raise SceneUnavailable("SimWorld 未恢复检查点中的人物姿态")
        for key, item in restored.items():
            self.poses[key] = point(item["position"]), angle(item["yaw_deg"])

    def location(self, coord):
        # A measured position, never a made-up street, object or semantic node.
        return [self.scene_id, ", ".join(format(n, ".12g") for n in point(coord)) + " m"]

    def semantics(self, coord):
        return ({"id": self.scene_id, "kind": "SCENE", "relation": "CURRENT",
                 "coord": list(point(coord))},)

    def act(self, step, actor, action):
        return self.call("act", {"step_no": step, "actor_id": actor, "action": action})

    def interact(self, step, actor, target, request_id, request):
        reply = self.act(step, actor, {"kind": "INTERACT", "target_id": target,
                                      "request_id": request_id, "request": request})
        if reply.get("target_id") != target or reply.get("request_id") != request_id:
            raise SceneUnavailable("SimWorld 交互回执身份与请求不符")
        if reply.get("accepted") is False or reply.get("interacted") is False:
            raise SceneActionRejected("SimWorld 未确认目标交互")
        if reply.get("accepted") is not True or reply.get("interacted") is not True:
            raise SceneUnavailable("SimWorld 未返回确定的交互结果")
        # Contact alone, or a sample of current state, is not evidence of a change.
        change = reply.get("state_change")
        if change is None:
            return None
        if not isinstance(change, dict) or not all(
            isinstance(change.get(k), dict) and type(change[k].get("visible")) is bool
            for k in ("before", "after")
        ):
            raise SceneUnavailable("SimWorld 的设施变化回执缺少可核验的前后状态")
        before, after = ({"visible": change[k]["visible"]} for k in ("before", "after"))
        return (before, after) if before != after else None

    def move(self, step, actor, start, target, budget):
        reply = self.act(step, actor, {"kind": "MOVE", "target": list(target), "max_distance_m": budget})
        try:
            origin = point(reply.get("from_position"))
            end = point(reply.get("position"))
            path = tuple(point(p) for p in reply["path"])
            before_yaw, after_yaw = angle(reply.get("from_yaw_deg")), angle(reply.get("yaw_deg"))
        except (KeyError, TypeError, ValueError) as exc:
            raise SceneUnavailable("SimWorld 移动回执缺少有效的姿态或实际轨迹") from exc
        if math.dist(origin, start) > 0.01:
            raise SceneUnavailable("SimWorld 移动回执的起点与 Run 不符")
        if path and path[0] == start:
            path = path[1:]
        if (not path and end != start) or (path and path[-1] != end):
            raise SceneUnavailable("SimWorld 实际轨迹与终点不符")
        distance = sum(math.dist(a,b) for a,b in zip((start,*path),path))
        if distance > budget + 0.05:
            raise SceneUnavailable("SimWorld 实际位移超过本步授权距离")
        return path, before_yaw, after_yaw, reply.get("blocked") is True
