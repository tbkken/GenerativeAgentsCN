"""generative_agents.utils.log"""

import os
import logging
from typing import Union

from .arguments import dump_dict


class IOLogger(object):
    """IO Logger for MSC"""

    def __init__(self, level=logging.INFO, color=False, clock=None):
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
        if not self._color:
            return print
        if color not in self._printers:
            return print
        return self._printers.get(color, print)

    def _prefix(self):
        if self._clock is None:
            return "<system>"
        return "<{}({})>".format(
            self._clock.get_date("%Y%m%d-%H:%M:%S"), self._clock.mode
        )

    def info(self, msg):
        if self._level <= logging.INFO:
            self._get_printer("green")("[INFO]{}: {}".format(self._prefix(), msg))

    def debug(self, msg):
        if self._level <= logging.DEBUG:
            self._get_printer("green")("[DEBUG]{}: {}".format(self._prefix(), msg))

    def warning(self, msg):
        if self._level <= logging.WARN:
            self._get_printer("yellow")("[WARNING]{}: {}".format(self._prefix(), msg))

    def error(self, msg):
        self._get_printer("red")("[ERROR]{}: {}".format(self._prefix(), msg))


def create_io_logger(level: Union[str, int] = logging.INFO, *, clock=None):
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
    """Create file logger

    Parameters
    ----------
    level: logging level
        The logging level.
    path: str
        The file path.

    Returns
    -------
    logger: logging.Logger
        The logger.
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
    return "{0}{1}{0}".format(symbol * 10, title.center(width - 20))


def block_msg(title, msg, symbol="-", width=80):
    if isinstance(msg, dict):
        msg = dump_dict(msg)
    return "\n{}\n{}".format(split_line(title, symbol, width), msg)
