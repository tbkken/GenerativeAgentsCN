"""智能体认知、记忆、计划、交互与移动决策。"""

import os
import math
import datetime
import copy
import hashlib
import json

from generative_agents.modules import memory, prompt, utils
from generative_agents.modules.model.llm_model import create_llm_model
from generative_agents.modules.memory.associate import Concept
from generative_agents.status import MemoryDeltaKind, MemoryState


def estimate_chat_duration(chats, chars_per_minute=240):
    """执行 的`estimate``chat``duration`操作。

    参数:
        chats: 按时间顺序排列的对话消息或说话人—内容二元组。
        chars_per_minute: 估算对话时长时采用的每分钟字符数，必须为正数。 默认值：`240`。

    返回:
        返回函数计算得到的结果。

    异常:
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """
    if chars_per_minute <= 0:
        raise ValueError("chars_per_minute must be positive")
    return max(
        1,
        math.ceil(sum(len(message) for _, message in chats) / chars_per_minute),
    )


_INVALID_CHAT_MARKERS = ("填坑", "待补充", "占位", "todo", "placeholder")


class AgentSpatialConfigurationError(RuntimeError):
    """已发布的旧版智能体无法解析必需运行时地址。"""

    code = "AGENT_SPATIAL_CONFIGURATION_INVALID"


def valid_chat_message(message, previous=()):
    """执行 的`valid``chat``message`操作。

    参数:
        message: 待发送、校验、脱敏或写入会话的消息文本或对象。
        previous: 用于去重或连续性判断的前序消息、状态或记录集合。

    返回:
        返回函数计算得到的结果。
    """

    if not isinstance(message, str):
        return False
    text = " ".join(message.split()).strip()
    if len(text) < 2:
        return False
    folded = text.casefold()
    if any(marker in folded for marker in _INVALID_CHAT_MARKERS):
        return False
    normalized = "".join(char for char in folded if char.isalnum())
    if len(normalized) < 2:
        return False
    for _speaker, prior in previous:
        prior_normalized = "".join(
            char for char in str(prior).casefold() if char.isalnum()
        )
        if normalized == prior_normalized:
            return False
    return True


class Agent:
    """一个运行私有的生成式智能体。

    该对象把长期记忆、短期 Scratch 状态、认知提示、空间位置和模型调用组合在一起。
    ``think()`` 是每个仿真步的总入口；其余方法分别实现感知、计划、反应、对话、
    反思和移动。Agent 只通过注入的时钟、随机源和 Skill 依赖工作，不读取全局运行状态。
    """

    def __init__(
        self,
        config,
        maze,
        conversation,
        logger,
        *,
        clock,
        random_source,
        skills,
        models=None,
        model_trace=None,
        algorithm=None,
        memory_stream=None,
    ):
        # RunControl 等仅运行时协作者含有线程锁，必须保持对象身份。
        # 先复制可序列化定义，再把注入依赖放回每个智能体的嵌入配置。
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。
            maze: 提供格子、地址、可通行性与寻路能力的地图实例。
            conversation: 当前步骤的对话上下文或已经完成的会话记录。
            logger: 记录运行诊断信息的日志器。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。
            random_source: 运行私有的伪随机数生成器，用于保证快照恢复后的确定性。
            skills: 当前智能体可调用的技能指令仓库或执行器集合。
            models: 按用途组织的运行私有模型注册表；为空时按配置创建。 默认值：`None`。
            model_trace: 模型调用轨迹写入器；为空时不记录物理调用明细。 默认值：`None`。
            algorithm: 当前运行选定的算法配置；为空时使用系统默认算法参数。 默认值：`None`。
            memory_stream: 运行私有的持久化记忆流；为空时只使用进程内关联记忆。 默认值：`None`。

        返回:
            无返回值。
        """
        embedding = config.get("associate", {}).get("embedding", {})
        runtime_embedding = {
            key: embedding[key]
            for key in ("_control", "_logger", "_sleep")
            if key in embedding
        }
        serializable_config = dict(config)
        serializable_associate = dict(serializable_config.get("associate", {}))
        serializable_embedding = {
            key: value
            for key, value in embedding.items()
            if key not in runtime_embedding
        }
        serializable_associate["embedding"] = serializable_embedding
        serializable_config["associate"] = serializable_associate
        agent_config = copy.deepcopy(serializable_config)
        agent_config.get("associate", {}).get("embedding", {}).update(runtime_embedding)
        self.name = agent_config["name"]
        self.agent_key = agent_config.get("agent_key", self.name)
        self.maze = maze
        self.conversation = conversation
        self._llm = None
        self.logger = logger
        self._clock = clock
        self._rng = random_source
        self._skills = skills
        self._models = models
        self._model_trace = model_trace
        self._algorithm = algorithm
        self._memory_stream = memory_stream
        self._current_step_no = None
        self._memory_id_map = {
            str(index_id): str(memory_id)
            for index_id, memory_id in (agent_config.get("memory_id_map") or {}).items()
        }
        self._result_events = []

        # 智能体配置
        self.percept_config = agent_config["percept"]
        self.think_config = agent_config["think"]
        chat_config = agent_config.get("chat", {})
        self.chat_iter = chat_config.get(
            "max_iterations", agent_config.get("chat_iter", 4)
        )
        self.chat_cooldown_minutes = chat_config.get("cooldown_minutes", 60)
        self.chat_stop_after_hour = chat_config.get("stop_after_hour", 23)
        self.repeat_detection_enabled = chat_config.get(
            "repeat_detection_enabled", True
        )
        clock_timezone = clock.get_date().tzinfo
        self.last_chat_at = {
            str(agent_key): utils.to_date(
                observed_at,
                naive_timezone=clock_timezone,
            )
            for agent_key, observed_at in (
                agent_config.get("last_chat_at") or {}
            ).items()
        }

        # 记忆组件
        self.spatial = memory.Spatial(
            **agent_config["spatial"], random_source=random_source
        )
        self.schedule = memory.Schedule(**agent_config["schedule"], clock=clock)
        self.associate = memory.Associate(
            os.path.join(agent_config["storage_root"], "associate"),
            **agent_config["associate"],
            clock=clock,
        )
        self.concepts, self.chats = [], agent_config.get("chats", [])

        # 提示词与模型调用
        self.scratch = prompt.Scratch(
            self.name,
            agent_config["currently"],
            agent_config["scratch"],
            clock=clock,
            random_source=random_source,
            skills=skills,
        )

        # 当前状态
        status = {"poignancy": 0}
        self.status = utils.update_dict(status, agent_config.get("status", {}))
        self.plan = agent_config.get("plan", {})

        # 运行记录
        self.last_record = self._clock.daily_duration()

        # 行为与事件
        if "action" in agent_config:
            self.action = memory.Action.from_dict(agent_config["action"], clock=clock)
            # 已验证检查点保存的是准确观测坐标。一个地址可能覆盖多个格子，若重新选格子，
            # 会在随机数状态恢复前悄悄造成恢复轨迹分叉。
            if "coord" in agent_config:
                initial_coord = tuple(agent_config["coord"])
            else:
                tiles = self.maze.get_address_tiles(self.get_event().address)
                initial_coord = self._rng.choice(list(tiles))
        else:
            initial_coord = tuple(agent_config["coord"])
            tile = self.maze.tile_at(initial_coord)
            address = tile.get_address("game_object", as_list=True)
            self.action = memory.Action(
                memory.Event(self.name, address=address),
                memory.Event(address[-1], address=address),
                clock=clock,
            )

        # 把智能体最新事件同步到地图。
        self.coord, self.path = None, None
        self.move(initial_coord, agent_config.get("path"))
        if self.coord is None:
            self.coord = initial_coord

    def abstract(self):
        """执行 `Agent` 的`abstract`操作。

        返回:
            返回函数计算得到的结果。
        """
        des = {
            "name": self.name,
            "currently": self.scratch.currently,
            "tile": self.maze.tile_at(self.coord).abstract(),
            "status": self.status,
            "concepts": {c.node_id: c.abstract() for c in self.concepts},
            "chats": self.chats,
            "action": self.action.abstract(),
            "associate": self.associate.abstract(),
        }
        if self.schedule.scheduled():
            des["schedule"] = self.schedule.abstract()
        if self.llm_available():
            des["llm"] = self._llm.get_summary()
        # if self.plan.get("path"):
        #     des["path"] = "-".join(
        #         ["{},{}".format(c[0], c[1]) for c in self.plan["path"]]
        #     )
        return des

    def __str__(self):
        """执行`str`的内部处理，供当前模块或类复用。

        返回:
            返回函数计算得到的结果。
        """
        return utils.dump_dict(self.abstract())

    def reset(self):
        """执行 `Agent` 的`reset`操作。

        返回:
            无返回值。
        """
        if not self._llm:
            if self._models is not None:
                self._llm = self._models.get("chat")
            else:
                self._llm = create_llm_model(
                    self.think_config["llm"], recorder=self._model_trace
                )

    def begin_step(self, step_no):
        """执行 `Agent` 的`begin`仿真步操作。

        参数:
            step_no: 当前仿真步编号；提交后按运行维度单调递增。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if not isinstance(step_no, int) or isinstance(step_no, bool) or step_no < 1:
            raise ValueError("step_no must be a positive integer")
        self._current_step_no = step_no

    def completion(self, func_hint, *args, **kwargs):
        """执行 `Agent` 的`completion`操作。

        参数:
            func_hint: 提示模型或执行器选择目标能力的函数语义提示。
            *args: 传给底层调用的额外位置参数，顺序和含义与被调用接口保持一致。
            **kwargs: 传给底层调用的额外关键字参数，键名和含义与被调用接口保持一致。

        返回:
            返回函数计算得到的结果。
        """
        assert hasattr(self.scratch, "prompt_" + func_hint), (
            "Can not find func prompt_{} from scratch".format(func_hint)
        )
        func = getattr(self.scratch, "prompt_" + func_hint)
        res = func(*args, **kwargs)._asdict()
        title, msg = "{}.{}".format(self.name, func_hint), {}
        output = res.get("failsafe")
        if self.llm_available():
            self.logger.info("{} -> {}".format(self.name, func_hint))
            output = self._llm.completion(
                **res,
                caller=func_hint,
                agent_key=self.agent_key,
                prompt_key=func_hint,
                step_no=self._current_step_no,
            )
            msg = {"<PROMPT>": "\n" + res["prompt"] + "\n"}
            msg.update({"response": output})
        self.logger.debug(utils.block_msg(title, msg))
        revision_resolver = getattr(self._skills, "revision", None)
        skill_revision = (
            revision_resolver(func_hint)
            if callable(revision_resolver)
            else "unversioned"
        )
        self._result_events.append(
            {
                "kind": "skill_execution",
                "agent_key": self.agent_key,
                "skill_name": str(func_hint).replace("_", "-"),
                "skill_revision": skill_revision,
                "output_text": str(output)[:8192],
                "execution_source": "MODEL" if self.llm_available() else "FAILSAFE",
            }
        )
        return output

    def think(self, status, agents_by_name):
        """综合感知、记忆、计划与反思结果，生成智能体下一步行为。

        参数:
            status: 当前仿真步的世界状态映射，包含时间、位置和可观察对象等认知输入。
            agents_by_name: 以智能体展示名为键的运行实例映射，用于解析交互对象。

        返回:
            返回函数计算得到的结果。

        说明:
            认知阶段的调用顺序会影响记忆与计划结果；调整感知、反思、计划的先后关系会改变仿真语义。
        """
        events = self.move(status["coord"], status.get("path"))
        plan, _ = self.make_schedule()

        if (
            plan["describe"] == "sleeping" or "睡" in plan["describe"]
        ) and self.is_awake():
            self.logger.info("{} is going to sleep...".format(self.name))
            address = self._required_spatial_address("睡觉")
            self.action = memory.Action(
                memory.Event(self.name, "正在", "睡觉", address=address, emoji="😴"),
                memory.Event(
                    address[-1],
                    "被占用",
                    self.name,
                    address=address,
                    emoji="🛌",
                ),
                duration=plan["duration"],
                start=self._clock.daily_time(plan["start"]),
                clock=self._clock,
            )
        if self.is_awake():
            self.percept()
            self.make_plan(agents_by_name)
            self.reflect()
        else:
            if self.action.finished():
                self.action = self._determine_action()

        emojis = {}
        if self.action:
            emojis[self.name] = {"emoji": self.get_event().emoji, "coord": self.coord}
        for eve, coord in events.items():
            if eve.subject in agents_by_name:
                continue
            emojis[":".join(eve.address)] = {"emoji": eve.emoji, "coord": coord}
        self.plan = {
            "name": self.name,
            "path": self.find_path(agents_by_name),
            "emojis": emojis,
        }
        return self.plan

    def _required_spatial_address(self, hint):
        """执行`required`空间数据`address`的内部处理，供当前模块或类复用。

        参数:
            hint: 帮助模型、解析器或选择器缩小候选范围的提示信息。

        返回:
            返回函数计算得到的结果。

        异常:
            AgentSpatialConfigurationError: 当底层操作报告该异常条件时抛出。
        """
        address = self.spatial.find_address(hint, as_list=True)
        if not address:
            raise AgentSpatialConfigurationError(
                f"Agent“{self.name}”缺少有效的{hint}地址；"
                "请在 Agent 的“初始位置与空间”中配置居住地、睡觉地址和床"
            )
        address_key = ":".join(address)
        if address_key not in self.maze.address_tiles:
            raise AgentSpatialConfigurationError(
                f"Agent“{self.name}”的{hint}地址“{address_key}”不属于当前地图；"
                "请检查 Agent 的空间定义与实验地图"
            )
        return address

    def move(self, coord, path=None):
        """执行 `Agent` 的`move`操作。

        参数:
            coord: 地图坐标，按 `(行, 列)` 或项目约定的二维顺序表示。
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        events = {}

        def _update_tile(coord):
            """更新`tile`。

            参数:
                coord: 地图坐标，按 `(行, 列)` 或项目约定的二维顺序表示。

            返回:
                返回函数计算得到的结果。
            """
            tile = self.maze.tile_at(coord)
            if not self.action:
                return {}
            if not tile.update_events(self.get_event()):
                tile.add_event(self.get_event())
            obj_event = self.get_event(False)
            if obj_event:
                self.maze.update_obj(coord, obj_event)
            return {e: coord for e in tile.get_events()}

        if self.coord and self.coord != coord:
            tile = self.get_tile()
            tile.remove_events(subject=self.name)
            if tile.has_address("game_object"):
                addr = tile.get_address("game_object")
                self.maze.update_obj(self.coord, memory.Event(addr[-1], address=addr))
            events.update({e: self.coord for e in tile.get_events()})
        if not path:
            events.update(_update_tile(coord))
        self.coord = coord
        self.path = path or []

        return events

    def make_schedule(self):
        """为指定时间范围生成并规范化智能体日程。

        返回:
            返回函数计算得到的结果。
        """
        if not self.schedule.scheduled():
            self.logger.info("{} is making schedule...".format(self.name))
            # 更新面向展示的当前活动摘要。
            if self.associate.index.nodes_num > 0:
                self.associate.cleanup_index()
                focus = [
                    f"{self.name} 在 {self._clock.daily_format_cn()} 的计划。",
                    f"在 {self.name} 的生活中，重要的近期事件。",
                ]
                retrieved = self.associate.retrieve_focus(focus)
                self.logger.info(
                    "{} retrieved {} concepts".format(self.name, len(retrieved))
                )
                if retrieved:
                    plan = self.completion("retrieve_plan", retrieved)
                    thought = self.completion("retrieve_thought", retrieved)
                    self.scratch.currently = self.completion(
                        "retrieve_currently", plan, thought
                    )
            # 生成首次运行使用的日程。
            self.schedule.create = self._clock.get_date()
            wake_up = self.completion("wake_up")
            init_schedule = self.completion("schedule_init", wake_up)
            # 生成当天完整日程。
            hours = [f"{i}:00" for i in range(24)]
            # seed = [(h, "sleeping") for h in hours[:wake_up]]
            seed = [(h, "睡觉") for h in hours[:wake_up]]
            seed += [(h, "") for h in hours[wake_up:]]
            schedule = {}
            for _ in range(self.schedule.max_try):
                schedule = {h: s for h, s in seed[:wake_up]}
                schedule.update(
                    self.completion("schedule_daily", wake_up, init_schedule)
                )
                if len(set(schedule.values())) >= self.schedule.diversity:
                    break

            def _to_duration(date_str):
                """执行`to``duration`的内部处理，供当前模块或类复用。

                参数:
                    date_str: 需要解析为仿真日期或时间的文本。

                返回:
                    返回函数计算得到的结果。
                """
                return utils.daily_duration(utils.to_date(date_str, "%H:%M"))

            schedule = {_to_duration(k): v for k, v in schedule.items()}
            starts = list(sorted(schedule.keys()))
            for idx, start in enumerate(starts):
                end = starts[idx + 1] if idx + 1 < len(starts) else 24 * 60
                self.schedule.add_plan(schedule[start], end - start)
            schedule_time = self._clock.time_format_cn(self.schedule.create)
            thought = "这是 {} 在 {} 的计划：{}".format(
                self.name, schedule_time, "；".join(init_schedule)
            )
            event = memory.Event(
                self.name,
                "计划",
                schedule_time,
                describe=thought,
                address=self.get_tile().get_address(),
            )
            self._add_concept(
                "thought",
                event,
                expire=self.schedule.create + datetime.timedelta(days=30),
            )
        # 把当前粗粒度计划分解成可执行时间片。
        plan, _ = self.schedule.current_plan()
        if self.schedule.decompose(plan):
            decompose_schedule = self.completion(
                "schedule_decompose", plan, self.schedule
            )
            decompose, start = [], plan["start"]
            for describe, duration in decompose_schedule:
                decompose.append(
                    {
                        "idx": len(decompose),
                        "describe": describe,
                        "start": start,
                        "duration": duration,
                    }
                )
                start += duration
            plan["decompose"] = decompose
        return self.schedule.current_plan()

    def revise_schedule(
        self,
        event,
        start,
        duration,
        *,
        local_interruption=False,
        reason="ACTION_REVISED",
    ):
        """执行 `Agent` 的`revise`日程操作。

        参数:
            event: 当前感知、处理或写入结果账本的领域事件。
            start: 处理区间的起始位置或起始时间。
            duration: 行为、对话或日程项占用的虚拟时间长度。
            local_interruption: 是否只在当前日程片段内处理突发事件，不重排整日计划。 默认值：`False`。
            reason: 触发当前操作的原因文本或协议枚举值，用于审计和状态迁移。 默认值：`'ACTION_REVISED'`。

        返回:
            无返回值。
        """
        self.action = memory.Action(
            event, start=start, duration=duration, clock=self._clock
        )
        if local_interruption:
            self.schedule.insert_interruption(event.get_describe(), start, duration)
        else:
            plan, _ = self.schedule.current_plan()
            if len(plan["decompose"]) > 0:
                plan["decompose"] = self.completion(
                    "schedule_revise", self.action, self.schedule
                )
        schedule = copy.deepcopy(self.schedule.daily_schedule)
        content_hash = hashlib.sha256(
            json.dumps(
                schedule,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._result_events.append(
            {
                "kind": "schedule",
                "agent_key": self.agent_key,
                "reason": reason,
                "content_hash": content_hash,
                "schedule": tuple(schedule),
            }
        )

    def percept(self):
        """感知智能体周边环境，并把新事件写入短期记忆。

        返回:
            无返回值。
        """
        scope = self.maze.get_scope(self.coord, self.percept_config)
        # 更新空间记忆。
        for tile in scope:
            if tile.has_address("game_object"):
                self.spatial.add_leaf(tile.address)
        events, arena = {}, self.get_tile().get_address("arena")
        # 收集感知范围内的事件。
        for tile in scope:
            if not tile.events or tile.get_address("arena") != arena:
                continue
            dist = math.dist(tile.coord, self.coord)
            for event in tile.get_events():
                if dist < events.get(event, float("inf")):
                    events[event] = dist
        events = list(sorted(events.keys(), key=lambda k: events[k]))
        # 把事件解析为可写入记忆的概念。
        self.concepts, valid_num = [], 0
        for idx, event in enumerate(events[: self.percept_config["att_bandwidth"]]):
            recent_nodes = (
                self.associate.retrieve_events() + self.associate.retrieve_chats()
            )
            recent_nodes = set(n.describe for n in recent_nodes)
            if event.get_describe() not in recent_nodes:
                if event.object == "idle" or event.object == "空闲":
                    node = Concept.from_event(
                        "idle_" + str(idx),
                        "event",
                        event,
                        poignancy=1,
                        clock=self._clock,
                    )
                else:
                    valid_num += 1
                    node_type = "chat" if event.fit(self.name, "对话") else "event"
                    node = self._add_concept(node_type, event)
                    self.status["poignancy"] += node.poignancy
                self.concepts.append(node)
        self.concepts = [c for c in self.concepts if c.event.subject != self.name]
        self.logger.info(
            "{} percept {}/{} concepts".format(self.name, valid_num, len(self.concepts))
        )

    def make_plan(self, agents_by_name):
        """依据当前日程与环境状态生成下一阶段行动计划。

        参数:
            agents_by_name: 以智能体展示名为键的运行实例映射，用于解析交互对象。

        返回:
            无返回值。
        """
        if self._reaction(agents_by_name):
            return
        if self.path:
            return
        if self.action.finished():
            self.action = self._determine_action()

    def choose_game_object_interaction(self, interactions, planned_path):
        """执行 `Agent` 的`choose`仿真世界对象`interaction`操作。

        参数:
            interactions: 当前智能体可以观察或参与的对象交互候选集合。
            planned_path: `planned`对应的文件系统路径。

        返回:
            返回函数计算得到的结果。
        """

        if not interactions:
            return "NONE"
        return self.completion(
            "decide_game_object_interaction",
            self.get_event().get_describe(),
            self.get_tile().get_address(as_list=False),
            tuple(tuple(coord) for coord in (planned_path or ())),
            interactions,
        )

    def receive_game_object_observation(
        self,
        *,
        object_key,
        object_name,
        interaction_key,
        skill_name,
        skill_revision,
        request,
        response,
        address,
    ):
        """执行 `Agent` 的`receive`仿真世界对象`observation`操作。

        参数:
            object_key: 用于稳定定位对象的键。
            object_name: 智能体准备交互的世界对象名称。
            interaction_key: 用于稳定定位`interaction`的键。
            skill_name: 需要调用的技能名称，必须能在当前运行的技能快照中解析。
            skill_revision: 当前运行固定使用的技能修订标识。
            request: 待执行、记录或发送到外部模型的请求对象。
            response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。

        返回:
            返回函数计算得到的结果。
        """

        description = f"{object_name}回应{self.name}：{response}"
        event = memory.Event(
            object_name,
            "回应",
            self.name,
            describe=description,
            address=list(address) or self.get_tile().get_address(),
            emoji="ℹ️",
        )
        concept = self._add_concept("event", event)
        self.concepts.append(concept)
        directive = self.completion(
            "decide_game_object_response",
            self.get_event().get_describe(),
            object_name,
            request,
            response,
        )
        if directive not in {"WAIT", "CONTINUE"}:
            directive = "WAIT"
        self._result_events.append(
            {
                "kind": "game_object_interaction",
                "agent_key": self.agent_key,
                "object_key": object_key,
                "object_name": object_name,
                "interaction_key": interaction_key,
                "skill_name": skill_name,
                "skill_revision": skill_revision,
                "request": request,
                "response": response,
                "agent_decision": directive,
                "location": tuple(event.address),
            }
        )
        return directive

        # 同时生成智能体行为事件和对象状态事件。

    def make_event(self, subject, describe, address):
        # emoji = self.completion("describe_emoji", describe)
        # return self.completion(
        #     "describe_event", subject, subject + describe, address, emoji
        # )

        """执行 `Agent` 的`make`事件操作。

        参数:
            subject: 事件三元组中的主体，通常是智能体或世界对象标识。
            describe: 事件、行为或记忆的人类可读描述文本。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。

        返回:
            返回函数计算得到的结果。
        """
        e_describe = (
            describe.replace("(", "").replace(")", "").replace("<", "").replace(">", "")
        )
        if e_describe.startswith(subject + "此时"):
            e_describe = e_describe[len(subject + "此时") :]
        if e_describe.startswith(subject):
            e_describe = e_describe[len(subject) :]
        event = memory.Event(
            subject, "此时", e_describe, describe=describe, address=address
        )
        return event

    def reflect(self):
        """根据近期记忆的重要性生成更高层次的反思记忆。

        返回:
            无返回值。
        """

        def _add_thought(thought, evidence=None):
            # event = self.completion(
            #     "describe_event",
            #     self.name,
            #     thought,
            #     address=self.get_tile().get_address(),
            # )
            """执行`add``thought`的内部处理，供当前模块或类复用。

            参数:
                thought: 智能体生成并准备写入记忆的思考内容。
                evidence: 支持当前反思或记忆结论的证据节点集合。 默认值：`None`。

            返回:
                返回函数计算得到的结果。
            """
            event = self.make_event(self.name, thought, self.get_tile().get_address())
            return self._add_concept("thought", event, filling=evidence)

        if self.status["poignancy"] < self.think_config["poignancy_max"]:
            return
        nodes = self.associate.retrieve_events() + self.associate.retrieve_thoughts()
        if not nodes:
            return
        self.logger.info(
            "{} reflect(P{}/{}) with {} concepts...".format(
                self.name,
                self.status["poignancy"],
                self.think_config["poignancy_max"],
                len(nodes),
            )
        )
        nodes = sorted(nodes, key=lambda n: n.access, reverse=True)[
            : self.associate.max_importance
        ]
        # 汇总近期思考。
        focus = self.completion("reflect_focus", nodes, 3)
        retrieved = self.associate.retrieve_focus(focus, reduce_all=False)
        for r_nodes in retrieved.values():
            thoughts = self.completion("reflect_insights", r_nodes, 5)
            for thought, evidence in thoughts:
                _add_thought(thought, evidence)
        # 汇总近期对话。
        if self.chats:
            recorded, evidence = set(), []
            for name, _ in self.chats:
                if name == self.name or name in recorded:
                    continue
                res = self.associate.retrieve_chats(name)
                if res and len(res) > 0:
                    node = res[-1]
                    evidence.append(node.node_id)
            thought = self.completion("reflect_chat_planing", self.chats)
            _add_thought(f"对于 {self.name} 的计划：{thought}", evidence)
            thought = self.completion("reflect_chat_memory", self.chats)
            _add_thought(f"{self.name} {thought}", evidence)
        self.status["poignancy"] = 0
        self.chats = []

    def find_path(self, agents_by_name):
        """在地图可通行区域内搜索从起点到终点的移动路径。

        参数:
            agents_by_name: 以智能体展示名为键的运行实例映射，用于解析交互对象。

        返回:
            返回函数计算得到的结果。
        """
        address = self.get_event().address
        if self.path:
            return self.path
        if address == self.get_tile().get_address():
            return []
        if address[0] == "<waiting>":
            return []
        if address[0] == "<persona>":
            target_tiles = self.maze.get_around(agents_by_name[address[1]].coord)
        else:
            target_tiles = self.maze.get_address_tiles(address)
        if tuple(self.coord) in target_tiles:
            return []

        # 过滤仅由智能体自身产生的格子事件。
        def _ignore_target(t_coord):
            """执行`ignore``target`的内部处理，供当前模块或类复用。

            参数:
                t_coord: 路径搜索或移动判断使用的目标坐标。

            返回:
                返回函数计算得到的结果。
            """
            if list(t_coord) == list(self.coord):
                return True
            events = self.maze.tile_at(t_coord).get_events()
            if any(e.subject in agents_by_name for e in events):
                return True
            return False

        target_tiles = [t for t in target_tiles if not _ignore_target(t)]
        if not target_tiles:
            return []
        if len(target_tiles) >= 4:
            target_tiles = self._rng.sample(target_tiles, 4)
        pathes = {t: self.maze.find_path(self.coord, t) for t in target_tiles}
        target = min(pathes, key=lambda p: len(pathes[p]))
        return pathes[target][1:]

    def _determine_action(self):
        """执行`determine``action`的内部处理，供当前模块或类复用。

        返回:
            返回函数计算得到的结果。
        """
        self.logger.info("{} is determining action...".format(self.name))
        plan, de_plan = self.schedule.current_plan()
        describes = [plan["describe"], de_plan["describe"]]
        address = self.spatial.find_address(describes[0], as_list=True)
        if not address:
            tile = self.get_tile()
            kwargs = {
                "describes": describes,
                "spatial": self.spatial,
                "address": tile.get_address("world", as_list=True),
            }
            kwargs["address"].append(
                self.completion("determine_sector", **kwargs, tile=tile)
            )
            arenas = self.spatial.get_leaves(kwargs["address"])
            if len(arenas) == 1:
                kwargs["address"].append(arenas[0])
            else:
                kwargs["address"].append(self.completion("determine_arena", **kwargs))
            objs = self.spatial.get_leaves(kwargs["address"])
            if len(objs) == 1:
                kwargs["address"].append(objs[0])
            elif len(objs) > 1:
                kwargs["address"].append(self.completion("determine_object", **kwargs))
            address = kwargs["address"]

        event = self.make_event(self.name, describes[-1], address)
        obj_describe = self.completion("describe_object", address[-1], describes[-1])
        obj_event = self.make_event(address[-1], obj_describe, address)

        event.emoji = f"{de_plan['describe']}"

        return memory.Action(
            event,
            obj_event,
            duration=de_plan["duration"],
            start=self._clock.daily_time(de_plan["start"]),
            clock=self._clock,
        )

    def _reaction(self, agents_by_name=None, ignore_words=None):
        """执行`reaction`的内部处理，供当前模块或类复用。

        参数:
            agents_by_name: 以智能体展示名为键的运行实例映射，用于解析交互对象。 默认值：`None`。
            ignore_words: 比较或检索文本时需要忽略的词集合。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        focus = None
        agents_by_name = agents_by_name or {}
        ignore_words = ignore_words or ["空闲"]

        def _focus(concept):
            """执行`focus`的内部处理，供当前模块或类复用。

            参数:
                concept: 从事件或记忆中抽取的认知概念节点。

            返回:
                返回函数计算得到的结果。
            """
            return concept.event.subject in agents_by_name

        def _ignore(concept):
            """执行`ignore`的内部处理，供当前模块或类复用。

            参数:
                concept: 从事件或记忆中抽取的认知概念节点。

            返回:
                返回函数计算得到的结果。
            """
            return any(i in concept.describe for i in ignore_words)

        if agents_by_name:
            priority = [i for i in self.concepts if _focus(i)]
            if priority:
                focus = self._rng.choice(priority)
        if not focus:
            priority = [i for i in self.concepts if not _ignore(i)]
            if priority:
                focus = self._rng.choice(priority)
        if not focus or focus.event.subject not in agents_by_name:
            return
        other = agents_by_name[focus.event.subject]
        focus = self.associate.get_relation(focus)

        if self._chat_with(other, focus):
            return True
        if self._wait_other(other, focus):
            return True
        return False

    def _skip_react(self, other):
        """执行`skip``react`的内部处理，供当前模块或类复用。

        参数:
            other: 当前操作使用的`other`。

        返回:
            返回函数计算得到的结果。
        """

        def _skip(event):
            """执行`skip`的内部处理，供当前模块或类复用。

            参数:
                event: 当前感知、处理或写入结果账本的领域事件。

            返回:
                返回函数计算得到的结果。
            """
            if (
                not event.address
                or "sleeping" in event.get_describe(False)
                or "睡觉" in event.get_describe(False)
            ):
                return True
            if event.predicate == "待开始":
                return True
            return False

        if self._clock.daily_duration(mode="hour") >= self.chat_stop_after_hour:
            return True
        if _skip(self.get_event()) or _skip(other.get_event()):
            return True
        return False

    def _chat_with(self, other, focus):
        """执行`chat``with`的内部处理，供当前模块或类复用。

        参数:
            other: 当前操作使用的`other`。
            focus: 当前反思、检索或对话需要重点关注的主题。

        返回:
            返回函数计算得到的结果。
        """
        if (
            len(self.schedule.daily_schedule) < 1
            or len(other.schedule.daily_schedule) < 1
        ):
            # 初始化对话状态。
            return False
        if self._skip_react(other):
            return False
        if self.path or other.path:
            return False
        if self.get_event().fit(predicate="对话") or other.get_event().fit(
            predicate="对话"
        ):
            return False

        self.last_chat_at = getattr(self, "last_chat_at", {})
        other.last_chat_at = getattr(other, "last_chat_at", {})
        last_chat_at = self.last_chat_at.get(other.agent_key)
        if last_chat_at is not None:
            delta = self._clock.get_delta(last_chat_at)
            self.logger.info(
                "last chat between {} and {} was {} min ago".format(
                    self.name, other.name, delta
                )
            )
            if delta < self.chat_cooldown_minutes:
                return False

        chats = self.associate.retrieve_chats(other.name)

        if not self.completion("decide_chat", self, other, focus, chats):
            return False

        self.logger.info("{} decides chat with {}".format(self.name, other.name))
        start, chats = self._clock.get_date(), []
        relations = [
            self.completion("summarize_relation", self, other.name),
            other.completion("summarize_relation", other, self.name),
        ]

        def generate_valid_turn(speaker, listener, relation):
            """执行 `Agent` 的`generate``valid``turn`操作。

            参数:
                speaker: 对话中生成当前消息的智能体。
                listener: 对话中接收当前消息的智能体。
                relation: 两个智能体之间的关系描述或关系边记录。

            返回:
                返回函数计算得到的结果。
            """
            for attempt in range(2):
                text = speaker.completion(
                    "generate_chat", speaker, listener, relation, chats
                )
                if valid_chat_message(text, chats):
                    return " ".join(text.split()).strip()
                speaker.logger.warning(
                    "%s rejected invalid chat turn (attempt %s)",
                    speaker.name,
                    attempt + 1,
                )
            return None

        for i in range(self.chat_iter):
            text = generate_valid_turn(self, other, relations[0])
            if text is None:
                break

            if i > 0:
                if self.repeat_detection_enabled:
                    # 对于发起对话的Agent，从第2轮对话开始，检查是否出现“复读”现象
                    end = self.completion(
                        "generate_chat_check_repeat", self, chats, text
                    )
                    if end:
                        break
                chats.append((self.name, text))
                # 话题结束检测独立于复读检测开关。
                end = self.completion("decide_chat_terminate", self, other, chats)
                if end:
                    break
            else:
                chats.append((self.name, text))

            text = generate_valid_turn(other, self, relations[1])
            if text is None:
                break
            if i > 0 and self.repeat_detection_enabled:
                # 对于响应对话的Agent，从第2轮开始，检查是否出现“复读”现象
                end = self.completion("generate_chat_check_repeat", other, chats, text)
                if end:
                    break

            chats.append((other.name, text))

            # 对于响应对话的Agent，从第1轮开始，检查话题是否结束
            end = other.completion("decide_chat_terminate", other, self, chats)
            if end:
                break

        if len(chats) < 2:
            self.logger.warning(
                "%s and %s discarded an incomplete conversation",
                self.name,
                other.name,
            )
            return False

        key = self._clock.get_date("%Y%m%d-%H:%M")
        if key not in self.conversation.keys():
            self.conversation[key] = []
        self.conversation[key].append(
            {
                f"{self.name} -> {other.name} @ {'，'.join(self.get_event().address)}": chats
            }
        )

        self.logger.info(
            "{} and {} has chats\n  {}".format(
                self.name,
                other.name,
                "\n  ".join(["{}: {}".format(n, c) for n, c in chats]),
            )
        )
        chat_summary = self.completion("summarize_chats", chats)
        if not valid_chat_message(chat_summary):
            chat_summary = "；".join(
                "{}：{}".format(speaker, content) for speaker, content in chats
            )
        chars_per_minute = (
            self._algorithm.chat_chars_per_minute if self._algorithm else 240
        )
        duration = estimate_chat_duration(chats, chars_per_minute)
        self._result_events.append(
            {
                "kind": "conversation",
                "participants": (self.agent_key, other.agent_key),
                "location": tuple(self.get_event().address),
                "messages": tuple(chats),
                "summary": chat_summary,
                "start": start,
                "duration_minutes": duration,
                "duration_source": "ESTIMATED",
            }
        )
        self.last_chat_at[other.agent_key] = start
        other.last_chat_at[self.agent_key] = start
        self.schedule_chat(chats, chat_summary, start, duration, other)
        other.schedule_chat(chats, chat_summary, start, duration, self)
        return True

    def _wait_other(self, other, focus):
        """执行`wait``other`的内部处理，供当前模块或类复用。

        参数:
            other: 当前操作使用的`other`。
            focus: 当前反思、检索或对话需要重点关注的主题。

        返回:
            返回函数计算得到的结果。
        """
        if self._skip_react(other):
            return False
        if not self.path:
            return False
        if self.get_event().address != other.get_tile().get_address():
            return False
        if not self.completion("decide_wait", self, other, focus):
            return False
        self.logger.info("{} decides wait to {}".format(self.name, other.name))
        start = self._clock.get_date()
        # duration = other.action.end - start
        t = other.action.end - start
        duration = int(t.total_seconds() / 60)
        event = memory.Event(
            self.name,
            "waiting to start",
            self.get_event().get_describe(False),
            # address=["<waiting>"] + self.get_event().address,
            address=self.get_event().address,
            emoji=f"⌛",
        )
        self.revise_schedule(event, start, duration)

    def schedule_chat(self, chats, chats_summary, start, duration, other, address=None):
        """执行 `Agent` 的日程`chat`操作。

        参数:
            chats: 按时间顺序排列的对话消息或说话人—内容二元组。
            chats_summary: 对既有对话内容的压缩摘要，用于后续认知提示词。
            start: 处理区间的起始位置或起始时间。
            duration: 行为、对话或日程项占用的虚拟时间长度。
            other: 当前操作使用的`other`。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。 默认值：`None`。

        返回:
            无返回值。
        """
        self.chats.extend(chats)
        event = memory.Event(
            self.name,
            "对话",
            other.name,
            describe=chats_summary,
            address=address or self.get_tile().get_address(),
            emoji=f"💬",
        )
        self.revise_schedule(
            event,
            start,
            duration,
            local_interruption=True,
            reason="CHAT_INSERTED",
        )

    def _add_concept(
        self,
        e_type,
        event,
        create=None,
        expire=None,
        filling=None,
    ):
        """执行`add``concept`的内部处理，供当前模块或类复用。

        参数:
            e_type: 写入关联记忆时使用的事件或记忆类型。
            event: 当前感知、处理或写入结果账本的领域事件。
            create: 记忆、事件或记录的创建时间；为空时使用当前仿真时间。 默认值：`None`。
            expire: 记忆的过期时间；为空时按记忆策略计算或表示不过期。 默认值：`None`。
            filling: 写入记忆节点的补充结构化内容。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        if event.fit(None, "is", "idle"):
            poignancy = 1
        elif event.fit(None, "此时", "空闲"):
            poignancy = 1
        elif e_type == "chat":
            poignancy = self.completion("poignancy_chat", event)
        else:
            poignancy = self.completion("poignancy_event", event)
        self.logger.debug("{} add associate {}".format(self.name, event))
        concept = self.associate.add_node(
            e_type,
            event,
            poignancy,
            create=create,
            expire=expire,
            filling=filling,
        )
        canonical_memory_id = concept.node_id
        canonical_evidence = tuple(
            self._memory_id_map.get(str(memory_id), str(memory_id))
            for memory_id in concept.evidence_memory_ids
        )
        if self._memory_stream is not None:
            stored_memory = self._memory_stream.append(
                agent_key=self.agent_key,
                content=concept.describe,
                kind=e_type,
                poignancy=max(1, min(10, int(round(concept.poignancy)))),
                expires_at=concept.expire,
                subject=concept.event.subject,
                predicate=concept.event.predicate,
                object=concept.event.object,
                address=concept.event.address,
                evidence_memory_ids=canonical_evidence,
                emit_event=False,
            )
            canonical_memory_id = stored_memory["id"]
        self._memory_id_map[concept.node_id] = canonical_memory_id
        self._result_events.append(
            {
                "kind": "memory",
                "memory_kind": MemoryDeltaKind.CREATED.value,
                "agent_key": self.agent_key,
                "memory_id": canonical_memory_id,
                "index_node_id": concept.node_id,
                "memory_type": e_type.upper(),
                "description": concept.describe,
                "poignancy": concept.poignancy,
                "event": concept.event.to_dict(),
                "created_at": concept.create.isoformat(),
                "expires_at": concept.expire.isoformat(),
                "evidence_memory_ids": list(canonical_evidence),
            }
        )
        for memory_id in self.associate.last_evicted:
            canonical_memory_id = self._memory_id_map.pop(memory_id, memory_id)
            if self._memory_stream is not None:
                self._memory_stream.remove(
                    canonical_memory_id,
                    state=MemoryState.EVICTED,
                    emit_event=False,
                )
            self._result_events.append(
                {
                    "kind": "memory",
                    "memory_kind": MemoryDeltaKind.EVICTED.value,
                    "agent_key": self.agent_key,
                    "memory_id": canonical_memory_id,
                    "index_node_id": memory_id,
                    "memory_type": e_type.upper(),
                }
            )
        return concept

    def drain_result_events(self):
        """执行 `Agent` 的`drain`结果`events`操作。

        返回:
            返回函数计算得到的结果。
        """
        lifecycle_reader = getattr(self.associate, "drain_lifecycle_events", None)
        lifecycle = (
            lifecycle_reader()
            if callable(lifecycle_reader)
            else {"accessed": (), "expired": ()}
        )
        for memory_id, memory_type in lifecycle["accessed"]:
            canonical_memory_id = self._memory_id_map.get(memory_id, memory_id)
            if self._memory_stream is not None:
                self._memory_stream.access(canonical_memory_id, emit_event=False)
            self._result_events.append(
                {
                    "kind": "memory",
                    "memory_kind": MemoryDeltaKind.ACCESSED.value,
                    "agent_key": self.agent_key,
                    "memory_id": canonical_memory_id,
                    "index_node_id": memory_id,
                    "memory_type": memory_type.upper(),
                }
            )
        for memory_id, memory_type in lifecycle["expired"]:
            canonical_memory_id = self._memory_id_map.pop(memory_id, memory_id)
            if self._memory_stream is not None:
                self._memory_stream.remove(
                    canonical_memory_id,
                    state=MemoryState.EXPIRED,
                    emit_event=False,
                )
            self._result_events.append(
                {
                    "kind": "memory",
                    "memory_kind": MemoryDeltaKind.EXPIRED.value,
                    "agent_key": self.agent_key,
                    "memory_id": canonical_memory_id,
                    "index_node_id": memory_id,
                    "memory_type": memory_type.upper(),
                }
            )
        events, self._result_events = tuple(self._result_events), []
        return events

    def get_tile(self):
        """获取`tile`。

        返回:
            返回函数计算得到的结果。
        """
        return self.maze.tile_at(self.coord)

    def get_event(self, as_act=True):
        """获取事件。

        参数:
            as_act: 是否把事件转换为智能体行为事件；否则保留原始事件语义。 默认值：`True`。

        返回:
            返回函数计算得到的结果。
        """
        return self.action.event if as_act else self.action.obj_event

    def is_awake(self):
        """判断是否`awake`。

        返回:
            返回函数计算得到的结果。
        """
        if not self.action:
            return True
        if self.get_event().fit(self.name, "is", "sleeping"):
            return False
        if self.get_event().fit(self.name, "正在", "睡觉"):
            return False
        return True

    def llm_available(self):
        """执行 `Agent` 的`llm``available`操作。

        返回:
            返回函数计算得到的结果。
        """
        if not self._llm:
            return False
        return self._llm.is_available()

    def to_dict(self, with_action=True):
        """执行 `Agent` 的`to``dict`操作。

        参数:
            with_action: 返回事件时是否同时附带智能体当前行为。 默认值：`True`。

        返回:
            返回函数计算得到的结果。
        """
        info = {
            "status": self.status,
            "schedule": self.schedule.to_dict(),
            "associate": self.associate.to_dict(),
            "chats": self.chats,
            "currently": self.scratch.currently,
            "last_chat_at": {
                agent_key: observed_at.isoformat()
                for agent_key, observed_at in self.last_chat_at.items()
            },
            "memory_id_map": dict(self._memory_id_map),
        }
        if with_action:
            info.update({"action": self.action.to_dict()})
        return info
