"""完全由单次运行上下文与清单快照装配的仿真世界。"""

from __future__ import annotations

import copy
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from generative_agents.modules import utils
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

    def get_agent(self, agent_key: str) -> Agent:
        """获取智能体。

        参数:
            agent_key: 智能体在当前实验或运行中的稳定唯一键。 类型：`str`。

        返回:
            返回 `Agent` 类型的处理结果。
        """
        return self.agents[agent_key]

    def agent_think(self, agent_key: str, status: dict) -> dict:
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
        plan = agent.think(status, self.agents_by_name)
        info = {
            "currently": agent.scratch.currently,
            "associate": agent.associate.abstract(),
            "concepts": {
                concept.node_id: concept.abstract() for concept in agent.concepts
            },
            "chats": [
                {"name": "self" if name == agent.name else name, "chat": chat}
                for name, chat in agent.chats
            ],
            "action": agent.action.abstract(),
            "schedule": agent.schedule.abstract(),
            "address": agent.get_tile().get_address(as_list=False),
        }
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
        return {"plan": plan, "info": info, "events": agent.drain_result_events()}

    def reset_game(self) -> None:
        """执行 `Game` 的`reset`仿真世界操作。

        返回:
            无返回值。
        """
        for agent_key, agent in self.agents.items():
            agent.reset()
            self.logger.info(
                "\n{}\n{}\n".format(utils.split_line(f"{agent_key}.reset"), agent)
            )

    def resolve_game_object_interaction(
        self, agent_key: str, outcome: dict, *, step_no: int
    ) -> dict:
        """解析仿真世界对象`interaction`。

        参数:
            agent_key: 智能体在当前实验或运行中的稳定唯一键。 类型：`str`。
            outcome: 当前步骤或交互实际产生的结构化结果。 类型：`dict`。
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """

        agent = self.get_agent(agent_key)
        plan = outcome.get("plan") or {}
        interaction = self.game_object_interactions.interact(
            agent,
            plan.get("path") or (),
            step_no=step_no,
        )
        if interaction is None:
            return outcome
        plan["movement_directive"] = interaction["agent_decision"]
        info = outcome.setdefault("info", {})
        info.setdefault("external_observations", []).append(interaction)
        info["concepts"] = {
            concept.node_id: concept.abstract() for concept in agent.concepts
        }
        interaction_events = []
        for event in agent.drain_result_events():
            if event.get("kind") == "game_object_interaction":
                event = {**event, "trace": list(interaction.get("trace") or ())}
            interaction_events.append(event)
        outcome["events"] = (
            *(outcome.get("events") or ()),
            *interaction_events,
        )
        return outcome

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
