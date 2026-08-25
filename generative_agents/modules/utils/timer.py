"""generative_agents.utils.timer"""

import datetime


def as_utc(value, *, naive_timezone=datetime.timezone.utc):
    """在不依赖宿主机时区的前提下，把仿真时间规范化为 UTC。

    参数:
        value: 当前操作使用的`value`。
        naive_timezone: 解释无时区时间时采用的显式默认时区。 默认值：`datetime.timezone.utc`。

    返回:
        返回函数计算得到的结果。

    异常:
        TypeError: 当参数类型不符合接口约定时抛出。
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。

    说明:
        无时区时间按项目约定解释，不读取操作系统本地时区，从而保证不同部署环境结果一致。
    """

    if not isinstance(value, datetime.datetime):
        raise TypeError("simulation time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        if naive_timezone is None:
            raise ValueError("naive simulation time requires an explicit timezone")
        value = value.replace(tzinfo=naive_timezone)
    return value.astimezone(datetime.timezone.utc)


def to_date(
    date_str,
    date_format="%Y%m%d-%H:%M:%S",
    *,
    naive_timezone=datetime.timezone.utc,
):
    """执行 的`to``date`操作。

    参数:
        date_str: 需要解析为仿真日期或时间的文本。
        date_format: 日期时间解析或输出采用的格式字符串。 默认值：`'%Y%m%d-%H:%M:%S'`。
        naive_timezone: 解释无时区时间时采用的显式默认时区。 默认值：`datetime.timezone.utc`。

    返回:
        返回函数计算得到的结果。
    """
    if isinstance(date_str, datetime.datetime):
        return as_utc(date_str, naive_timezone=naive_timezone)
    if date_format == "%H:%M" and date_str.startswith("24:"):
        date_str = date_str.replace("24:", "0:")
    try:
        parsed = datetime.datetime.strptime(date_str, date_format)
    except ValueError:
        if date_format != "%Y%m%d-%H:%M:%S":
            raise
        parsed = datetime.datetime.fromisoformat(date_str)
    return as_utc(parsed, naive_timezone=naive_timezone)


def daily_duration(date, mode="minute"):
    """执行 的`daily``duration`操作。

    参数:
        date: 传入当前算法的`date`；其结构与有效范围由类型注解和调用协议共同限定。
        mode: 选择当前操作行为的模式判别值；允许值由类型注解或调用协议限定。 默认值：`'minute'`。

    返回:
        返回函数计算得到的结果。
    """
    duration = date.hour % 24
    if mode == "hour":
        return duration
    duration = duration * 60 + date.minute
    if mode == "minute":
        return duration
    return datetime.timedelta(minutes=duration)


class Timer:
    def __init__(self, start=None):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            start: 处理区间的起始位置或起始时间。 默认值：`None`。

        返回:
            无返回值。
        """
        self._mode = "on_time"
        if start:
            d_format = "%Y%m%d-%H:%M" if "-" in start else "%H:%M"
            self._offset = to_date(start, d_format)
        else:
            self._offset = datetime.datetime.now(datetime.timezone.utc)

    def forward(self, offset):
        """执行 `Timer` 的`forward`操作。

        参数:
            offset: 从结果集或字节流起点跳过的数量。

        返回:
            无返回值。
        """
        self._offset += datetime.timedelta(minutes=offset)

    def get_date(self, date_format=""):
        """获取`date`。

        参数:
            date_format: 日期时间解析或输出采用的格式字符串。 默认值：`''`。

        返回:
            返回函数计算得到的结果。
        """
        date = self._offset
        if date_format:
            return date.strftime(date_format)
        return date

    def get_delta(self, start, end=None, mode="minute"):
        """获取`delta`。

        参数:
            start: 处理区间的起始位置或起始时间。
            end: 处理区间的结束位置或结束时间；是否包含由当前接口约定。 默认值：`None`。
            mode: 选择当前操作行为的模式判别值；允许值由类型注解或调用协议限定。 默认值：`'minute'`。

        返回:
            返回函数计算得到的结果。
        """
        end = end or self.get_date()
        seconds = (end - start).total_seconds()
        if mode == "second":
            return seconds
        if mode == "minute":
            return round(seconds / 60)
        if mode == "hour":
            return round(seconds / 3600)
        return end - start

    def daily_format(self):
        """执行 `Timer` 的`daily``format`操作。

        返回:
            返回函数计算得到的结果。
        """
        return self.get_date("%A %B %d")

    def get_weekday(self, t):
        """获取`weekday`。

        参数:
            t: 计时、插值或数值计算使用的时间参数。

        返回:
            返回函数计算得到的结果。
        """
        weekday_dict = {
            0: "星期一",
            1: "星期二",
            2: "星期三",
            3: "星期四",
            4: "星期五",
            5: "星期六",
            6: "星期日",
        }
        weekday = weekday_dict[t.weekday()]
        return weekday

    def daily_format_cn(self):
        """执行 `Timer` 的`daily``format``cn`操作。

        返回:
            返回函数计算得到的结果。
        """
        weekday = self.get_weekday(self.get_date())
        date = self.get_date("%Y年%m月%d日")
        return f"{date}（{weekday}）"

    def time_format_cn(self, t):
        """执行 `Timer` 的`time``format``cn`操作。

        参数:
            t: 计时、插值或数值计算使用的时间参数。

        返回:
            返回函数计算得到的结果。
        """
        weekday = self.get_weekday(t)
        date = t.strftime("%Y年%m月%d日")
        time = t.strftime("%H:%M")
        return f"{date}（{weekday}）{time}"

    def daily_duration(self, mode="minute"):
        """执行 `Timer` 的`daily``duration`操作。

        参数:
            mode: 选择当前操作行为的模式判别值；允许值由类型注解或调用协议限定。 默认值：`'minute'`。

        返回:
            返回函数计算得到的结果。
        """
        return daily_duration(self.get_date(), mode)

    def daily_time(self, duration):
        """执行 `Timer` 的`daily``time`操作。

        参数:
            duration: 行为、对话或日程项占用的虚拟时间长度。

        返回:
            返回函数计算得到的结果。
        """
        base = self.get_date().replace(hour=0, minute=0, second=0, microsecond=0)
        return base + datetime.timedelta(minutes=duration)

    @property
    def mode(self):
        """执行 `Timer` 的`mode`操作。

        返回:
            返回函数计算得到的结果。
        """
        return self._mode
