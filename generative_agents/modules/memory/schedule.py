"""generative_agents.memory.schedule"""

import copy

from generative_agents.modules import utils


class Schedule:
    def __init__(self, create=None, daily_schedule=None, diversity=5, max_try=5, clock=None):
        if clock is None:
            raise ValueError("Schedule requires an injected clock")
        self._clock = clock
        if create:
            self.create = utils.to_date(
                create, naive_timezone=clock.get_date().tzinfo
            )
        else:
            self.create = None
        self.daily_schedule = daily_schedule or []
        self.diversity = diversity
        self.max_try = max_try

    def abstract(self):
        def _to_stamp(plan):
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
        return utils.dump_dict(self.abstract())

    def add_plan(self, describe, duration, decompose=None):
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
        """Splice a short observed interruption into the current plan.

        Conversations are already facts by the time this method is called.  A
        deterministic local splice preserves the unaffected schedule instead
        of asking an LLM to rewrite the remainder of the hour on every chat.
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
        def _to_date(minutes):
            return self._clock.daily_time(minutes).strftime(time_format)

        start, end = plan["start"], plan["start"] + plan["duration"]
        if time_format:
            start, end = _to_date(start), _to_date(end)
        return start, end

    def decompose(self, plan):
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
        if not self.daily_schedule:
            return False
        return self._clock.daily_format() == self.create.strftime("%A %B %d")

    def to_dict(self):
        return {
            "create": (
                self.create.strftime("%Y%m%d-%H:%M:%S") if self.create else None
            ),
            "daily_schedule": self.daily_schedule,
        }
