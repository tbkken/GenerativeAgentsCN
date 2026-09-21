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
