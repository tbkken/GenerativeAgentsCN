"""generative_agents.agent"""

import os
import math
import datetime
import copy
import hashlib
import json

from generative_agents.modules import memory, prompt, utils
from generative_agents.modules.model.llm_model import create_llm_model
from generative_agents.modules.memory.associate import Concept


def estimate_chat_duration(chats, chars_per_minute=240):
    """ga-cn-v1: a non-empty chat occupies at least one virtual minute."""
    if chars_per_minute <= 0:
        raise ValueError("chars_per_minute must be positive")
    return max(
        1,
        math.ceil(sum(len(message) for _, message in chats) / chars_per_minute),
    )


_INVALID_CHAT_MARKERS = ("填坑", "待补充", "占位", "todo", "placeholder")


def valid_chat_message(message, previous=()):
    """Reject empty, placeholder and exact-repeat turns before persistence."""

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
    def __init__(
        self,
        config,
        maze,
        conversation,
        logger,
        *,
        clock,
        random_source,
        prompts,
        models=None,
        model_trace=None,
        algorithm=None,
    ):
        # Runtime-only collaborators such as RunControl contain thread locks and
        # must retain identity. Copy the serializable definition first, then put
        # those injected references back into the per-Agent embedding config.
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
        agent_config.get("associate", {}).get("embedding", {}).update(
            runtime_embedding
        )
        self.name = agent_config["name"]
        self.agent_key = agent_config.get("agent_key", self.name)
        self.maze = maze
        self.conversation = conversation
        self._llm = None
        self.logger = logger
        self._clock = clock
        self._rng = random_source
        self._prompts = prompts
        self._models = models
        self._model_trace = model_trace
        self._algorithm = algorithm
        self._result_events = []

        # agent config
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

        # memory
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

        # prompt
        self.scratch = prompt.Scratch(
            self.name,
            agent_config["currently"],
            agent_config["scratch"],
            clock=clock,
            random_source=random_source,
            prompts=prompts,
        )

        # status
        status = {"poignancy": 0}
        self.status = utils.update_dict(status, agent_config.get("status", {}))
        self.plan = agent_config.get("plan", {})

        # record
        self.last_record = self._clock.daily_duration()

        # action and events
        if "action" in agent_config:
            self.action = memory.Action.from_dict(agent_config["action"], clock=clock)
            # A verified checkpoint carries the exact observed coordinate.  The
            # address may cover many tiles, so re-choosing one would silently
            # fork the resumed trajectory before RNG state is restored.
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

        # update maze
        self.coord, self.path = None, None
        self.move(initial_coord, agent_config.get("path"))
        if self.coord is None:
            self.coord = initial_coord

    def abstract(self):
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
        return utils.dump_dict(self.abstract())

    def reset(self):
        if not self._llm:
            if self._models is not None:
                self._llm = self._models.get("chat")
            else:
                self._llm = create_llm_model(
                    self.think_config["llm"], recorder=self._model_trace
                )

    def completion(self, func_hint, *args, **kwargs):
        assert hasattr(
            self.scratch, "prompt_" + func_hint
        ), "Can not find func prompt_{} from scratch".format(func_hint)
        func = getattr(self.scratch, "prompt_" + func_hint)
        res = func(*args, **kwargs)._asdict()
        title, msg = "{}.{}".format(self.name, func_hint), {}
        if self.llm_available():
            self.logger.info("{} -> {}".format(self.name, func_hint))
            output = self._llm.completion(
                **res,
                caller=func_hint,
                agent_key=self.agent_key,
                prompt_key=func_hint,
            )
            msg = {"<PROMPT>": "\n" + res["prompt"] + "\n"}
            msg.update({"response": output})
        self.logger.debug(utils.block_msg(title, msg))
        return output

    def think(self, status, agents_by_name):
        events = self.move(status["coord"], status.get("path"))
        plan, _ = self.make_schedule()

        if (plan["describe"] == "sleeping" or "睡" in plan["describe"]) and self.is_awake():
            self.logger.info("{} is going to sleep...".format(self.name))
            address = self.spatial.find_address("睡觉", as_list=True)
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

    def move(self, coord, path=None):
        events = {}

        def _update_tile(coord):
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
                self.maze.update_obj(
                    self.coord, memory.Event(addr[-1], address=addr)
                )
            events.update({e: self.coord for e in tile.get_events()})
        if not path:
            events.update(_update_tile(coord))
        self.coord = coord
        self.path = path or []

        return events

    def make_schedule(self):
        if not self.schedule.scheduled():
            self.logger.info("{} is making schedule...".format(self.name))
            # update currently
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
            # make init schedule
            self.schedule.create = self._clock.get_date()
            wake_up = self.completion("wake_up")
            init_schedule = self.completion("schedule_init", wake_up)
            # make daily schedule
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
        # decompose current plan
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
        self.action = memory.Action(
            event, start=start, duration=duration, clock=self._clock
        )
        if local_interruption:
            self.schedule.insert_interruption(
                event.get_describe(), start, duration
            )
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
        scope = self.maze.get_scope(self.coord, self.percept_config)
        # add spatial memory
        for tile in scope:
            if tile.has_address("game_object"):
                self.spatial.add_leaf(tile.address)
        events, arena = {}, self.get_tile().get_address("arena")
        # gather events in scope
        for tile in scope:
            if not tile.events or tile.get_address("arena") != arena:
                continue
            dist = math.dist(tile.coord, self.coord)
            for event in tile.get_events():
                if dist < events.get(event, float("inf")):
                    events[event] = dist
        events = list(sorted(events.keys(), key=lambda k: events[k]))
        # get concepts
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
        if self._reaction(agents_by_name):
            return
        if self.path:
            return
        if self.action.finished():
            self.action = self._determine_action()

    # create action && object events
    def make_event(self, subject, describe, address):
        # emoji = self.completion("describe_emoji", describe)
        # return self.completion(
        #     "describe_event", subject, subject + describe, address, emoji
        # )

        e_describe = describe.replace("(", "").replace(")", "").replace("<", "").replace(">", "")
        if e_describe.startswith(subject + "此时"):
            e_describe = e_describe[len(subject + "此时"):]
        if e_describe.startswith(subject):
            e_describe = e_describe[len(subject):]
        event = memory.Event(
            subject, "此时", e_describe, describe=describe, address=address
        )
        return event

    def reflect(self):
        def _add_thought(thought, evidence=None):
            # event = self.completion(
            #     "describe_event",
            #     self.name,
            #     thought,
            #     address=self.get_tile().get_address(),
            # )
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
        # summary thought
        focus = self.completion("reflect_focus", nodes, 3)
        retrieved = self.associate.retrieve_focus(focus, reduce_all=False)
        for r_nodes in retrieved.values():
            thoughts = self.completion("reflect_insights", r_nodes, 5)
            for thought, evidence in thoughts:
                _add_thought(thought, evidence)
        # summary chats
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

        # filter tile with self event
        def _ignore_target(t_coord):
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
        focus = None
        agents_by_name = agents_by_name or {}
        ignore_words = ignore_words or ["空闲"]

        def _focus(concept):
            return concept.event.subject in agents_by_name

        def _ignore(concept):
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
        def _skip(event):
            if not event.address or "sleeping" in event.get_describe(False) or "睡觉" in event.get_describe(False):
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
        if len(self.schedule.daily_schedule) < 1 or len(other.schedule.daily_schedule) < 1:
            # initializing
            return False
        if self._skip_react(other):
            return False
        if self.path or other.path:
            return False
        if self.get_event().fit(predicate="对话") or other.get_event().fit(predicate="对话"):
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
                end = self.completion(
                    "decide_chat_terminate", self, other, chats
                )
                if end:
                    break
            else:
                chats.append((self.name, text))

            text = generate_valid_turn(other, self, relations[1])
            if text is None:
                break
            if i > 0 and self.repeat_detection_enabled:
                # 对于响应对话的Agent，从第2轮开始，检查是否出现“复读”现象
                end = self.completion(
                    "generate_chat_check_repeat", other, chats, text
                )
                if end:
                    break

            chats.append((other.name, text))

            # 对于响应对话的Agent，从第1轮开始，检查话题是否结束
            end = other.completion(
                "decide_chat_terminate", other, self, chats
            )
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
        self.conversation[key].append({f"{self.name} -> {other.name} @ {'，'.join(self.get_event().address)}": chats})

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
                "{}：{}".format(speaker, content)
                for speaker, content in chats
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
        self.schedule_chat(
            chats, chat_summary, start, duration, other
        )
        other.schedule_chat(chats, chat_summary, start, duration, self)
        return True

    def _wait_other(self, other, focus):
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
        self._result_events.append(
            {
                "kind": "memory",
                "memory_kind": "CREATED",
                "agent_key": self.agent_key,
                "memory_id": concept.node_id,
                "memory_type": e_type.upper(),
                "description": concept.describe,
                "poignancy": concept.poignancy,
            }
        )
        for memory_id in self.associate.last_evicted:
            self._result_events.append(
                {
                    "kind": "memory",
                    "memory_kind": "EVICTED",
                    "agent_key": self.agent_key,
                    "memory_id": memory_id,
                    "memory_type": e_type.upper(),
                }
            )
        return concept

    def drain_result_events(self):
        events, self._result_events = tuple(self._result_events), []
        return events

    def get_tile(self):
        return self.maze.tile_at(self.coord)

    def get_event(self, as_act=True):
        return self.action.event if as_act else self.action.obj_event

    def is_awake(self):
        if not self.action:
            return True
        if self.get_event().fit(self.name, "is", "sleeping"):
            return False
        if self.get_event().fit(self.name, "正在", "睡觉"):
            return False
        return True

    def llm_available(self):
        if not self._llm:
            return False
        return self._llm.is_available()

    def to_dict(self, with_action=True):
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
        }
        if with_action:
            info.update({"action": self.action.to_dict()})
        return info
