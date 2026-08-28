"""完全由单次运行上下文与清单快照装配的仿真世界。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from generative_agents.modules import utils
from generative_agents.modules import memory
from generative_agents.modules.agent import Agent
from generative_agents.modules.game_object_interaction import (
    GameObjectInteractionSystem,
)
from generative_agents.modules.maze import Maze
from generative_agents.runtime.context import SimulationContext


def _as_tuple_tree(value):
    """执行`as``tuple``tree`的内部处理，供当前模块或类复用。

    参数:
        value: 当前操作使用的`value`。

    返回:
        返回函数计算得到的结果。
    """
    if isinstance(value, list):
        return tuple(_as_tuple_tree(item) for item in value)
    return value


class Game:
    """运行私有的世界聚合；不读取进程注册表或启动配置文件。"""

    def __init__(
        self,
        config: dict,
        conversation: dict,
        *,
        context: SimulationContext,
    ):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。 类型：`dict`。
            conversation: 当前步骤的对话上下文或已经完成的会话记录。 类型：`dict`。
            context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`SimulationContext`。

        返回:
            无返回值。

        异常:
            TypeError: 当参数类型不符合接口约定时抛出。
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        self.context = context
        self.name = str(context.run_id)
        self.record_interval = config.get(
            "record_interval_minutes", config.get("record_iterval", 30)
        )
        self.logger = context.logger
        maze_definition = config.get("maze") or config.get("world")
        if not isinstance(maze_definition, dict):
            raise ValueError(
                "run manifest must contain an inline maze/world definition"
            )
        self.maze = Maze(copy.deepcopy(maze_definition), self.logger, context.random)
        self.game_object_interactions = GameObjectInteractionSystem(
            maze_definition,
            skill_executor=getattr(context, "passive_skills", None),
            clock=context.clock,
        )
        self.conversation = conversation
        self.agents: dict[str, Agent] = {}
        agents_by_name: dict[str, Agent] = {}
        agent_keys_by_name: dict[str, str] = {}
        agent_base = copy.deepcopy(config.get("agent_base", {}))
        storage_root = Path(config.get("storage_root", context.paths.root / "storage"))
        agents = config.get("agents", {})
        if not isinstance(agents, dict):
            raise TypeError("runtime agent configuration must be keyed by agent_key")
        for agent_key, definition in agents.items():
            if "config_path" in definition:
                raise ValueError(
                    "runtime agent definitions must be materialized; config_path is legacy-only"
                )
            agent_config = utils.update_dict(
                copy.deepcopy(agent_base), copy.deepcopy(definition)
            )
            agent_config["agent_key"] = agent_key
            agent_config["storage_root"] = str(storage_root / agent_key)
            agent_name = str(agent_config.get("name") or "").strip()
            if not agent_name:
                raise ValueError(f"runtime agent name is required: {agent_key}")
            if agent_name in agent_keys_by_name:
                raise ValueError(
                    "enabled agent names must be unique: "
                    f"{agent_name!r} is used by {agent_keys_by_name[agent_name]!r} "
                    f"and {agent_key!r}"
                )
            agent_config["name"] = agent_name
            embedding_config = agent_config.get("associate", {}).get("embedding")
            if isinstance(embedding_config, dict):
                embedding_config["_control"] = context.control
                embedding_config["_logger"] = context.logger
                shared_embedding_model = context.metadata.get("embedding_model")
                if shared_embedding_model is not None:
                    embedding_config["_embed_model"] = shared_embedding_model
            agent = Agent(
                agent_config,
                self.maze,
                self.conversation,
                self.logger,
                clock=context.clock,
                random_source=context.random,
                skills=context.skills,
                models=context.models,
                model_trace=context.metadata.get("model_trace"),
                algorithm=context.algorithm,
                memory_stream=getattr(context, "memory_stream", None),
            )
            self.agents[agent_key] = agent
            agents_by_name[agent_name] = agent
            agent_keys_by_name[agent_name] = agent_key

        # 运行时与持久化层使用不可变 agent_key；旧认知模型的 Event.subject 使用展示名。
        # 两套命名空间必须显式分离，不能把别名混入同一字典，否则迭代和快照会重复智能体。
        self.agents_by_name: Mapping[str, Agent] = MappingProxyType(agents_by_name)
        self.agent_keys_by_name: Mapping[str, str] = MappingProxyType(
            agent_keys_by_name
        )
        # Game Object passive Skills produce observations, not world mutations.
        # Keep them in a run-private one-step inbox so the response that was
        # returned at the end of one Brain turn becomes explicit input to the
        # next IterationContext.  The inbox is checkpointed below.
        self._external_observation_inbox: dict[str, list[dict[str, Any]]] = {
            agent_key: [] for agent_key in self.agents
        }
        # A conversation is a world-level interaction that may span many Agent
        # iterations.  The Brain chooses when to speak; the system supplies the
        # stable thread identity required by replay and message ordering.
        self._conversation_threads: dict[str, dict[str, Any]] = {}
        self._open_conversation_by_participants: dict[str, str] = {}
        self._conversation_sequence = 0

    def get_agent(self, agent_key: str) -> Agent:
        """获取智能体。

        参数:
            agent_key: 智能体在当前实验或运行中的稳定唯一键。 类型：`str`。

        返回:
            返回 `Agent` 类型的处理结果。
        """
        return self.agents[agent_key]

    def queue_external_observation(
        self, agent_key: str, observation: Mapping[str, Any]
    ) -> None:
        """Queue one passive world response for the Agent's next iteration."""

        if agent_key not in self.agents:
            raise KeyError(f"unknown Agent for external observation: {agent_key}")
        # Skill traces are audit data already persisted as domain events. They
        # are deliberately excluded from the Brain context to keep the semantic
        # observation compact and prevent internal tool chatter from accumulating.
        context_observation = {
            "kind": "GAME_OBJECT_SKILL_RESPONSE",
            **{
                key: copy.deepcopy(value)
                for key, value in observation.items()
                if key != "trace"
            },
        }
        self._external_observation_inbox.setdefault(agent_key, []).append(
            context_observation
        )

    def consume_external_observations(
        self, agent_key: str
    ) -> tuple[dict[str, Any], ...]:
        """Deliver and clear the observations queued for this iteration."""

        if agent_key not in self.agents:
            raise KeyError(f"unknown Agent for external observation: {agent_key}")
        observations = tuple(
            copy.deepcopy(self._external_observation_inbox.get(agent_key, ()))
        )
        self._external_observation_inbox[agent_key] = []
        return observations

    def record_conversation_message(
        self,
        agent_key: str,
        participant_agent_keys,
        *,
        requested_conversation_id: str | None = None,
        start_new: bool = False,
        end_conversation: bool = False,
        step_no: int | None = None,
    ) -> dict[str, Any]:
        """Return a stable thread id and monotonically increasing message number."""

        participants = tuple(sorted({agent_key, *participant_agent_keys}))
        if len(participants) != 2:
            raise ValueError("SPEAK currently requires exactly two distinct Agents")
        participant_key = "|".join(participants)
        requested_id = str(requested_conversation_id or "").strip()
        if requested_id:
            try:
                UUID(requested_id)
            except ValueError as exc:
                raise ValueError("conversation_id must be a UUID") from exc
            conversation_id = requested_id
        elif not start_new:
            conversation_id = self._open_conversation_by_participants.get(
                participant_key, ""
            )
        else:
            conversation_id = ""

        thread = self._conversation_threads.get(conversation_id)
        if thread is not None and tuple(thread["participants"]) != participants:
            raise ValueError("conversation_id belongs to different participants")
        if thread is not None and not bool(thread.get("open", True)):
            if requested_id:
                raise ValueError("conversation_id is already closed")
            thread = None
            conversation_id = ""
        if thread is None:
            if requested_id and conversation_id in self._conversation_threads:
                raise ValueError("conversation_id cannot be reopened")
            if not conversation_id:
                self._conversation_sequence += 1
                conversation_id = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"{self.context.run_id}:conversation:"
                        f"{self._conversation_sequence}:{participant_key}",
                    )
                )
            thread = {
                "conversation_id": conversation_id,
                "participants": list(participants),
                "initiator_agent_key": agent_key,
                "message_count": 0,
                "open": True,
            }
            self._conversation_threads[conversation_id] = thread
        thread["message_count"] = int(thread.get("message_count") or 0) + 1
        thread["last_step"] = int(step_no or 0)
        thread["open"] = not end_conversation
        if end_conversation:
            if self._open_conversation_by_participants.get(participant_key) == conversation_id:
                self._open_conversation_by_participants.pop(participant_key, None)
        else:
            self._open_conversation_by_participants[participant_key] = conversation_id
        return {
            "conversation_id": conversation_id,
            "message_sequence": thread["message_count"],
            "participants": participants,
            "ended_reason": "EXPLICIT_END" if end_conversation else None,
        }

    def active_conversations_for(self, agent_key: str) -> tuple[dict[str, Any], ...]:
        """Expose compact open-thread context to world perception."""

        return tuple(
            copy.deepcopy(thread)
            for thread in self._conversation_threads.values()
            if bool(thread.get("open", True))
            and agent_key in tuple(thread.get("participants") or ())
        )

    def agent_think(
        self,
        agent_key: str,
        status: dict,
        *,
        step_no: int,
        total_steps: int,
        stride_minutes: int,
    ) -> dict:
        """执行一次智能体认知循环，产出计划、可观测上下文与副作用。

        参数:
            agent_key: 智能体在当前实验或运行中的稳定唯一键。 类型：`str`。
            status: 当前仿真步的世界状态映射，包含时间、位置和可观察对象等认知输入。 类型：`dict`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        说明:
            该方法是单个智能体认知链路的边界：读取同一步世界快照，收集副作用，统一交给游戏循环提交。
        """
        agent = self.get_agent(agent_key)
        brain_runtime = getattr(self.context, "brain_runtime", None)
        if brain_runtime is None:
            raise RuntimeError("SimulationContext requires a BrainRuntime")
        outcome = brain_runtime.run_step(
            self,
            agent_key,
            step_no=step_no,
            total_steps=total_steps,
            stride_minutes=stride_minutes,
        )
        info = outcome.setdefault("info", {})
        elapsed = self.context.clock.daily_duration() - agent.last_record
        info["record"] = elapsed > self.record_interval
        if info["record"]:
            agent.last_record = self.context.clock.daily_duration()
        if agent.llm_available():
            info["llm"] = agent._llm.get_summary()
        title = "{}.summary @ {}".format(
            agent_key, self.context.clock.get_date("%Y%m%d-%H:%M:%S")
        )
        self.logger.info("\n{}\n{}\n".format(utils.split_line(title), agent))
        return outcome

    def commit_world_action(
        self,
        agent_key: str,
        outcome: dict,
        *,
        stride_minutes: int,
        movement_budget: int,
    ) -> dict:
        """Commit the one MCP-selected action and emit replay-complete facts."""

        agent = self.get_agent(agent_key)
        action = dict(outcome.get("world_action") or {})
        action_type = str(action.get("action_type") or "WAIT").upper()
        arguments = dict(action.get("arguments") or {})
        planned_path = tuple(
            tuple(coord) for coord in (action.get("path") or ())
        )
        from_coord = tuple(agent.coord)
        before_address = tuple(agent.get_tile().get_address())
        consumed = planned_path[:movement_budget] if action_type == "MOVE" else ()
        remaining = planned_path[len(consumed) :] if action_type == "MOVE" else ()
        target_address = tuple(arguments.get("target_address") or ())
        description = str(arguments.get("description") or "").strip()
        predicate = str(arguments.get("predicate") or "").strip()
        object_value = str(arguments.get("object") or "").strip()
        emoji = str(arguments.get("emoji") or "").strip()
        object_event = None
        extra_events: list[dict] = []

        if action_type == "MOVE":
            destination = tuple(consumed[-1]) if consumed else from_coord
            destination_address = tuple(
                self.maze.tile_at(destination).get_address()
            )
            target_address = target_address or destination_address
            predicate = predicate or "移动到"
            object_value = object_value or ":".join(target_address)
            description = description or f"{agent.name} 前往 {object_value}"
            emoji = emoji or "🚶"
        elif action_type == "ACT":
            if not predicate or not object_value:
                raise ValueError(
                    "ACT requires non-empty Event predicate and object"
                )
            description = description or f"{agent.name}{predicate}{object_value}"
        elif action_type == "SPEAK":
            participant_keys = tuple(arguments.get("participant_agent_keys") or ())
            message = str(arguments.get("message") or "").strip()
            thread = self.record_conversation_message(
                agent_key,
                participant_keys,
                requested_conversation_id=arguments.get("conversation_id"),
                start_new=bool(arguments.get("start_new_conversation", False)),
                end_conversation=bool(arguments.get("end_conversation", False)),
                step_no=int(
                    ((outcome.get("info") or {}).get("iteration_context") or {}).get(
                        "step_no", 0
                    )
                    or 0
                ),
            )
            predicate = predicate or "对话"
            object_value = object_value or message
            description = description or f"{agent.name} 说：{message}"
            emoji = emoji or "💬"
            extra_events.append(
                {
                    "kind": "conversation",
                    "conversation_id": thread["conversation_id"],
                    "message_sequence": thread["message_sequence"],
                    "participants": thread["participants"],
                    "location": before_address,
                    "messages": ((agent.name, message),),
                    "summary": message,
                    "ended_reason": thread["ended_reason"],
                    "duration_minutes": stride_minutes,
                    "duration_source": "SCHEDULED",
                }
            )
        elif action_type == "INTERACT":
            observation = dict(action.get("observation") or {})
            object_key = str(observation.get("object_key") or "")
            predicate = predicate or "交互"
            object_value = object_value or object_key
            description = description or f"{agent.name} 与 {object_key} 交互"
            emoji = emoji or "🤝"
            if observation:
                self.queue_external_observation(agent_key, observation)
        elif action_type == "SET_OBJECT_STATE":
            object_key = str(arguments.get("object_key") or "")
            before, after = self.game_object_interactions.apply_state_patch(
                object_key,
                arguments.get("state_patch") or {},
            )
            predicate = predicate or "更新状态"
            object_value = object_value or object_key
            description = description or f"{agent.name} 更新了 {object_key} 的状态"
            emoji = emoji or "⚙️"
            object_event = memory.Event(
                object_key,
                "状态变为",
                json.dumps(after, ensure_ascii=False, sort_keys=True),
                address=list(before_address),
                describe=f"{object_key} 状态已更新",
            )
            extra_events.append(
                self._world_domain_event(
                    "GAME_OBJECT_STATE_CHANGED",
                    (agent_key,),
                    subject=object_key,
                    predicate="状态变为",
                    object_value=json.dumps(after, ensure_ascii=False, sort_keys=True),
                    structured_payload={
                        "object_key": object_key,
                        "before": before,
                        "after": after,
                        "state_patch": dict(arguments.get("state_patch") or {}),
                        "actor_agent_key": agent_key,
                        "coord": list(from_coord),
                        "address": list(before_address),
                    },
                )
            )
        else:
            action_type = "WAIT"
            predicate = predicate or "等待"
            object_value = object_value or "下一轮"
            description = description or f"{agent.name} 原地等待"
            emoji = emoji or "⏳"

        event_address = list(target_address if action_type == "MOVE" else before_address)
        agent.action = memory.Action(
            memory.Event(
                agent.name,
                predicate,
                object_value,
                address=event_address,
                describe=description,
                emoji=emoji,
            ),
            object_event,
            duration=stride_minutes,
            clock=self.context.clock,
        )
        if consumed:
            agent.move(tuple(consumed[-1]), list(remaining))
        else:
            agent.move(from_coord, list(remaining))
        to_coord = tuple(agent.coord)
        current_address = tuple(agent.get_tile().get_address())
        address_text = ":".join(str(part) for part in current_address if str(part))
        current_status = (
            f"{description}（位置：{address_text}）" if address_text else description
        )
        # ``currently`` is persisted in checkpoints and projected into result
        # views.  BrainRuntime reads it before the action is committed, so it
        # must be synchronized here from the action that actually reached the
        # world rather than left at the Agent template's initial value.
        agent.scratch.currently = current_status
        outcome.setdefault("info", {})["currently"] = current_status
        observed_waypoints = (
            consumed[1:] if consumed and consumed[0] == from_coord else consumed
        )
        executed_path = (from_coord, *observed_waypoints) if consumed else ()
        if action_type != "SET_OBJECT_STATE":
            event_types = {
                "MOVE": "AGENT_MOVED",
                "ACT": "AGENT_ACTED",
                "WAIT": "AGENT_WAITED",
                "SPEAK": "AGENT_SPOKE",
                "INTERACT": "GAME_OBJECT_INTERACTED",
            }
            structured_payload = {
                "action_type": action_type,
                "from_coord": list(from_coord),
                "to_coord": list(to_coord),
                "executed_path": [list(coord) for coord in executed_path],
                "planned_path": [list(coord) for coord in planned_path],
                "remaining_path": [list(coord) for coord in remaining],
                "before_address": list(before_address),
                "after_address": list(current_address),
                "description": description,
                "emoji": emoji,
                "currently": current_status,
                "arguments": arguments,
            }
            if action.get("observation"):
                structured_payload["observation"] = dict(action["observation"])
            extra_events.append(
                self._world_domain_event(
                    event_types[action_type],
                    (agent_key,),
                    subject=agent.name,
                    predicate=predicate,
                    object_value=object_value,
                    structured_payload=structured_payload,
                )
            )
        outcome["events"] = tuple(outcome.get("events") or ()) + tuple(extra_events)
        return {
            "outcome": outcome,
            "planned_path": planned_path,
            "executed_path": executed_path,
            "remaining_path": tuple(remaining),
        }

    @staticmethod
    def _world_domain_event(
        event_type,
        agent_keys,
        *,
        subject,
        predicate,
        object_value,
        structured_payload,
    ) -> dict:
        if not structured_payload:
            raise ValueError("replay world event requires structured_payload")
        return {
            "kind": "world_domain_event",
            "event_type": event_type,
            "agent_keys": tuple(agent_keys),
            "subject": subject,
            "predicate": predicate,
            "object": object_value,
            "structured_payload": copy.deepcopy(dict(structured_payload)),
        }

    def reset_game(self) -> None:
        """Reset only per-run orchestration state.

        BrainRuntime owns model calls; the legacy Agent LLM is deliberately not
        initialized here, otherwise the removed hard-coded cognition pipeline
        would remain an accidental production dependency.
        """
        self.logger.info("BrainRuntime ready for %d Agents", len(self.agents))

    def snapshot_state(self) -> dict:
        """执行 `Game` 的快照状态操作。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        return {
            "agents": {
                agent_key: {
                    **agent.to_dict(),
                    "coord": list(agent.coord),
                    "path": [list(coord) for coord in agent.path or []],
                }
                for agent_key, agent in self.agents.items()
            },
            "virtual_time": self.context.clock.get_date().isoformat(),
            # random.Random 状态只包含可写入 JSON 的标量和元组；检查点序列化会把元组变成列表，
            # 因此调用 setstate() 前必须恢复原有嵌套形状。
            "rng_state": self.context.random.getstate(),
            "game_object_states": self.game_object_interactions.snapshot_state(),
            "external_observation_inbox": copy.deepcopy(
                getattr(
                    self,
                    "_external_observation_inbox",
                    {agent_key: [] for agent_key in self.agents},
                )
            ),
            "conversation_threads": copy.deepcopy(
                getattr(self, "_conversation_threads", {})
            ),
            "open_conversation_by_participants": copy.deepcopy(
                getattr(self, "_open_conversation_by_participants", {})
            ),
            "conversation_sequence": getattr(self, "_conversation_sequence", 0),
        }

    def restore_runtime_state(self, snapshot: dict) -> None:
        """执行 `Game` 的`restore``runtime`状态操作。

        参数:
            snapshot: 从检查点读取或准备写入检查点的运行时快照。 类型：`dict`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        rng_state = snapshot.get("rng_state")
        if rng_state is None:
            raise ValueError("checkpoint is missing rng_state")
        self.context.random.setstate(_as_tuple_tree(rng_state))
        object_states = snapshot.get("game_object_states")
        if object_states is None:
            raise ValueError("checkpoint is missing game_object_states")
        self.game_object_interactions.restore_state(object_states)
        inbox = snapshot.get("external_observation_inbox") or {}
        if not isinstance(inbox, Mapping):
            raise ValueError("checkpoint external observation inbox is invalid")
        unknown_agent_keys = {str(key) for key in inbox} - set(self.agents)
        if unknown_agent_keys:
            raise ValueError(
                "checkpoint external observation inbox contains unknown Agents"
            )
        restored_inbox: dict[str, list[dict[str, Any]]] = {
            agent_key: [] for agent_key in self.agents
        }
        for agent_key, items in inbox.items():
            if not isinstance(items, list) or not all(
                isinstance(item, Mapping) for item in items
            ):
                raise ValueError("checkpoint external observations are invalid")
            restored_inbox[str(agent_key)] = [
                copy.deepcopy(dict(item)) for item in items
            ]
        self._external_observation_inbox = restored_inbox
        threads = snapshot.get("conversation_threads") or {}
        open_threads = snapshot.get("open_conversation_by_participants") or {}
        if not isinstance(threads, Mapping) or not isinstance(open_threads, Mapping):
            raise ValueError("checkpoint conversation state is invalid")
        self._conversation_threads = {
            str(key): copy.deepcopy(dict(value))
            for key, value in threads.items()
            if isinstance(value, Mapping)
        }
        self._open_conversation_by_participants = {
            str(key): str(value) for key, value in open_threads.items()
        }
        self._conversation_sequence = int(snapshot.get("conversation_sequence") or 0)

    def storage_exporters(self):
        """执行 `Game` 的存储`exporters`操作。

        返回:
            返回函数计算得到的结果。
        """
        return {
            agent_key: agent.associate.export_storage
            for agent_key, agent in self.agents.items()
        }

    def runtime_storage_exporters(self):
        """执行 `Game` 的`runtime`存储`exporters`操作。

        返回:
            返回函数计算得到的结果。
        """
        memory_stream = getattr(self.context, "memory_stream", None)
        if memory_stream is None:
            return {}
        return {"skill-memory": memory_stream.export_storage}


def create_game(config, conversation, *, context: SimulationContext) -> Game:
    """创建仿真世界。

    参数:
        config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。
        conversation: 当前步骤的对话上下文或已经完成的会话记录。
        context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`SimulationContext`。

    返回:
        返回 `Game` 类型的处理结果。
    """
    return Game(config, conversation, context=context)
