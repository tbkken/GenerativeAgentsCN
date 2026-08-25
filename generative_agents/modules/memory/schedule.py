"""generative_agents.memory.schedule"""

import copy

from generative_agents.modules import utils


class Schedule:
    def __init__(
        self, create=None, daily_schedule=None, diversity=5, max_try=5, clock=None
    ):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            create: 记忆、事件或记录的创建时间；为空时使用当前仿真时间。 默认值：`None`。
            daily_schedule: 智能体当天按时间顺序排列的日程项。 默认值：`None`。
            diversity: 传入当前算法的`diversity`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`5`。
            max_try: `try`允许的最大值。 默认值：`5`。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。 默认值：`None`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if clock is None:
            raise ValueError("Schedule requires an injected clock")
        self._clock = clock
        if create:
            self.create = utils.to_date(create, naive_timezone=clock.get_date().tzinfo)
        else:
            self.create = None
        self.daily_schedule = daily_schedule or []
        self.diversity = diversity
        self.max_try = max_try

    def abstract(self):
        """执行 `Schedule` 的`abstract`操作。

        返回:
            返回函数计算得到的结果。
        """

        def _to_stamp(plan):
            """执行`to``stamp`的内部处理，供当前模块或类复用。

            参数:
                plan: 智能体当前计划或等待执行的计划片段。

            返回:
                返回函数计算得到的结果。
            """
            start, end = self.plan_stamps(plan, time_format="%H:%M")
            return "{}~{}".format(start, end)

        des = {}
        for plan in self.daily_schedule:
            stamp = _to_stamp(plan)
            if plan.get("decompose"):
                s_info = {_to_stamp(p): p["describe"] for p in plan["decompose"]}
                des[stamp + ": " + plan["describe"]] = s_info
            else:
                des[stamp] = plan["describe"]
        return des

    def __str__(self):
        """执行`str`的内部处理，供当前模块或类复用。

        返回:
            返回函数计算得到的结果。
        """
        return utils.dump_dict(self.abstract())

    def add_plan(self, describe, duration, decompose=None):
        """执行 `Schedule` 的`add``plan`操作。

        参数:
            describe: 事件、行为或记忆的人类可读描述文本。
            duration: 行为、对话或日程项占用的虚拟时间长度。
            decompose: 传入当前算法的`decompose`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        if self.daily_schedule:
            last_plan = self.daily_schedule[-1]
            start = last_plan["start"] + last_plan["duration"]
        else:
            start = 0
        self.daily_schedule.append(
            {
                "idx": len(self.daily_schedule),
                "describe": describe,
                "start": start,
                "duration": duration,
                "decompose": decompose or {},
            }
        )
        return self.daily_schedule[-1]

    def current_plan(self):
        """执行 `Schedule` 的`current``plan`操作。

        返回:
            返回函数计算得到的结果。
        """
        total_minute = self._clock.daily_duration()
        for plan in self.daily_schedule:
            if self.plan_stamps(plan)[1] <= total_minute:
                continue
            for de_plan in plan.get("decompose", []):
                if self.plan_stamps(de_plan)[1] <= total_minute:
                    continue
                return plan, de_plan
            return plan, plan
        last_plan = self.daily_schedule[-1]
        return last_plan, last_plan

    def insert_interruption(self, describe, start, duration):
        """把短时突发事件插入当前日程，并保持后续时间段连续。

        参数:
            describe: 事件、行为或记忆的人类可读描述文本。
            start: 处理区间的起始位置或起始时间。
            duration: 行为、对话或日程项占用的虚拟时间长度。

        返回:
            返回函数计算得到的结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。

        说明:
            插入后必须保持时间片有序、连续且总时长守恒；修改拆分算法时需要同时验证这三个不变量。
        """

        if duration < 1:
            raise ValueError("interruption duration must be positive")
        plan, _ = self.current_plan()
        plan_start, plan_end = self.plan_stamps(plan)
        observed_start = utils.daily_duration(start)
        begin = max(plan_start, observed_start)
        finish = min(plan_end, begin + duration)
        if finish <= begin:
            return False

        source = list(plan.get("decompose") or ())
        if not source:
            source = [
                {
                    "idx": 0,
                    "describe": plan["describe"],
                    "start": plan_start,
                    "duration": plan_end - plan_start,
                }
            ]

        interruption = {
            "describe": describe,
            "start": begin,
            "duration": finish - begin,
        }
        revised = []
        inserted = False
        for item in sorted(source, key=lambda value: value["start"]):
            item = copy.deepcopy(item)
            item_start, item_end = self.plan_stamps(item)
            if item_end <= begin or item_start >= finish:
                if not inserted and item_start >= finish:
                    revised.append(dict(interruption))
                    inserted = True
                revised.append(item)
                continue
            if item_start < begin:
                before = copy.deepcopy(item)
                before["duration"] = begin - item_start
                revised.append(before)
            if not inserted:
                revised.append(dict(interruption))
                inserted = True
            if item_end > finish:
                after = copy.deepcopy(item)
                after["start"] = finish
                after["duration"] = item_end - finish
                revised.append(after)
        if not inserted:
            revised.append(dict(interruption))
        revised.sort(key=lambda value: value["start"])
        for index, item in enumerate(revised):
            item["idx"] = index
        plan["decompose"] = revised
        return True

    def plan_stamps(self, plan, time_format=None):
        """执行 `Schedule` 的`plan``stamps`操作。

        参数:
            plan: 智能体当前计划或等待执行的计划片段。
            time_format: 传入当前算法的`time``format`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """

        def _to_date(minutes):
            """执行`to``date`的内部处理，供当前模块或类复用。

            参数:
                minutes: 需要推进、等待或分配的虚拟分钟数。

            返回:
                返回函数计算得到的结果。
            """
            return self._clock.daily_time(minutes).strftime(time_format)

        start, end = plan["start"], plan["start"] + plan["duration"]
        if time_format:
            start, end = _to_date(start), _to_date(end)
        return start, end

    def decompose(self, plan):
        """执行 `Schedule` 的`decompose`操作。

        参数:
            plan: 智能体当前计划或等待执行的计划片段。

        返回:
            返回函数计算得到的结果。
        """
        d_plan = plan.get("decompose", {})
        if len(d_plan) > 0:
            return False
        describe = plan["describe"]
        if "sleep" not in describe and "bed" not in describe:
            return True
        if "睡" not in describe and "床" not in describe:
            return True
        if "sleeping" in describe or "asleep" in describe or "in bed" in describe:
            return False
        if "睡" in describe or "床" in describe:
            return False
        if "sleep" in describe or "bed" in describe:
            return plan["duration"] <= 60
        if "睡" in describe or "床" in describe:
            return plan["duration"] <= 60
        return True

    def scheduled(self):
        """执行 `Schedule` 的`scheduled`操作。

        返回:
            返回函数计算得到的结果。
        """
        if not self.daily_schedule:
            return False
        current = self._clock.get_date()
        created = self.create
        if created.tzinfo is not None and current.tzinfo is not None:
            created = created.astimezone(current.tzinfo)
        return self._clock.daily_format() == created.strftime("%A %B %d")

    def to_dict(self):
        """执行 `Schedule` 的`to``dict`操作。

        返回:
            返回函数计算得到的结果。
        """
        return {
            "create": (
                self.create.strftime("%Y%m%d-%H:%M:%S") if self.create else None
            ),
            "daily_schedule": self.daily_schedule,
        }
