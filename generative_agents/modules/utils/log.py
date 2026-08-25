"""generative_agents.utils.log"""

import os
import logging
from typing import Union

from .arguments import dump_dict


class IOLogger(object):
    """IO Logger for MSC"""

    def __init__(self, level=logging.INFO, color=False, clock=None):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            level: 日志级别、树层级或重要性等级。 默认值：`logging.INFO`。
            color: 渲染地图、图例或界面元素时使用的颜色值。 默认值：`False`。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。 默认值：`None`。

        返回:
            无返回值。
        """
        self._printers = {
            "red": (lambda m: print("\033[91m {}\033[00m".format(m))),
            "green": (lambda m: print("\033[92m {}\033[00m".format(m))),
            "yellow": (lambda m: print("\033[93m {}\033[00m".format(m))),
            "purple": (lambda m: print("\033[95m {}\033[00m".format(m))),
            "cyan": (lambda m: print("\033[96m {}\033[00m".format(m))),
            "gray": (lambda m: print("\033[97m {}\033[00m".format(m))),
            "black": (lambda m: print("\033[98m {}\033[00m".format(m))),
        }
        self._level = level
        self._color = color
        self._clock = clock

    def _get_printer(self, color):
        """获取`printer`。

        参数:
            color: 渲染地图、图例或界面元素时使用的颜色值。

        返回:
            返回函数计算得到的结果。
        """
        if not self._color:
            return print
        if color not in self._printers:
            return print
        return self._printers.get(color, print)

    def _prefix(self):
        """执行`prefix`的内部处理，供当前模块或类复用。

        返回:
            返回函数计算得到的结果。
        """
        if self._clock is None:
            return "<system>"
        return "<{}({})>".format(
            self._clock.get_date("%Y%m%d-%H:%M:%S"), self._clock.mode
        )

    def info(self, msg):
        """执行 `IOLogger` 的`info`操作。

        参数:
            msg: 待解析或处理的消息对象。

        返回:
            无返回值。
        """
        if self._level <= logging.INFO:
            self._get_printer("green")("[INFO]{}: {}".format(self._prefix(), msg))

    def debug(self, msg):
        """执行 `IOLogger` 的`debug`操作。

        参数:
            msg: 待解析或处理的消息对象。

        返回:
            无返回值。
        """
        if self._level <= logging.DEBUG:
            self._get_printer("green")("[DEBUG]{}: {}".format(self._prefix(), msg))

    def warning(self, msg):
        """执行 `IOLogger` 的`warning`操作。

        参数:
            msg: 待解析或处理的消息对象。

        返回:
            无返回值。
        """
        if self._level <= logging.WARN:
            self._get_printer("yellow")("[WARNING]{}: {}".format(self._prefix(), msg))

    def error(self, msg):
        """执行 `IOLogger` 的`error`操作。

        参数:
            msg: 待解析或处理的消息对象。

        返回:
            无返回值。
        """
        self._get_printer("red")("[ERROR]{}: {}".format(self._prefix(), msg))


def create_io_logger(level: Union[str, int] = logging.INFO, *, clock=None):
    """创建`io``logger`。

    参数:
        level: 日志级别、树层级或重要性等级。 类型：`Union[str, int]`。 默认值：`logging.INFO`。
        clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。 默认值：`None`。

    返回:
        返回函数计算得到的结果。

    异常:
        Exception: 当底层操作报告该异常条件时抛出。
    """
    if isinstance(level, str):
        if level.startswith("debug"):
            level = logging.DEBUG
        elif level == "info":
            level = logging.INFO
        elif level == "warn":
            level = logging.WARN
        elif level == "error":
            level = logging.ERROR
        elif level == "critical":
            level = logging.CRITICAL
        else:
            raise Exception("Unexcept verbose {}, should be debug| info| warn")
    return IOLogger(level, clock=clock)


def create_file_logger(
    path: str,
    level: Union[str, int] = logging.INFO,
    *,
    run_id: str = "legacy",
    attempt_no: int = 1,
) -> logging.Logger:
    """创建`file``logger`。

    参数:
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`str`。
        level: 日志级别、树层级或重要性等级。 类型：`Union[str, int]`。 默认值：`logging.INFO`。
        run_id: 仿真运行的唯一标识。 类型：`str`。 默认值：`'legacy'`。
        attempt_no: 同一运行内从 1 开始递增的执行尝试序号。 类型：`int`。 默认值：`1`。

    返回:
        返回 `logging.Logger` 类型的处理结果。

    异常:
        Exception: 当底层操作报告该异常条件时抛出。
    """

    if isinstance(level, str):
        if level.startswith("debug"):
            level = logging.DEBUG
        elif level == "info":
            level = logging.INFO
        elif level == "warn":
            level = logging.WARN
        elif level == "error":
            level = logging.ERROR
        elif level == "critical":
            level = logging.CRITICAL
        else:
            raise Exception("Unexcept verbose {}, should be debug| info| warn")

    log_name = f"ga.run.{run_id}.{attempt_no}"
    logger = logging.getLogger(log_name)
    logger.setLevel(level)
    for handler in tuple(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    logger.propagate = False
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(filename)s[ln:%(lineno)d]<%(levelname)s> %(message)s"
    )
    handlers = [
        logging.FileHandler(path, mode="a", encoding="utf-8", delay=False),
        logging.StreamHandler(),
    ]
    for handler in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def split_line(title, symbol="-", width=80):
    """执行 的`split``line`操作。

    参数:
        title: 面向用户展示的标题文本。
        symbol: 用于日志、终端或界面展示状态的短符号。 默认值：`'-'`。
        width: 地图、图像或矩形区域的宽度。 默认值：`80`。

    返回:
        返回函数计算得到的结果。
    """
    return "{0}{1}{0}".format(symbol * 10, title.center(width - 20))


def block_msg(title, msg, symbol="-", width=80):
    """执行 的`block``msg`操作。

    参数:
        title: 面向用户展示的标题文本。
        msg: 待解析或处理的消息对象。
        symbol: 用于日志、终端或界面展示状态的短符号。 默认值：`'-'`。
        width: 地图、图像或矩形区域的宽度。 默认值：`80`。

    返回:
        返回函数计算得到的结果。
    """
    if isinstance(msg, dict):
        msg = dump_dict(msg)
    return "\n{}\n{}".format(split_line(title, symbol, width), msg)
