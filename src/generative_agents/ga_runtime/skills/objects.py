"""Model-driven Game Objects using the same public capabilities and commit boundary.

Objects are fixed spatial actors, not public Agents. A binding is sufficient to
execute its natural-language root Skill once per Step, including pending requests.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any, Mapping

from generative_agents.ga_runtime.skills.executor import RecoverableSkillRuntimeError
from generative_agents.ga_runtime.skills.executor import SkillRuntime
from generative_agents.ga_protocol.facts.movement import normalize_movement_activity

from generative_agents.ga_runtime.capabilities.server import PlannedWorldAction
from generative_agents.ga_runtime.capabilities.server import SimulationMCPServer
from generative_agents.ga_runtime.engine.iteration import ObjectIterationContext


class ObjectMCPServer(SimulationMCPServer):
    """Object-scoped perception, private memory and a single validated action."""

    default_tools = True

    def __init__(self, game, iteration: ObjectIterationContext, *, binding,
                 observed_facts=(), requests=(), memory_stream=None):
        super().__init__(game, iteration, memory_stream=memory_stream)
        self.binding = binding
        self.observed_facts = tuple(observed_facts)
        self.requests = {item["request_id"]: copy.deepcopy(item) for item in requests}
        self._visible_targets: set[str] = set()
        self._evidence: dict[str, dict] = {}

    @property
    def memory_owner_key(self) -> str:
        return self.iteration.memory_owner_key

    def _observer(self):
        return SimpleNamespace(
            coord=self.iteration.coord,
            percept_config={"vision_r": self.binding.vision_radius,
                            "att_bandwidth": self.binding.attention_bandwidth},
        )

    def _public_object_state(self, object_key: str) -> dict:
        if object_key == self.iteration.object_key:
            return self.game.game_object_interactions.object_state(object_key)
        return super()._public_object_state(object_key)

    def tools(self) -> list[dict]:
        available = [tool for tool in super().tools() if tool["name"] != "world-navigate"]
        action = next(tool for tool in available if tool["name"] == "world-act")
        action["description"] = (
            "Commit your one Game Object action this Step. ACT records an activity; "
            "SET_OBJECT_STATE changes only your own state; WAIT waits. You are stationary. "
            "Attach responses to answer pending request IDs in the same action. "
            "Use target_agent_key and evidence_ids for an observed subject, and "
            "idempotency_key to prevent repeating an activity across Steps/resumes. "
            "Only the state label is publicly visible; other state fields are private."
        )
        names = {"action_type", "description", "predicate", "object", "emoji", "object_key",
                 "state_patch", "wait_reason", "expected_until_step", "expected_until_time"}
        props = action["inputSchema"]["properties"]
        action["inputSchema"]["properties"] = props = {key: value for key, value in props.items() if key in names}
        props["action_type"] = {"type": "string", "enum": ["ACT", "WAIT", "SET_OBJECT_STATE"]}
        props.update({
            "target_agent_key": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
            "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
            "responses": {"type": "array", "maxItems": 100, "items": {
                "type": "object", "properties": {
                    "request_id": {"type": "string"}, "message": {"type": "string", "minLength": 1}},
                "required": ["request_id", "message"], "additionalProperties": False}},
        })
        for tool in available:
            tool["description"] = tool["description"].replace("this Agent", "this Game Object").replace("The Agent identity", "The Game Object identity")
        return available

    def _perceive(self, arguments: Mapping[str, Any]) -> dict:
        result = super()._perceive(arguments)
        radius = result["radius_tiles"]
        center = self.iteration.coord
        candidates = []
        for fact in self.observed_facts:
            if fact.event_type not in {"AGENT_MOVED", "AGENT_ACTED", "AGENT_WAITED"}:
                continue
            payload = fact.payload.get("structured_payload") or {}
            path = payload.get("executed_path") or [payload.get("to_coord")]
            segments, current = [], []
            for coord in path:
                visible = (isinstance(coord, (list, tuple)) and len(coord) == 2
                           and max(abs(coord[i] - center[i]) for i in (0, 1)) <= radius)
                if visible:
                    current.append(list(coord))
                elif current:
                    segments.append(current)
                    current = []
            if current:
                segments.append(current)
            if not segments or len(fact.agent_keys) != 1:
                continue
            agent_key = fact.agent_keys[0]
            candidates.append({
                "fact_id": str(fact.event_id), "event_type": fact.event_type,
                "agent_key": agent_key, "step_no": self.iteration.step_no,
                "predicate": fact.payload.get("predicate"),
                "object": "可见范围内的路径" if fact.event_type == "AGENT_MOVED" else fact.payload.get("object"),
                "visible_segments": segments,
                **({"movement_activity": normalize_movement_activity(payload.get("movement_activity"))}
                   if fact.event_type == "AGENT_MOVED" else {}),
            })
        candidates.sort(key=lambda item: (
            min(max(abs(coord[i] - center[i]) for i in (0, 1))
                for segment in item["visible_segments"] for coord in segment),
            item["fact_id"],
        ))
        visible_actions = candidates[:self.binding.attention_bandwidth]
        result["observed_actions"] = visible_actions
        result["attention"]["observed_action_candidates"] = len(candidates)
        result["attention"]["truncated"] |= len(candidates) > len(visible_actions)
        self._visible_targets.update(item["agent_key"] for item in result["nearby_agents"])
        self._visible_targets.update(item["agent_key"] for item in visible_actions)
        self._evidence.update((item["fact_id"], copy.deepcopy(item)) for item in visible_actions)
        return result

    def _plan_action(self, arguments: Mapping[str, Any]) -> dict:
        if self.action is not None:
            raise ValueError("world-act already selected an action for this iteration")
        payload = copy.deepcopy(dict(arguments))
        kind = str(payload.get("action_type") or "").strip().upper()
        if kind not in {"ACT", "WAIT", "SET_OBJECT_STATE"}:
            raise ValueError("Game Object action must be ACT, WAIT or SET_OBJECT_STATE")
        payload["action_type"] = kind
        object_key = str(payload.get("object_key") or self.iteration.object_key)
        if object_key != self.iteration.object_key:
            raise ValueError("Game Objects can act only as themselves and change only their own state")
        payload["object_key"] = object_key
        if kind == "ACT":
            for key in ("predicate", "object"):
                if not isinstance(payload.get(key), str) or not payload[key].strip():
                    raise ValueError("ACT requires Event predicate and object")
        if kind == "SET_OBJECT_STATE":
            patch = payload.get("state_patch")
            if not isinstance(patch, dict) or not patch or len(patch) > 100:
                raise ValueError("SET_OBJECT_STATE requires a non-empty state_patch (up to 100 fields)")
            if "state" in patch and not isinstance(patch["state"], str):
                raise ValueError("the public visual state must be a string")
        elif "state_patch" in payload:
            raise ValueError("state_patch is only available for SET_OBJECT_STATE")
        target = payload.get("target_agent_key")
        if target is not None and (not isinstance(target, str) or target not in self._visible_targets):
            raise ValueError("target Agent has not been perceived by this Game Object")
        ids = payload.get("evidence_ids", [])
        if (not isinstance(ids, list) or len(ids) > 100
                or any(not isinstance(key, str) or key not in self._evidence for key in ids)):
            raise ValueError("evidence_ids must refer to facts actually returned by world-perceive")
        if target and any(self._evidence[key]["agent_key"] != target for key in ids):
            raise ValueError("evidence must belong to the selected Agent")
        key = payload.get("idempotency_key")
        if key is not None:
            if not isinstance(key, str) or not key.strip() or len(key) > 240:
                raise ValueError("invalid idempotency_key")
            key = payload["idempotency_key"] = key.strip()
            if key in self.game.game_object_interactions.runtime_state(object_key)["action_keys"]:
                raise ValueError("this Game Object action idempotency key is already committed")
        responses = payload.get("responses", [])
        if not isinstance(responses, list) or len(responses) > 100:
            raise ValueError("responses must be a list of at most 100 messages")
        replied = set()
        for response in responses:
            if not isinstance(response, dict) or set(response) != {"request_id", "message"}:
                raise ValueError("responses require only request_id and message")
            request_id = response["request_id"]
            if not isinstance(request_id, str) or request_id not in self.requests or request_id in replied:
                raise ValueError("response request_id is not a pending request for this object")
            if not isinstance(response["message"], str) or not response["message"].strip():
                raise ValueError("response message must not be empty")
            replied.add(request_id)
        # All extra facts originate at the server, not in model-written payloads.
        payload["evidence"] = [copy.deepcopy(self._evidence[key]) for key in ids]
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        self._action = PlannedWorldAction(action_type=kind, arguments=payload)
        return {"accepted": True, "action": self._action.as_dict()}


class ObjectSkillRuntime:
    """Execute one object's natural-language SOP with shared model infrastructure."""

    def __init__(self, registry, *, model_config, memory_stream=None, model_client=None,
                 recorder=None, control=None, logger=None):
        self.registry = registry
        self.model_config = dict(model_config)
        self.memory_stream = memory_stream
        self.model_client = model_client
        self.recorder = recorder
        self.control = control
        self.logger = logger

    def run_step(self, game, binding, *, step_no: int, total_steps: int,
                 stride_minutes: int, observed_facts=()) -> dict:
        system = game.game_object_interactions
        state = system.runtime_state(binding.object_key)
        if step_no <= state["last_step"]:
            raise ValueError("Game Object already ran this Step")
        coord = (min(game.maze.width_tiles - 1, max(0, int(binding.coord[0]))),
                 min(game.maze.height_tiles - 1, max(0, int(binding.coord[1]))))
        nodes = game.maze.semantic_nodes_in_scope(coord, 0)
        by_id = {str(node["id"]): node for node in nodes}
        selected = by_id.get(binding.object_key)
        if selected is None:
            raise ValueError(f"Game Object semantic anchor is missing: {binding.object_key}")
        address = tuple(selected["address"])
        anchors = tuple(node for node in nodes
                        if tuple(node.get("address") or ()) == address[:len(node.get("address") or ())])
        requests = state["requests"][:100]
        iteration = ObjectIterationContext(
            run_id=game.context.run_id, attempt_id=game.context.attempt_id,
            object_key=binding.object_key, object_name=binding.object_name,
            step_no=step_no, total_steps=total_steps, now=game.context.clock.get_date(),
            stride_minutes=stride_minutes, coord=coord, address=address,
            spatial_semantics=anchors,
            variables={"object_state": system.object_state(binding.object_key),
                       "last_action": state["last_action"], "interaction_requests": requests,
                       "pending_request_count": len(state["requests"]),
                       "recent_action_keys": list(state["action_keys"])[-100:]},
        )
        mcp = ObjectMCPServer(game, iteration, binding=binding, observed_facts=observed_facts,
                              requests=requests, memory_stream=self.memory_stream)
        config = self.model_config
        runtime = SkillRuntime(
            self.registry, base_url=str(config.get("base_url") or ""),
            model=str(config.get("resolved_model") or config.get("model") or ""),
            api_key=str(config.get("api_key") or ""), mcp=mcp,
            timeout=float(config.get("timeout_seconds") or 300),
            max_hops=int(config.get("max_hops") or 12),
            temperature=float(config.get("temperature") or 0.2),
            max_tokens=int(config.get("max_tokens") or 2048),
            enable_thinking=bool(config.get("enable_thinking", False)),
            provider=str(config.get("provider") or "vllm"),
            retry_attempts=int(config.get("retry_attempts") or 1),
            retry_backoff_seconds=float(config.get("retry_backoff_seconds") or 0),
            model_client=self.model_client, recorder=self.recorder, control=self.control,
            logger=self.logger, agent_key=iteration.memory_owner_key, step_no=step_no,
        )
        task = (
            "你是当前 Game Object，以对象自己的身份执行本轮 Skill。即使无人询问也自主运行。"
            "按照自然语言 SOP 决定是否感知、调用子 Skill、读取或写入自身记忆、处理交互。"
            "world-perceive 返回附近真实位置、活动和本步可见范围内的实际路径片段；"
            "不可用主观描述代替真实轨迹。每轮最多提交一次 world-act，成功后立即结束。"
            "ACT 记录活动，SET_OBJECT_STATE 只修改自身状态，WAIT 表示等待；你不能移动。"
            "需要回复交互时，将 request_id 和 message 写入同一次 world-act 的 responses。"
            "没有提交的回复文本不发送给 Agent。回复与状态在下一轮 Agent 上下文生效。"
            "针对已观察到的 Agent 执行动作时可指定 target_agent_key、evidence_ids；"
            "用 idempotency_key 标识不能重复的同一次活动。记忆和自己的内部状态用于跨步进度。"
            "只有 state 外观标签公开可见，其他对象状态和私人记忆不会自动告知附近 Agent。"
            "根 Skill 负责提交动作，子 Skill 只返回判断建议。世界时间由系统推进。"
        )
        memory_snapshot = self.memory_stream.begin_iteration(iteration.memory_owner_key) if self.memory_stream else None
        trace, output, fallback = (), "", False
        try:
            result = runtime.run(binding.skill_name, task, context={"IterationContext": iteration.as_dict()})
            trace, output = result.trace, result.output_text
        except RecoverableSkillRuntimeError as exc:
            if memory_snapshot is not None:
                self.memory_stream.rollback_iteration(memory_snapshot)
            mcp.discard_action()
            trace = (*exc.trace, {"event": "object.fallback", "reason": str(exc), "object_key": binding.object_key})
            fallback = True
        except Exception:
            if memory_snapshot is not None:
                self.memory_stream.rollback_iteration(memory_snapshot)
            raise
        if mcp.action is None:
            fallback = True
            trace = (*trace, {"event": "object.missing_action", "object_key": binding.object_key,
                             "reason": "Game Object Skill did not submit world-act"})
        action = mcp.action or PlannedWorldAction("WAIT", {
            "action_type": "WAIT", "object_key": binding.object_key,
            "description": "对象 Skill 未提交动作，本轮等待", "responses": [],
        })
        return {"action": action, "iteration": iteration, "requests": requests,
                "fallback": fallback, "skill_name": binding.skill_name,
                "skill_content_hash": self.registry.get(binding.skill_name).content_hash,
                "input_text": task, "output_text": output, "trace": list(trace)}


__all__ = ["ObjectMCPServer", "ObjectSkillRuntime"]
