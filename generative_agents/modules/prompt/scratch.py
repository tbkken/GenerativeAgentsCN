import datetime
import re
from collections.abc import Mapping
from string import Template
from pydantic import BaseModel, Field
from collections import namedtuple
from typing import List, Literal, Tuple
from generative_agents.modules import utils
from generative_agents.modules.memory import Event


Result = namedtuple("Result", ["prompt", "callback", "failsafe", "return_type"])
_PATH_VARIABLE = re.compile(
    r"(?<!\$)\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}"
)


class Scratch:
    def __init__(self, name, currently, config, *, clock, random_source, skills):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            name: 目标对象的人类可读名称。
            currently: 传入当前算法的`currently`；其结构与有效范围由类型注解和调用协议共同限定。
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。
            random_source: 运行私有的伪随机数生成器，用于保证快照恢复后的确定性。
            skills: 当前智能体可调用的技能指令仓库或执行器集合。

        返回:
            无返回值。
        """
        self.name = name
        self.currently = currently
        self.config = config
        self._clock = clock
        self._rng = random_source
        self._skills = skills

    @staticmethod
    def _structured_context(data):
        """执行`structured`运行上下文的内部处理，供当前模块或类复用。

        参数:
            data: 待编码、解码、校验或持久化的原始数据。

        返回:
            返回函数计算得到的结果。
        """

        raw_context = data.get("context")
        context = dict(raw_context) if isinstance(raw_context, Mapping) else {}
        if "context" in data and not isinstance(raw_context, Mapping):
            context["background"] = raw_context
        for key, value in data.items():
            if key != "context":
                context.setdefault(key, value)

        for key in ("agent", "another"):
            value = context.get(key)
            if value is None and key == "agent":
                value = data.get("name")
            if value is None or isinstance(value, Mapping) or hasattr(value, "name"):
                continue
            context[key] = {"name": value}
        return context

    def build_prompt(self, template, data):
        """构建提示词。

        参数:
            template: 传入当前算法的`template`；其结构与有效范围由类型注解和调用协议共同限定。
            data: 待编码、解码、校验或持久化的原始数据。

        返回:
            返回函数计算得到的结果。
        """
        render_data = dict(data)
        render_data["context"] = self._structured_context(data)
        prompt_template = Template(self._skills.get(template))
        filled_content = prompt_template.substitute(render_data)

        def replace_path(match):
            """执行 `Scratch` 的`replace`路径操作。

            参数:
                match: 传入当前算法的`match`；其结构与有效范围由类型注解和调用协议共同限定。

            返回:
                返回函数计算得到的结果。

            异常:
                KeyError: 当必需的键或映射项不存在时抛出。
            """
            path = match.group(1)
            parts = path.split(".")
            if parts[0] not in render_data:
                raise KeyError(
                    f"prompt {template} references unknown variable {parts[0]}"
                )
            value = render_data[parts[0]]
            # Drafts published before the explicit-path migration may still use
            # ``{context}`` for a business field while also referencing nested
            # paths such as ``{context.agent.name}``. Preserve that snapshot by
            # resolving the exact root to its namespaced scalar value.
            if (
                path == "context"
                and isinstance(value, Mapping)
                and "background" in value
            ):
                value = value["background"]
            for part in parts[1:]:
                if isinstance(value, Mapping):
                    if part not in value:
                        raise KeyError(
                            f"prompt {template} references unknown path {path}"
                        )
                    value = value[part]
                else:
                    if not hasattr(value, part):
                        raise KeyError(
                            f"prompt {template} references unknown path {path}"
                        )
                    value = getattr(value, part)
            return str(value)

        filled_content = _PATH_VARIABLE.sub(replace_path, filled_content)

        return filled_content

    def _base_desc(self):
        """执行`base``desc`的内部处理，供当前模块或类复用。

        返回:
            返回函数计算得到的结果。
        """
        return self.build_prompt(
            "base_desc",
            {
                "name": self.name,
                "age": self.config["age"],
                "innate": self.config["innate"],
                "learned": self.config["learned"],
                "lifestyle": self.config["lifestyle"],
                "daily_plan": self.config["daily_plan"],
                "date": self._clock.daily_format_cn(),
                "currently": self.currently,
            },
        )

    def prompt_poignancy_event(self, event):
        """执行 `Scratch` 的提示词`poignancy`事件操作。

        参数:
            event: 当前感知、处理或写入结果账本的领域事件。

        返回:
            返回函数计算得到的结果。
        """
        prompt = self.build_prompt(
            "poignancy_event",
            {
                "base_desc": self._base_desc(),
                "agent": self.name,
                "event": event.get_describe(),
            },
        )

        class PoignancyEventResponse(BaseModel):
            res: int = Field(description="事件的情感强度评分，整数，范围1到10")

        return Result(
            prompt, None, self._rng.choice(list(range(10))) + 1, PoignancyEventResponse
        )

    def prompt_poignancy_chat(self, event):
        """执行 `Scratch` 的提示词`poignancy``chat`操作。

        参数:
            event: 当前感知、处理或写入结果账本的领域事件。

        返回:
            返回函数计算得到的结果。
        """
        prompt = self.build_prompt(
            "poignancy_chat",
            {
                "base_desc": self._base_desc(),
                "agent": self.name,
                "event": event.get_describe(),
            },
        )

        class PoignancyChatResponse(BaseModel):
            res: int = Field(description="对话的情感强度评分，整数，范围1到10")

        return Result(
            prompt, None, self._rng.choice(list(range(10))) + 1, PoignancyChatResponse
        )

    def prompt_wake_up(self):
        """执行 `Scratch` 的提示词`wake``up`操作。

        返回:
            返回函数计算得到的结果。
        """
        prompt = self.build_prompt(
            "wake_up",
            {
                "base_desc": self._base_desc(),
                "lifestyle": self.config["lifestyle"],
                "agent": self.name,
            },
        )

        class wakeupResponse(BaseModel):
            res: int = Field(description="起床时间，24小时制的小时数，整数，范围0到11")

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            value = response
            if value > 11:
                value = 11
            return value

        return Result(prompt, _callback, 8, wakeupResponse)

    def prompt_schedule_init(self, wake_up):
        """执行 `Scratch` 的提示词日程`init`操作。

        参数:
            wake_up: 智能体当天计划醒来的虚拟时间。

        返回:
            返回函数计算得到的结果。
        """
        prompt = self.build_prompt(
            "schedule_init",
            {
                "base_desc": self._base_desc(),
                "lifestyle": self.config["lifestyle"],
                "agent": self.name,
                "wake_up": wake_up,
            },
        )

        class schedule_initResponse(BaseModel):
            res: list[str] = Field(
                description="按时间顺序排列的日程活动列表，每项为简短的活动描述"
            )

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            assert len(response) >= 3, "schedule_init: too few items"
            return response

        failsafe = [
            "早上6点起床并完成早餐的例行工作",
            "早上7点吃早餐",
            "早上8点看书",
            "中午12点吃午饭",
            "下午1点小睡一会儿",
            "晚上7点放松一下，看电视",
            "晚上11点睡觉",
        ]
        return Result(prompt, _callback, failsafe, schedule_initResponse)

    def prompt_schedule_daily(self, wake_up, daily_schedule):
        """执行 `Scratch` 的提示词日程`daily`操作。

        参数:
            wake_up: 智能体当天计划醒来的虚拟时间。
            daily_schedule: 智能体当天按时间顺序排列的日程项。

        返回:
            返回函数计算得到的结果。
        """
        hourly_schedule = ""
        for i in range(wake_up):
            hourly_schedule += f"[{i}:00] 睡觉\n"
        for i in range(wake_up, 24):
            hourly_schedule += f"[{i}:00] <活动>\n"

        prompt = self.build_prompt(
            "schedule_daily",
            {
                "base_desc": self._base_desc(),
                "agent": self.name,
                "daily_schedule": "；".join(daily_schedule),
                "hourly_schedule": hourly_schedule,
            },
        )

        class schedule_dailyResponse(BaseModel):
            res: dict[str, str] = Field(
                description="24小时日程表，键为时间字符串如'8:00'，值为该时段的活动描述"
            )

        failsafe = {
            "6:00": "起床并完成早晨的例行工作",
            "7:00": "吃早餐",
            "8:00": "读书",
            "9:00": "读书",
            "10:00": "读书",
            "11:00": "读书",
            "12:00": "吃午饭",
            "13:00": "小睡一会儿",
            "14:00": "小睡一会儿",
            "15:00": "小睡一会儿",
            "16:00": "继续工作",
            "17:00": "继续工作",
            "18:00": "回家",
            "19:00": "放松，看电视",
            "20:00": "放松，看电视",
            "21:00": "睡前看书",
            "22:00": "准备睡觉",
            "23:00": "睡觉",
        }

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            assert len(response) >= 5, "less than 5 schedules"
            return response

        return Result(prompt, _callback, failsafe, schedule_dailyResponse)

    def prompt_schedule_decompose(self, plan, schedule):
        """执行 `Scratch` 的提示词日程`decompose`操作。

        参数:
            plan: 智能体当前计划或等待执行的计划片段。
            schedule: 按虚拟时间排序的日程对象或日程项集合。

        返回:
            返回函数计算得到的结果。
        """

        def _plan_des(plan):
            """执行`plan``des`的内部处理，供当前模块或类复用。

            参数:
                plan: 智能体当前计划或等待执行的计划片段。

            返回:
                返回函数计算得到的结果。
            """
            start, end = schedule.plan_stamps(plan, time_format="%H:%M")
            return f"{start} 至 {end}，{self.name} 计划 {plan['describe']}"

        indices = range(
            max(plan["idx"] - 1, 0), min(plan["idx"] + 2, len(schedule.daily_schedule))
        )

        start, end = schedule.plan_stamps(plan, time_format="%H:%M")
        increment = max(int(plan["duration"] / 100) * 5, 5)

        prompt = self.build_prompt(
            "schedule_decompose",
            {
                "base_desc": self._base_desc(),
                "agent": self.name,
                "plan": "；".join(
                    [_plan_des(schedule.daily_schedule[i]) for i in indices]
                ),
                "increment": increment,
                "start": start,
                "end": end,
            },
        )

        class schedule_decomposeResponse(BaseModel):
            res: List[Tuple[str, int]] = Field(
                description="子任务列表，每项为 [活动描述, 时长分钟数] 的元组"
            )

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            left = plan["duration"] - sum([s[1] for s in response])
            if left > 0:
                response.append((plan["describe"], left))
            return response

        failsafe = [(plan["describe"], 10) for _ in range(int(plan["duration"] / 10))]
        return Result(prompt, _callback, failsafe, schedule_decomposeResponse)

    def prompt_schedule_revise(self, action, schedule):
        """执行 `Scratch` 的提示词日程`revise`操作。

        参数:
            action: 智能体当前选择或已经执行的行为记录。
            schedule: 按虚拟时间排序的日程对象或日程项集合。

        返回:
            返回函数计算得到的结果。
        """
        plan, _ = schedule.current_plan()
        start, end = schedule.plan_stamps(plan, time_format="%H:%M")
        act_start_minutes = utils.daily_duration(action.start)
        original_plan, new_plan = [], []

        def _plan_des(start, end, describe):
            """执行`plan``des`的内部处理，供当前模块或类复用。

            参数:
                start: 处理区间的起始位置或起始时间。
                end: 处理区间的结束位置或结束时间；是否包含由当前接口约定。
                describe: 事件、行为或记忆的人类可读描述文本。

            返回:
                返回函数计算得到的结果。
            """
            if not isinstance(start, str):
                start = start.strftime("%H:%M")
            if not isinstance(end, str):
                end = end.strftime("%H:%M")
            return "[{} 至 {}] {}".format(start, end, describe)

        for de_plan in plan["decompose"]:
            de_start, de_end = schedule.plan_stamps(de_plan, time_format="%H:%M")
            original_plan.append(_plan_des(de_start, de_end, de_plan["describe"]))
            if de_plan["start"] + de_plan["duration"] <= act_start_minutes:
                new_plan.append(_plan_des(de_start, de_end, de_plan["describe"]))
            elif de_plan["start"] <= act_start_minutes:
                new_plan.extend(
                    [
                        _plan_des(de_start, action.start, de_plan["describe"]),
                        _plan_des(
                            action.start, action.end, action.event.get_describe(False)
                        ),
                    ]
                )

        original_plan, new_plan = "\n".join(original_plan), "\n".join(new_plan)

        prompt = self.build_prompt(
            "schedule_revise",
            {
                "agent": self.name,
                "start": start,
                "end": end,
                "original_plan": original_plan,
                "duration": action.duration,
                "event": action.event.get_describe(),
                "new_plan": new_plan,
            },
        )

        class schedule_reviseResponse(BaseModel):
            res: List[Tuple[str, str, str]] = Field(
                description="调整后的完整日程列表，每项为 [开始时间, 结束时间, 活动描述] 的元组，时间格式为 HH:MM"
            )

        def _callback(response):
            # response已经是List[Tuple[str, str, str]]类型
            # 格式: [(开始时间, 结束时间, 描述), ...]
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            decompose = []
            for start, end, describe in response:
                m_start = utils.daily_duration(utils.to_date(start, "%H:%M"))
                m_end = utils.daily_duration(utils.to_date(end, "%H:%M"))
                decompose.append(
                    {
                        "idx": len(decompose),
                        "describe": describe,
                        "start": m_start,
                        "duration": m_end - m_start,
                    }
                )
            return decompose

        return Result(prompt, _callback, plan["decompose"], schedule_reviseResponse)

    def prompt_determine_sector(self, describes, spatial, address, tile):
        """执行 `Scratch` 的提示词`determine``sector`操作。

        参数:
            describes: 需要批量解析或汇总的描述文本集合。
            spatial: 当前智能体或世界使用的空间配置与地址信息。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。
            tile: 传入当前算法的`tile`；其结构与有效范围由类型注解和调用协议共同限定。

        返回:
            返回函数计算得到的结果。
        """
        live_address = spatial.find_address("living_area", as_list=True)[:-1]
        curr_address = tile.get_address("sector", as_list=True)

        prompt = self.build_prompt(
            "determine_sector",
            {
                "agent": self.name,
                "live_sector": live_address[-1],
                "live_arenas": ", ".join(i for i in spatial.get_leaves(live_address)),
                "current_sector": curr_address[-1],
                "current_arenas": ", ".join(
                    i for i in spatial.get_leaves(curr_address)
                ),
                "daily_plan": self.config["daily_plan"],
                "areas": ", ".join(i for i in spatial.get_leaves(address)),
                "complete_plan": describes[0],
                "decomposed_plan": describes[1],
            },
        )

        sectors = spatial.get_leaves(address)
        arenas = {}
        for sec in sectors:
            arenas.update(
                {a: sec for a in spatial.get_leaves(address + [sec]) if a not in arenas}
            )
        failsafe = self._rng.choice(sectors)

        class determine_sectorResponse(BaseModel):
            res: str = Field(
                description="从给定列表中选出的目标区域名称，必须与列表中的某项完全一致"
            )

        def _callback(response):
            # response已经是str类型
            # 验证sector是否在有效列表中，或进行映射
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            if response in sectors:
                return response
            if response in arenas:
                return arenas[response]
            for s in sectors:
                if response.startswith(s):
                    return s
            return failsafe

        return Result(prompt, _callback, failsafe, determine_sectorResponse)

    def prompt_determine_arena(self, describes, spatial, address):
        """执行 `Scratch` 的提示词`determine``arena`操作。

        参数:
            describes: 需要批量解析或汇总的描述文本集合。
            spatial: 当前智能体或世界使用的空间配置与地址信息。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。

        返回:
            返回函数计算得到的结果。
        """
        prompt = self.build_prompt(
            "determine_arena",
            {
                "agent": self.name,
                "target_sector": address[-1],
                "target_arenas": ", ".join(i for i in spatial.get_leaves(address)),
                "daily_plan": self.config["daily_plan"],
                "complete_plan": describes[0],
                "decomposed_plan": describes[1],
            },
        )

        arenas = spatial.get_leaves(address)
        failsafe = self._rng.choice(arenas)

        class determine_arenaResponse(BaseModel):
            res: str = Field(
                description="从给定列表中选出的目标场所名称，必须与列表中的某项完全一致"
            )

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            return response if response in arenas else failsafe

        return Result(prompt, _callback, failsafe, determine_arenaResponse)

    def prompt_determine_object(self, describes, spatial, address):
        """执行 `Scratch` 的提示词`determine`对象操作。

        参数:
            describes: 需要批量解析或汇总的描述文本集合。
            spatial: 当前智能体或世界使用的空间配置与地址信息。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。

        返回:
            返回函数计算得到的结果。
        """
        objects = spatial.get_leaves(address)

        prompt = self.build_prompt(
            "determine_object",
            {
                "activity": describes[1],
                "objects": ", ".join(objects),
            },
        )

        failsafe = self._rng.choice(objects)

        class determine_objectResponse(BaseModel):
            res: str = Field(
                description="从给定列表中选出的最相关对象名称，必须与列表中的某项完全一致"
            )

        def _callback(response):
            # pattern = ["The most relevant object from the Objects is: <(.+?)>", "<(.+?)>"]
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            return response if response in objects else failsafe

        return Result(prompt, _callback, failsafe, determine_objectResponse)

    def prompt_decide_game_object_interaction(
        self, current_action, current_location, planned_path, interactions
    ):
        """执行 `Scratch` 的提示词`decide`仿真世界对象`interaction`操作。

        参数:
            current_action: 智能体当前正在执行的行为描述。
            current_location: 传入当前算法的`current``location`；其结构与有效范围由类型注解和调用协议共同限定。
            planned_path: `planned`对应的文件系统路径。
            interactions: 当前智能体可以观察或参与的对象交互候选集合。

        返回:
            返回函数计算得到的结果。
        """
        selection_keys = [
            str(item.get("selection_key"))
            for item in interactions
            if item.get("selection_key")
        ]
        interaction_text = "\n".join(
            "- {selection_key}｜{object_name}｜{description}｜距离 {distance_tiles} Tile".format(
                **item
            )
            for item in interactions
        )
        prompt = self.build_prompt(
            "decide_game_object_interaction",
            {
                "current_action": current_action,
                "current_location": current_location,
                "planned_path": " → ".join(
                    f"({coord[0]}, {coord[1]})" for coord in planned_path
                )
                or "无移动路径",
                "interactions": interaction_text or "无",
            },
        )
        failsafe = selection_keys[0] if selection_keys and planned_path else "NONE"

        class DecideGameObjectInteractionResponse(BaseModel):
            res: str = Field(description="一个可用 selection_key，或 NONE")

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            normalized = str(response).strip()
            return normalized if normalized in {*selection_keys, "NONE"} else failsafe

        return Result(
            prompt,
            _callback,
            failsafe,
            DecideGameObjectInteractionResponse,
        )

    def prompt_decide_game_object_response(
        self, current_action, object_name, request, response
    ):
        """执行 `Scratch` 的提示词`decide`仿真世界对象`response`操作。

        参数:
            current_action: 智能体当前正在执行的行为描述。
            object_name: 智能体准备交互的世界对象名称。
            request: 待执行、记录或发送到外部模型的请求对象。
            response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

        返回:
            返回函数计算得到的结果。
        """
        prompt = self.build_prompt(
            "decide_game_object_response",
            {
                "current_action": current_action,
                "object_name": object_name,
                "request": request,
                "response": response,
            },
        )
        folded = str(response).casefold()
        wait_markers = ("红灯", "等待", "禁止", "不可", "不要进入", "危险")
        continue_markers = ("绿灯", "可以通行", "允许通行", "安全通过")
        if any(marker in folded for marker in wait_markers):
            failsafe = "WAIT"
        elif any(marker in folded for marker in continue_markers):
            failsafe = "CONTINUE"
        else:
            failsafe = "WAIT"

        class DecideGameObjectResponse(BaseModel):
            res: Literal["WAIT", "CONTINUE"] = Field(
                description="Agent 基于外部信息作出的移动决策"
            )

        def _callback(value):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                value: 当前操作使用的`value`。

            返回:
                返回函数计算得到的结果。
            """
            normalized = str(value).strip().upper()
            return normalized if normalized in {"WAIT", "CONTINUE"} else failsafe

        return Result(prompt, _callback, failsafe, DecideGameObjectResponse)

    def prompt_describe_event(self, subject, describe, address, emoji=None):
        """执行 `Scratch` 的提示词`describe`事件操作。

        参数:
            subject: 事件三元组中的主体，通常是智能体或世界对象标识。
            describe: 事件、行为或记忆的人类可读描述文本。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。
            emoji: 用于回放或界面展示行为含义的单个表情符号。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        prompt = self.build_prompt(
            "describe_event",
            {
                "action": describe,
            },
        )

        e_describe = (
            describe.replace("(", "").replace(")", "").replace("<", "").replace(">", "")
        )
        if e_describe.startswith(subject + "此时"):
            e_describe = e_describe.replace(subject + "此时", "")
        failsafe = Event(
            subject, "此时", e_describe, describe=describe, address=address, emoji=emoji
        )

        class describe_eventResponse(BaseModel):
            res: List[Tuple[str, str, str]] = Field(
                description="动作的三元组列表，每项为 [主语, 谓语, 宾语]"
            )

        def _callback(response):
            # response已经是List[Tuple[str, str, str]]类型
            # 格式: [(主语, 谓语, 宾语), ...]
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            for subject, predicate, obj in response:
                # 验证三元组不为空
                if subject and predicate and obj:
                    return Event(
                        subject,
                        predicate,
                        obj,
                        describe=describe,
                        address=address,
                        emoji=emoji,
                    )
            return None

        return Result(prompt, _callback, failsafe, describe_eventResponse)

    def prompt_describe_object(self, obj, describe):
        """执行 `Scratch` 的提示词`describe`对象操作。

        参数:
            obj: 当前操作使用的`obj`。
            describe: 事件、行为或记忆的人类可读描述文本。

        返回:
            返回函数计算得到的结果。
        """
        prompt = self.build_prompt(
            "describe_object",
            {
                "object": obj,
                "agent": self.name,
                "action": describe,
            },
        )

        class DescribeObjectResponse(BaseModel):
            res: str = Field(
                description="物品的状态描述，不超过10个字的短句，不要包含物品名称"
            )

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            response = response.strip()
            # 移除 "物品名" 前缀
            if response.startswith(obj):
                response = response[len(obj) :].strip()
            return response or failsafe

        failsafe = "空闲"
        return Result(prompt, _callback, failsafe, DescribeObjectResponse)

    def prompt_decide_chat(self, agent, other, focus, chats):
        """执行 `Scratch` 的提示词`decide``chat`操作。

        参数:
            agent: 参与当前操作的智能体实例。
            other: 当前操作使用的`other`。
            focus: 当前反思、检索或对话需要重点关注的主题。
            chats: 按时间顺序排列的对话消息或说话人—内容二元组。

        返回:
            返回函数计算得到的结果。
        """

        def _status_des(a):
            """执行`status``des`的内部处理，供当前模块或类复用。

            参数:
                a: 参与当前比较、计算或合并操作的第一个值。

            返回:
                返回函数计算得到的结果。
            """
            event = a.get_event()
            if a.path:
                return f"{a.name} 正去往 {event.get_describe(False)}"
            return event.get_describe()

        context = "。".join([c.describe for c in focus["events"]])
        context += "\n" + "。".join([c.describe for c in focus["thoughts"]])
        date_str = self._clock.get_date("%Y-%m-%d %H:%M:%S")
        chat_history = ""
        if chats:
            chat_history = f" {agent.name} 和 {other.name} 上次在 {chats[0].create} 聊过关于 {chats[0].describe} 的话题"
        a_des, o_des = _status_des(agent), _status_des(other)

        prompt = self.build_prompt(
            "decide_chat",
            {
                "context": context,
                "date": date_str,
                "chat_history": chat_history,
                "agent_status": a_des,
                "another_status": o_des,
                "agent": agent.name,
                "another": other.name,
            },
        )

        class decide_chatResponse(BaseModel):
            res: bool = Field(
                description="是否主动发起对话，true 表示会主动对话，false 表示不会"
            )

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            if isinstance(response, bool):
                return response
            return str(response).strip().lower() in ("true", "yes", "是", "1")

        failsafe = False
        return Result(prompt, _callback, failsafe, decide_chatResponse)

    def prompt_decide_chat_terminate(self, agent, other, chats):
        """执行 `Scratch` 的提示词`decide``chat``terminate`操作。

        参数:
            agent: 参与当前操作的智能体实例。
            other: 当前操作使用的`other`。
            chats: 按时间顺序排列的对话消息或说话人—内容二元组。

        返回:
            返回函数计算得到的结果。
        """
        conversation = "\n".join(["{}: {}".format(n, u) for n, u in chats])
        conversation = conversation or "[对话尚未开始]"

        prompt = self.build_prompt(
            "decide_chat_terminate",
            {
                "conversation": conversation,
                "agent": agent.name,
                "another": other.name,
            },
        )

        class decide_chat_terminateResponse(BaseModel):
            res: bool = Field(
                description="对话是否已告一段落，true 表示对话结束，false 表示对话仍在继续"
            )

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            if isinstance(response, bool):
                return response
            return str(response).strip().lower() in ("true", "yes", "是", "1")

        failsafe = False
        return Result(prompt, _callback, failsafe, decide_chat_terminateResponse)

    def prompt_decide_wait(self, agent, other, focus):
        """执行 `Scratch` 的提示词`decide``wait`操作。

        参数:
            agent: 参与当前操作的智能体实例。
            other: 当前操作使用的`other`。
            focus: 当前反思、检索或对话需要重点关注的主题。

        返回:
            返回函数计算得到的结果。
        """
        example1 = self.build_prompt(
            "decide_wait_example",
            {
                "context": "简是丽兹的室友。2022-10-25 07:05，简和丽兹互相问候了早上好。",
                "date": "2022-10-25 07:09",
                "agent": "简",
                "another": "丽兹",
                "status": "简 正要去浴室",
                "another_status": "丽兹 已经在 使用浴室",
                "action": "使用浴室",
                "another_action": "使用浴室",
                "reason": "推理：简和丽兹都想用浴室。简和丽兹同时使用浴室会很奇怪。所以，既然丽兹已经在用浴室了，对简来说最好的选择就是等着用浴室。\n",
                "answer": "答案：<选项A>",
            },
        )
        example2 = self.build_prompt(
            "decide_wait_example",
            {
                "context": "山姆是莎拉的朋友。2022-10-24 23:00，山姆和莎拉就最喜欢的电影进行了交谈。",
                "date": "2022-10-25 12:40",
                "agent": "山姆",
                "another": "莎拉",
                "status": "山姆 正要去吃午饭",
                "another_status": "莎拉 已经在 洗衣服",
                "action": "吃午饭",
                "another_action": "洗衣服",
                "reason": "推理：山姆可能会在餐厅吃午饭。莎拉可能会去洗衣房洗衣服。由于山姆和莎拉需要使用不同的区域，他们的行为并不冲突。所以，由于山姆和莎拉将在不同的区域，山姆现在继续吃午饭。\n",
                "answer": "答案：<选项B>",
            },
        )

        def _status_des(a):
            """执行`status``des`的内部处理，供当前模块或类复用。

            参数:
                a: 参与当前比较、计算或合并操作的第一个值。

            返回:
                返回函数计算得到的结果。
            """
            event, loc = a.get_event(), ""
            if event.address:
                loc = " 在 {} 的 {}".format(event.address[-2], event.address[-1])
            if not a.path:
                return f"{a.name} 已经在 {event.get_describe(False)}{loc}"
            return f"{a.name} 正要去 {event.get_describe(False)}{loc}"

        context = ". ".join([c.describe for c in focus["events"]])
        context += "\n" + ". ".join([c.describe for c in focus["thoughts"]])

        task = self.build_prompt(
            "decide_wait_example",
            {
                "context": context,
                "date": self._clock.get_date("%Y-%m-%d %H:%M"),
                "agent": agent.name,
                "another": other.name,
                "status": _status_des(agent),
                "another_status": _status_des(other),
                "action": agent.get_event().get_describe(False),
                "another_action": other.get_event().get_describe(False),
                "reason": "",
                "answer": "",
            },
        )

        prompt = self.build_prompt(
            "decide_wait",
            {
                "examples_1": example1,
                "examples_2": example2,
                "task": task,
            },
        )

        class decide_waitResponse(BaseModel):
            res: str = Field(
                description="选择的选项，'A' 表示等待，'B' 表示继续当前行动"
            )

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            return "A" in response

        failsafe = False
        return Result(prompt, _callback, failsafe, decide_waitResponse)

    def prompt_summarize_relation(self, agent, other_name):
        """执行 `Scratch` 的提示词`summarize``relation`操作。

        参数:
            agent: 参与当前操作的智能体实例。
            other_name: 传入当前算法的`other``name`；其结构与有效范围由类型注解和调用协议共同限定。

        返回:
            返回函数计算得到的结果。
        """
        nodes = agent.associate.retrieve_focus([other_name], 50)

        prompt = self.build_prompt(
            "summarize_relation",
            {
                "context": "\n".join(
                    ["{}. {}".format(idx, n.describe) for idx, n in enumerate(nodes)]
                ),
                "agent": agent.name,
                "another": other_name,
            },
        )
        failsafe = agent.name + " 正在看着 " + other_name

        class summarize_relationResponse(BaseModel):
            res: str = Field(description="一句话描述两人之间的关系，以第三人称表述")

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            return response.strip() or failsafe

        return Result(prompt, _callback, failsafe, summarize_relationResponse)

    def prompt_generate_chat(self, agent, other, relation, chats):
        """执行 `Scratch` 的提示词`generate``chat`操作。

        参数:
            agent: 参与当前操作的智能体实例。
            other: 当前操作使用的`other`。
            relation: 两个智能体之间的关系描述或关系边记录。
            chats: 按时间顺序排列的对话消息或说话人—内容二元组。

        返回:
            返回函数计算得到的结果。
        """
        focus = [relation, other.get_event().get_describe()]
        if len(chats) > 4:
            focus.append("; ".join("{}: {}".format(n, t) for n, t in chats[-4:]))
        nodes = agent.associate.retrieve_focus(focus, 15)
        memory = "\n- " + "\n- ".join([n.describe for n in nodes])
        chat_nodes = agent.associate.retrieve_chats(other.name)
        pass_context = ""
        for n in chat_nodes:
            delta = self._clock.get_delta(n.create)
            if delta > 480:
                continue
            pass_context += f"{delta} 分钟前，{agent.name} 和 {other.name} 进行过对话。{n.describe}\n"

        address = agent.get_tile().get_address()
        if len(pass_context) > 0:
            prev_context = f'\n背景：\n"""\n{pass_context}"""\n\n'
        else:
            prev_context = ""
        curr_context = f"{agent.name} {agent.get_event().get_describe(False)} 时，看到 {other.name} {other.get_event().get_describe(False)}。"

        conversation = "\n".join(["{}: {}".format(n, u) for n, u in chats])
        conversation = conversation or "[对话尚未开始]"

        prompt = self.build_prompt(
            "generate_chat",
            {
                "agent": agent.name,
                "base_desc": self._base_desc(),
                "memory": memory,
                "address": f"{address[-2]}，{address[-1]}",
                "current_time": self._clock.get_date("%H:%M"),
                "previous_context": prev_context,
                "current_context": curr_context,
                "another": other.name,
                "conversation": conversation,
            },
        )

        class generate_chat(BaseModel):
            res: str = Field(description="角色说出的对话内容，1到3句话")

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            response = response.strip()
            # 移除 "名字：" 前缀
            if response.startswith(agent.name + "：") or response.startswith(
                agent.name + ":"
            ):
                response = response[len(agent.name) + 1 :].strip()
            return response or failsafe

        failsafe = "嗯"
        return Result(prompt, _callback, failsafe, generate_chat)

    def prompt_generate_chat_check_repeat(self, agent, chats, content):
        """执行 `Scratch` 的提示词`generate``chat``check``repeat`操作。

        参数:
            agent: 参与当前操作的智能体实例。
            chats: 按时间顺序排列的对话消息或说话人—内容二元组。
            content: 待解析、写入、哈希或发送给下游组件的正文内容。

        返回:
            返回函数计算得到的结果。
        """
        conversation = "\n".join(["{}: {}".format(n, u) for n, u in chats])
        conversation = conversation or "[对话尚未开始]"

        class generate_chat_check_repeatResponse(BaseModel):
            res: bool = Field(
                description="新对话内容是否与历史记录重复，true 表示重复，false 表示不重复"
            )

        prompt = self.build_prompt(
            "generate_chat_check_repeat",
            {
                "conversation": conversation,
                "content": f"{agent.name}: {content}",
                "agent": agent.name,
            },
        )

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            if isinstance(response, bool):
                return response
            return str(response).strip().lower() in ("true", "yes", "是", "1")

        failsafe = False
        return Result(prompt, _callback, failsafe, generate_chat_check_repeatResponse)

    def prompt_summarize_chats(self, chats):
        """执行 `Scratch` 的提示词`summarize``chats`操作。

        参数:
            chats: 按时间顺序排列的对话消息或说话人—内容二元组。

        返回:
            返回函数计算得到的结果。
        """
        conversation = "\n".join(["{}: {}".format(n, u) for n, u in chats])

        prompt = self.build_prompt(
            "summarize_chats",
            {
                "conversation": conversation,
            },
        )

        class summarize_chatsResponse(BaseModel):
            res: str = Field(description="对话内容的简短摘要，一句话概括对话主题")

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            return response.strip()

        if len(chats) > 1:
            failsafe = "{} 和 {} 之间的普通对话".format(chats[0][0], chats[1][0])
        else:
            failsafe = "{} 说的话没有得到回应".format(chats[0][0])

        return Result(prompt, _callback, failsafe, summarize_chatsResponse)

    def prompt_reflect_focus(self, nodes, topk):
        """执行 `Scratch` 的提示词`reflect``focus`操作。

        参数:
            nodes: 需要批量遍历、校验或转换的节点集合。
            topk: 排序后最多保留的候选项数量。

        返回:
            返回函数计算得到的结果。
        """
        prompt = self.build_prompt(
            "reflect_focus",
            {
                "reference": "\n".join(
                    ["{}. {}".format(idx, n.describe) for idx, n in enumerate(nodes)]
                ),
                "number": topk,
            },
        )

        class reflect_focusResponse(BaseModel):
            res: List[str] = Field(description="需要深入思考的问题列表，每项为一个问题")

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            assert len(response) >= 1, "reflect_focus: empty list"
            return response

        failsafe = [
            "{} 是谁？".format(self.name),
            "{} 住在哪里？".format(self.name),
            "{} 今天要做什么？".format(self.name),
        ]
        return Result(prompt, _callback, failsafe, reflect_focusResponse)

    def prompt_reflect_insights(self, nodes, topk):
        """执行 `Scratch` 的提示词`reflect``insights`操作。

        参数:
            nodes: 需要批量遍历、校验或转换的节点集合。
            topk: 排序后最多保留的候选项数量。

        返回:
            返回函数计算得到的结果。
        """
        prompt = self.build_prompt(
            "reflect_insights",
            {
                "reference": "\n".join(
                    ["{}. {}".format(idx, n.describe) for idx, n in enumerate(nodes)]
                ),
                "number": topk,
            },
        )

        class reflect_insightsResponse(BaseModel):
            res: List[Tuple[str, str]] = Field(
                description="洞察列表，每项为 [洞察内容, 相关节点索引的逗号分隔字符串如'1,2,3'] 的元组"
            )

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            insights = []
            for insight, node_ids_str in response:
                # 将字符串"1,2,3"转换为节点ID列表
                indices = [int(i.strip()) for i in node_ids_str.split(",")]
                node_ids = [nodes[i].node_id for i in indices if i < len(nodes)]
                insights.append([insight.strip(), node_ids])
            return insights

        failsafe = [
            [
                "{} 在考虑下一步该做什么".format(self.name),
                [nodes[0].node_id],
            ]
        ]
        return Result(prompt, _callback, failsafe, reflect_insightsResponse)

    def prompt_reflect_chat_planing(self, chats):
        """执行 `Scratch` 的提示词`reflect``chat``planing`操作。

        参数:
            chats: 按时间顺序排列的对话消息或说话人—内容二元组。

        返回:
            返回函数计算得到的结果。
        """
        all_chats = "\n".join(["{}: {}".format(n, c) for n, c in chats])

        prompt = self.build_prompt(
            "reflect_chat_planing",
            {
                "conversation": all_chats,
                "agent": self.name,
            },
        )

        class reflect_chat_planingResponse(BaseModel):
            res: str = Field(
                description="从对话中提取的对角色计划的影响或启发，一句话描述"
            )

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            return response.strip() or failsafe

        failsafe = f"{self.name} 进行了一次对话"
        return Result(prompt, _callback, failsafe, reflect_chat_planingResponse)

    def prompt_reflect_chat_memory(self, chats):
        """执行 `Scratch` 的提示词`reflect``chat`记忆操作。

        参数:
            chats: 按时间顺序排列的对话消息或说话人—内容二元组。

        返回:
            返回函数计算得到的结果。
        """
        all_chats = "\n".join(["{}: {}".format(n, c) for n, c in chats])

        prompt = self.build_prompt(
            "reflect_chat_memory",
            {
                "conversation": all_chats,
                "agent": self.name,
            },
        )

        class reflect_chat_memoryResponse(BaseModel):
            res: str = Field(description="从对话中提取的值得记忆的内容，一句话描述")

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            return response.strip() or failsafe

        failsafe = f"{self.name} 进行了一次对话"
        return Result(prompt, _callback, failsafe, reflect_chat_memoryResponse)

    def prompt_retrieve_plan(self, nodes):
        """执行 `Scratch` 的提示词`retrieve``plan`操作。

        参数:
            nodes: 需要批量遍历、校验或转换的节点集合。

        返回:
            返回函数计算得到的结果。
        """
        statements = [
            n.create.strftime("%Y-%m-%d %H:%M") + ": " + n.describe for n in nodes
        ]

        prompt = self.build_prompt(
            "retrieve_plan",
            {
                "description": "\n".join(statements),
                "agent": self.name,
                "date": self._clock.get_date("%Y-%m-%d"),
            },
        )

        class retrieve_planResponse(BaseModel):
            res: List[str] = Field(
                description="从记忆中检索出的相关计划列表，每项为一条计划描述"
            )

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            assert len(response) >= 1, "retrieve_plan: empty list"
            return response

        failsafe = [r.describe for r in self._rng.choices(nodes, k=5)]
        return Result(prompt, _callback, failsafe, retrieve_planResponse)

    def prompt_retrieve_thought(self, nodes):
        """执行 `Scratch` 的提示词`retrieve``thought`操作。

        参数:
            nodes: 需要批量遍历、校验或转换的节点集合。

        返回:
            返回函数计算得到的结果。
        """
        statements = [
            n.create.strftime("%Y-%m-%d %H:%M") + "：" + n.describe for n in nodes
        ]

        prompt = self.build_prompt(
            "retrieve_thought",
            {
                "description": "\n".join(statements),
                "agent": self.name,
            },
        )

        class retrieve_thoughtResponse(BaseModel):
            res: str = Field(description="从记忆中检索出的相关思考内容，一句话总结")

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            return response.strip() or failsafe

        failsafe = "{} 应该遵循昨天的日程".format(self.name)
        return Result(prompt, _callback, failsafe, retrieve_thoughtResponse)

    def prompt_retrieve_currently(self, plan_note, thought_note):
        """执行 `Scratch` 的提示词`retrieve``currently`操作。

        参数:
            plan_note: 传入当前算法的`plan``note`；其结构与有效范围由类型注解和调用协议共同限定。
            thought_note: 传入当前算法的`thought``note`；其结构与有效范围由类型注解和调用协议共同限定。

        返回:
            返回函数计算得到的结果。
        """
        time_stamp = (self._clock.get_date() - datetime.timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )

        prompt = self.build_prompt(
            "retrieve_currently",
            {
                "agent": self.name,
                "time": time_stamp,
                "currently": self.currently,
                "plan": ". ".join(plan_note),
                "thought": thought_note,
                "current_time": self._clock.get_date("%Y-%m-%d"),
            },
        )

        class retrieve_currentlyResponse(BaseModel):
            res: str = Field(description="角色当前状态的更新描述，基于过去的计划和思考")

        def _callback(response):
            """执行`callback`的内部处理，供当前模块或类复用。

            参数:
                response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

            返回:
                返回函数计算得到的结果。
            """
            return response.strip() or failsafe

        failsafe = self.currently

        return Result(prompt, _callback, failsafe, retrieve_currentlyResponse)
