"""generative_agents.memory.action"""

import datetime

from generative_agents.modules import utils
from .event import Event


class Action:
    """智能体当前动作的描述、持续时间、目标地址和对象状态。"""

    def __init__(
        self,
        event,
        obj_event=None,
        start=None,
        duration=0,
        clock=None,
    ):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            event: 当前感知、处理或写入结果账本的领域事件。
            obj_event: 世界对象因当前行为产生的状态事件。 默认值：`None`。
            start: 处理区间的起始位置或起始时间。 默认值：`None`。
            duration: 行为、对话或日程项占用的虚拟时间长度。 默认值：`0`。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。 默认值：`None`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        self.event = event
        self.obj_event = obj_event
        if clock is None and start is None:
            raise ValueError("Action requires an injected clock when start is omitted")
        self._clock = clock
        self.start = start or clock.get_date()
        self.duration = duration
        self.end = self.start + datetime.timedelta(minutes=self.duration)

    def abstract(self):
        """执行 `Action` 的`abstract`操作。

        返回:
            返回函数计算得到的结果。
        """
        status = "{} [{}~{}]".format(
            "已完成" if self.finished() else "进行中",
            self.start.strftime("%Y%m%d-%H:%M"),
            self.end.strftime("%Y%m%d-%H:%M"),
        )
        info = {"status": status, "event": str(self.event)}
        if self.obj_event:
            info["object"] = str(self.obj_event)
        return info

    def __str__(self):
        """执行`str`的内部处理，供当前模块或类复用。

        返回:
            返回函数计算得到的结果。
        """
        return utils.dump_dict(self.abstract())

    def finished(self):
        """执行 `Action` 的`finished`操作。

        返回:
            返回函数计算得到的结果。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        if not self.duration:
            return True
        if not self.event.address:
            return True
        if self._clock is None:
            raise RuntimeError("Action has no clock for completion checks")
        return self._clock.get_date() > self.end

    def to_dict(self):
        """执行 `Action` 的`to``dict`操作。

        返回:
            返回函数计算得到的结果。
        """
        return {
            "event": self.event.to_dict(),
            "obj_event": self.obj_event.to_dict() if self.obj_event else None,
            "start": self.start.strftime("%Y%m%d-%H:%M:%S"),
            "duration": self.duration,
        }

    @classmethod
    def from_dict(cls, config, *, clock):
        """执行 `Action` 的`from``dict`操作。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。

        返回:
            返回函数计算得到的结果。
        """
        values = dict(config)
        values["event"] = Event.from_dict(values["event"])
        if values.get("obj_event"):
            values["obj_event"] = Event.from_dict(values["obj_event"])
        values["start"] = utils.to_date(
            values["start"], naive_timezone=clock.get_date().tzinfo
        )
        values["clock"] = clock
        return cls(**values)
