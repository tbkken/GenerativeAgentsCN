"""generative_agents.memory.action"""

import datetime

from generative_agents.modules import utils
from .event import Event


class Action:
    def __init__(
        self,
        event,
        obj_event=None,
        start=None,
        duration=0,
        clock=None,
    ):
        self.event = event
        self.obj_event = obj_event
        if clock is None and start is None:
            raise ValueError("Action requires an injected clock when start is omitted")
        self._clock = clock
        self.start = start or clock.get_date()
        self.duration = duration
        self.end = self.start + datetime.timedelta(minutes=self.duration)

    def abstract(self):
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
        return utils.dump_dict(self.abstract())

    def finished(self):
        if not self.duration:
            return True
        if not self.event.address:
            return True
        if self._clock is None:
            raise RuntimeError("Action has no clock for completion checks")
        return self._clock.get_date() > self.end

    def to_dict(self):
        return {
            "event": self.event.to_dict(),
            "obj_event": self.obj_event.to_dict() if self.obj_event else None,
            "start": self.start.strftime("%Y%m%d-%H:%M:%S"),
            "duration": self.duration,
        }

    @classmethod
    def from_dict(cls, config, *, clock):
        values = dict(config)
        values["event"] = Event.from_dict(values["event"])
        if values.get("obj_event"):
            values["obj_event"] = Event.from_dict(values["obj_event"])
        values["start"] = utils.to_date(
            values["start"], naive_timezone=clock.get_date().tzinfo
        )
        values["clock"] = clock
        return cls(**values)
