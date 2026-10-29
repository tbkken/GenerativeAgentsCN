"""Run-owned 1D conversation state; all mutations become StepResult facts."""
from __future__ import annotations

import copy
from uuid import UUID, uuid5

from generative_agents.ga_runtime.memory.action import Action
from generative_agents.ga_runtime.memory.event import Event


class SocialWorld:
    def __init__(self, game):
        self.game = game
        self.messages: list[dict] = []
        self.conversations: dict[str, dict] = {}
        self.last_actions: dict[str, int] = {}

    def _visible(self, agent_key, message):
        return message["visibility"] == "public" or agent_key in message["participant_agent_keys"]

    def message(self, agent_key, message_id):
        for message in self.messages:
            if message["message_id"] == str(message_id) and self._visible(agent_key, message):
                return copy.deepcopy(message)
        raise ValueError("message is unknown or inaccessible")

    def source(self, agent_key, source_event_id):
        for message in self.messages:
            if message["source_event_id"] == str(source_event_id) and self._visible(agent_key, message):
                return copy.deepcopy(message)
        raise ValueError("source_event_id is unknown or inaccessible")

    def perceive(self, agent_key, arguments):
        agent = self.game.get_agent(agent_key)
        bandwidth = max(0, int(agent.percept_config.get("att_bandwidth", 8)))
        limit = min(bandwidth, max(1, min(100, int(arguments.get("limit", bandwidth or 1)))))
        selected = [item for item in self.messages if self._visible(agent_key, item)]
        if arguments.get("message_id"):
            selected = [self.message(agent_key, arguments["message_id"])]
        if arguments.get("conversation_id"):
            thread = self.conversations.get(str(arguments["conversation_id"]))
            if not thread or (thread["visibility"] == "private" and agent_key not in thread["participant_agent_keys"]):
                raise ValueError("conversation is unknown or inaccessible")
            selected = [item for item in selected if item["conversation_id"] == str(arguments["conversation_id"])]
        for key, comparison in (("after_message_id", lambda a, b: a > b),
                                ("before_message_id", lambda a, b: a < b)):
            if arguments.get(key):
                anchor = self.message(agent_key, arguments[key])
                selected = [item for item in selected if comparison(item["sequence"], anchor["sequence"])]
        candidates = len(selected)
        selected = selected[:limit] if arguments.get("after_message_id") else selected[-limit:] if limit else []
        output = copy.deepcopy(selected)
        for item in output:
            quote = item.get("quoted_message_id")
            if quote:
                original = self.message(agent_key, quote)
                item["quoted_message"] = {key: original[key] for key in
                                          ("message_id", "speaker_agent_key", "content")}
        participants = [{"agent_key": key, "name": value.name} for key, value in self.game.agents.items() if key != agent_key]
        offset = max(0, int(arguments.get("participant_offset") or 0))
        visible_participants = participants[offset:offset + bandwidth]
        return {"environment_type": "social_1d", "presentation": self.game.presentation,
                "space": {"space_id": self.game.space_id, "name": self.game.world_name,
                          "description": self.game.world_definition.get("description", ""),
                          "rules": self.game.world_definition.get("rules", "")},
                "observer": {"agent_key": agent_key, "name": agent.name},
                "participants": visible_participants,
                "next_participant_offset": offset + bandwidth if bandwidth and offset + bandwidth < len(participants) else None,
                "messages": output,
                "attention": {"bandwidth": bandwidth, "message_candidates": candidates,
                              "participant_candidates": len(participants), "participant_returned": len(visible_participants),
                              "returned": len(output), "truncated": candidates > len(output) or len(participants) > len(visible_participants)},
                "first_message_id": output[0]["message_id"] if output else None,
                "last_message_id": output[-1]["message_id"] if output else None}

    def validate_action(self, agent_key, arguments):
        payload = copy.deepcopy(dict(arguments))
        kind = str(payload.get("action_type") or "").upper()
        if kind == "WAIT":
            reason = str(payload.get("wait_reason") or payload.get("description") or "").strip()
            if not reason:
                raise ValueError("1D WAIT requires wait_reason explaining the silence")
            return {**payload, "action_type": "WAIT", "wait_reason": reason}
        if kind != "SPEAK":
            raise ValueError("1D worlds support only SPEAK and WAIT")
        message = str(payload.get("message") or "").strip()
        if not message:
            raise ValueError("SPEAK requires a non-empty message")
        participants = payload.get("participant_agent_keys") or []
        if not isinstance(participants, (list, tuple)) or len(set(participants)) != len(participants):
            raise ValueError("participant_agent_keys must contain distinct Agent keys")
        if any(key not in self.game.agents or key == agent_key for key in participants):
            raise ValueError("unknown or invalid conversation participant")
        visibility = payload.get("visibility", "private" if participants else "public")
        if visibility not in {"public", "private"}:
            raise ValueError("visibility must be public or private")
        if visibility == "private" and len(participants) != 1:
            raise ValueError("private SPEAK requires exactly one other Agent")
        if visibility == "public" and participants:
            raise ValueError("public SPEAK addresses the whole space, not selected participants")
        members = sorted([agent_key, *participants]) if visibility == "private" else sorted(self.game.agents)
        conversation_kind = "direct" if visibility == "private" else self.game.presentation
        conversation_id = str(payload.get("conversation_id") or "").strip()
        parent_id = str(payload.get("reply_to_message_id") or "").strip()
        quote_id = str(payload.get("quoted_message_id") or "").strip()
        if parent_id:
            parent = self.message(agent_key, parent_id)
            if conversation_id and conversation_id != parent["conversation_id"]:
                raise ValueError("reply parent belongs to a different conversation")
            conversation_id = parent["conversation_id"]
        if conversation_id and payload.get("start_new_conversation"):
            raise ValueError("a new conversation cannot specify an existing ID or reply")
        if not conversation_id and not payload.get("start_new_conversation") and conversation_kind != "forum":
            conversation_id = next((key for key, item in reversed(list(self.conversations.items()))
                                    if item["kind"] == conversation_kind and item["open"]
                                    and item["participant_agent_keys"] == members), "")
        if conversation_id:
            thread = self.conversations.get(conversation_id)
            if not thread or thread["visibility"] != visibility or thread["participant_agent_keys"] != members or not thread["open"]:
                raise ValueError("conversation is unknown, inaccessible or closed")
            if conversation_kind == "forum" and not parent_id:
                parent_id = thread["root_message_id"]
        if visibility == "public" and payload.get("end_conversation"):
            raise ValueError("public conversations cannot be closed by an Agent")
        if visibility == "public" and conversation_kind == "chat" and payload.get("start_new_conversation"):
            raise ValueError("the public chat has one continuous conversation")
        title = str(payload.get("title") or "").strip()
        if conversation_kind == "forum" and not conversation_id and not title:
            raise ValueError("a new forum topic requires title")
        if title and (conversation_kind != "forum" or conversation_id):
            raise ValueError("title is only valid for a new forum topic")
        if quote_id:
            quote = self.message(agent_key, quote_id)
            if quote["visibility"] == "private" and (visibility != "private" or quote["conversation_id"] != conversation_id):
                raise ValueError("a private message may only be quoted inside its private conversation")
        return {**payload, "action_type": "SPEAK", "message": message,
                "visibility": visibility, "participant_agent_keys": members,
                "conversation_id": conversation_id or None, "kind": conversation_kind,
                "reply_to_message_id": parent_id or None, "quoted_message_id": quote_id or None,
                "title": title or None}

    def commit(self, agent_key, outcome, stride_minutes):
        agent = self.game.get_agent(agent_key)
        action = outcome.get("world_action") or {}
        payload = copy.deepcopy(action.get("arguments") or {})
        action_type = action.get("action_type", "WAIT")
        iteration = (outcome.get("info") or {}).get("iteration_context") or {}
        step = int((iteration.get("step") or {}).get("number") or agent._current_step_no or 0)
        if step < 1:
            raise ValueError("social action requires a positive Step")
        if step <= self.last_actions.get(agent_key, 0):
            raise ValueError("Agent already committed an action in this Step")
        actor_sequence = list(self.game.agents).index(agent_key) + 1
        events = list(outcome.get("events") or ())
        if action_type == "SPEAK":
            # Revalidate the request at the world boundary, using caller parameters.
            request = {key: value for key, value in payload.items() if key != "kind"}
            request["participant_agent_keys"] = ([key for key in payload["participant_agent_keys"] if key != agent_key]
                                                  if payload["visibility"] == "private" else [])
            payload = self.validate_action(agent_key, request)
            message_id = str(uuid5(self.game.context.run_id, f"social-message:{step}:{agent_key}"))
            if any(item["message_id"] == message_id for item in self.messages):
                raise ValueError("Agent already committed an action in this Step")
            conversation_id = payload["conversation_id"] or str(uuid5(UUID(message_id), "conversation"))
            source_event_id = str(uuid5(UUID(message_id), "world-event"))
            record = {"conversation_id": conversation_id, "participant_agent_keys": payload["participant_agent_keys"],
                      "location": [], "visibility": payload["visibility"], "kind": payload["kind"],
                      "space_id": self.game.space_id, "duration_minutes": stride_minutes,
                      "duration_source": "SCHEDULED", "ended_reason": "EXPLICIT_END" if payload.get("end_conversation") else None,
                      "summary": payload["message"]}
            message = {"message_id": message_id, "sequence": len(self.messages) + 1,
                       "speaker_agent_key": agent_key, "content": payload["message"],
                       "reply_to_message_id": payload["reply_to_message_id"], "quoted_message_id": payload["quoted_message_id"],
                       "title": payload["title"], "step_no": step, "actor_sequence": actor_sequence,
                       "source_event_id": source_event_id}
            self.messages.append({**copy.deepcopy(record), **message})
            thread = self.conversations.setdefault(conversation_id, {**copy.deepcopy(record), "root_message_id": message_id})
            thread["open"] = not bool(payload.get("end_conversation"))
            record["messages"] = [message]
            events.append({"kind": "social_message", "record": record})
            description = f"{agent.name} 发言" if payload["visibility"] == "public" else f"{agent.name} 私聊"
            event = Event(agent.name, "对话", "公共交流" if payload["visibility"] == "public" else "私聊", describe=description, emoji="💬")
        elif action_type == "WAIT":
            payload = self.validate_action(agent_key, {**payload, "action_type": "WAIT"})
            description = payload["wait_reason"]
            event = Event(agent.name, "等待", "下一轮", describe=description, emoji="⏳")
            events.append(self.game._world_domain_event("AGENT_WAITED", (agent_key,), subject=agent.name,
                          predicate="等待", object_value="下一轮", structured_payload={"action_type": "WAIT",
                          "space_id": self.game.space_id, "wait_reason": description,
                          "description": description, "expected_until_step": payload.get("expected_until_step"),
                          "expected_until_time": payload.get("expected_until_time")}))
        else:
            raise ValueError("1D worlds support only SPEAK and WAIT")
        agent.action = Action(event, None, duration=stride_minutes, clock=self.game.context.clock)
        self.last_actions[agent_key] = step
        agent.profile.currently = description
        outcome.setdefault("info", {})["currently"] = description
        outcome["events"] = tuple(events)
        return {"outcome": outcome, "planned_path": (), "executed_path": (), "remaining_path": ()}

    def snapshot(self):
        return {"messages": copy.deepcopy(self.messages), "conversations": copy.deepcopy(self.conversations),
                "last_actions": dict(self.last_actions)}

    def restore(self, value):
        messages = copy.deepcopy(value.get("messages", []))
        conversations = copy.deepcopy(value.get("conversations", {}))
        seen = set()
        for item in messages:
            UUID(item["message_id"])
            UUID(item["source_event_id"])
            if item["message_id"] in seen or item["conversation_id"] not in conversations:
                raise ValueError("invalid social checkpoint message identity")
            seen.add(item["message_id"])
            if item["space_id"] != self.game.space_id or not set(item["participant_agent_keys"]).issubset(self.game.agents):
                raise ValueError("social checkpoint belongs to another space or participant set")
        last_actions = value.get("last_actions", {})
        if not isinstance(last_actions, dict) or not set(last_actions).issubset(self.game.agents) or any(
            not isinstance(step, int) or step < 1 for step in last_actions.values()
        ):
            raise ValueError("invalid social checkpoint action boundary")
        self.messages, self.conversations = messages, conversations
        self.last_actions = dict(last_actions)
