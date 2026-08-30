"""Run-scoped MCP capabilities available to Brain and child Skills."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Mapping

from .iteration import IterationContext


@dataclass(frozen=True, slots=True)
class PlannedWorldAction:
    """One validated, not-yet-committed world mutation for an Agent step."""

    action_type: str
    arguments: Mapping[str, Any]
    path: tuple[tuple[int, int], ...] = ()
    observation: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "arguments": copy.deepcopy(dict(self.arguments)),
            "path": [list(coord) for coord in self.path],
            "observation": (
                copy.deepcopy(dict(self.observation)) if self.observation else None
            ),
        }


class SimulationMCPServer:
    """Bound MCP view for one Agent and one iteration.

    Reads are unlimited. ``world-act`` may succeed only once, which gives the
    scheduler an unambiguous mutation to commit and replay for this Agent step.
    """

    def __init__(self, game, iteration: IterationContext, *, memory_stream=None):
        self.game = game
        self.iteration = iteration
        self.memory_stream = memory_stream
        self._action: PlannedWorldAction | None = None

    @property
    def action(self) -> PlannedWorldAction | None:
        return self._action

    def discard_action(self) -> None:
        """Discard an uncommitted choice when the enclosing Brain iteration fails."""

        self._action = None

    def tools(self) -> list[dict[str, Any]]:
        tools = [
            {
                "name": "world-perceive",
                "description": (
                    "Observe compact, unique four-layer spatial semantics, events, "
                    "nearby Agents, and nearby Game Objects without changing the "
                    "simulation world. radius_tiles is optional and is always "
                    "clamped to this Agent's configured vision radius."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "radius_tiles": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                        }
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "world-act",
                "description": (
                    "Select the single replayable world-changing action for this "
                    "Agent iteration. Call at most once."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action_type": {
                            "type": "string",
                            "enum": [
                                "MOVE",
                                "ACT",
                                "WAIT",
                                "SPEAK",
                                "INTERACT",
                                "SET_OBJECT_STATE",
                            ],
                        },
                        "description": {"type": "string"},
                        "predicate": {"type": "string"},
                        "object": {"type": "string"},
                        "emoji": {"type": "string"},
                        "target_address": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "For MOVE, the destination. For ACT, when supplied, "
                                "it must equal the Agent's current four-layer address."
                            ),
                        },
                        "target_coord": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
                            "description": (
                                "For MOVE, the destination. For ACT, when supplied, "
                                "it must equal the Agent's current coordinate."
                            ),
                        },
                        "participant_agent_keys": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "message": {"type": "string"},
                        "conversation_id": {
                            "type": "string",
                            "description": "Continue a known conversation UUID.",
                        },
                        "start_new_conversation": {
                            "type": "boolean",
                            "default": False,
                        },
                        "end_conversation": {
                            "type": "boolean",
                            "default": False,
                        },
                        "selection_key": {"type": "string"},
                        "request": {"type": "string"},
                        "object_key": {"type": "string"},
                        "state_patch": {"type": "object"},
                        "wait_reason": {
                            "type": "string",
                            "description": (
                                "For WAIT, explain the external condition or schedule "
                                "boundary being awaited."
                            ),
                        },
                        "expected_until_step": {
                            "type": "integer",
                            "minimum": 1,
                            "description": (
                                "For an intentional bounded WAIT, the last simulation "
                                "step through which waiting is expected."
                            ),
                        },
                        "expected_until_time": {
                            "type": "string",
                            "description": (
                                "For an intentional bounded WAIT, the simulation-time "
                                "boundary in ISO-8601 form."
                            ),
                        },
                    },
                    "required": ["action_type"],
                    "additionalProperties": False,
                },
            },
        ]
        if self.memory_stream is not None:
            tools.extend(
                [
                    {
                        "name": "memory-stream-search",
                        "description": (
                            "Semantically search this Agent's persistent memory "
                            "stream. The Agent identity is injected by the system."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "limit": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 100,
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                    {
                        "name": "memory-stream-append",
                        "description": (
                            "Save natural-language memory for this Agent, optionally "
                            "with Event(subject, predicate, object) semantics."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "kind": {"type": "string"},
                                "poignancy": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 10,
                                },
                                "subject": {"type": "string"},
                                "predicate": {"type": "string"},
                                "object": {"type": "string"},
                                "address": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "evidence_memory_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["content"],
                            "additionalProperties": False,
                        },
                    },
                    {
                        "name": "memory-stream-supersede",
                        "description": (
                            "Replace one active memory with a corrected version. "
                            "The old version remains replayable as SUPERSEDED."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "memory_id": {"type": "string"},
                                "content": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["memory_id", "content"],
                            "additionalProperties": False,
                        },
                    },
                    {
                        "name": "memory-stream-invalidate",
                        "description": (
                            "Mark one active memory INVALIDATED so it no longer "
                            "participates in retrieval while history is retained."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "memory_id": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["memory_id", "reason"],
                            "additionalProperties": False,
                        },
                    },
                ]
            )
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "world-perceive":
                value = self._perceive(arguments)
            elif name == "world-act":
                value = self._plan_action(arguments)
            elif name == "memory-stream-search" and self.memory_stream is not None:
                value = self.memory_stream.search(
                    agent_key=self.iteration.agent_key,
                    query=str(arguments.get("query") or ""),
                    limit=int(arguments.get("limit") or 8),
                )
            elif name == "memory-stream-append" and self.memory_stream is not None:
                payload = dict(arguments)
                payload["agent_key"] = self.iteration.agent_key
                payload.setdefault("kind", "event")
                payload.setdefault("poignancy", 1)
                payload.setdefault("address", list(self.iteration.address))
                value = self.memory_stream.append(**payload)
            elif name == "memory-stream-supersede" and self.memory_stream is not None:
                value = self.memory_stream.supersede(
                    agent_key=self.iteration.agent_key,
                    memory_id=str(arguments.get("memory_id") or ""),
                    content=str(arguments.get("content") or ""),
                    reason=str(arguments.get("reason") or "").strip() or None,
                )
            elif name == "memory-stream-invalidate" and self.memory_stream is not None:
                value = self.memory_stream.invalidate(
                    agent_key=self.iteration.agent_key,
                    memory_id=str(arguments.get("memory_id") or ""),
                    reason=str(arguments.get("reason") or ""),
                )
            else:
                raise ValueError(f"Unknown simulation MCP tool: {name}")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(value, ensure_ascii=False, default=str),
                    }
                ],
                "isError": False,
            }
        except (KeyError, TypeError, ValueError) as exc:
            return {
                "content": [{"type": "text", "text": f"MCP error: {exc}"}],
                "isError": True,
            }

    def _perceive(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        agent = self.game.get_agent(self.iteration.agent_key)
        percept_config = getattr(agent, "percept_config", {}) or {}
        vision_radius = max(0, int(percept_config.get("vision_r", 4)))
        requested_radius = (
            int(arguments["radius_tiles"])
            if arguments.get("radius_tiles") is not None
            else vision_radius
        )
        radius = max(0, min(requested_radius, vision_radius))
        attention_bandwidth = max(0, int(percept_config.get("att_bandwidth", 8)))
        center = tuple(agent.coord)

        semantic_query = getattr(self.game.maze, "semantic_nodes_in_scope", None)
        if not callable(semantic_query):
            raise ValueError("runtime map does not provide a spatial semantic index")
        indexed_nodes = list(semantic_query(center, radius))
        nodes_by_id = {
            str(item.get("id") or ""): copy.deepcopy(dict(item))
            for item in indexed_nodes
            if str(item.get("id") or "").strip()
        }
        # IterationContext is the authoritative current-location snapshot.  Add
        # its four anchors defensively so a legacy or partially indexed map can
        # never hide the Agent's own World/Sector/Arena/Game Object semantics.
        for level, semantic in enumerate(self.iteration.spatial_semantics):
            item = copy.deepcopy(dict(semantic))
            address = list(self.iteration.address[: level + 1])
            node_id = str(item.get("id") or "").strip() or "legacy:{}:{}".format(
                str(item.get("kind") or "").upper(),
                ":".join(address),
            )
            current = nodes_by_id.setdefault(node_id, item)
            current.update(
                {
                    "id": node_id,
                    "address": address,
                    "distance_tiles": 0.0,
                    "relation": "CURRENT",
                }
            )

        kind_order = {"WORLD": 0, "SECTOR": 1, "ARENA": 2, "GAME_OBJECT": 3}

        def public_node(item):
            return {
                "id": str(item.get("id") or ""),
                "kind": str(item.get("kind") or "").upper(),
                "name": str(item.get("name") or item.get("id") or ""),
                "semantic": str(item.get("semantic") or ""),
                "address": list(item.get("address") or ()),
                "distance_tiles": float(item.get("distance_tiles") or 0.0),
                "relation": str(item.get("relation") or "NEARBY"),
            }

        space_candidates = sorted(
            (
                public_node(item)
                for item in nodes_by_id.values()
                if str(item.get("kind") or "").upper() != "GAME_OBJECT"
            ),
            key=lambda item: (
                item["relation"] != "CURRENT",
                item["distance_tiles"],
                kind_order.get(item["kind"], 99),
                item["id"],
            ),
        )
        current_spaces = [
            item for item in space_candidates if item["relation"] == "CURRENT"
        ]
        nearby_spaces = [
            item for item in space_candidates if item["relation"] != "CURRENT"
        ]
        spatial_nodes = [
            *current_spaces,
            *nearby_spaces[:attention_bandwidth],
        ]

        agent_candidates = []
        for key, other in sorted(self.game.agents.items()):
            if key == self.iteration.agent_key:
                continue
            distance = max(
                abs(int(other.coord[0]) - int(center[0])),
                abs(int(other.coord[1]) - int(center[1])),
            )
            if distance > radius:
                continue
            agent_candidates.append(
                {
                    "agent_key": key,
                    "name": other.name,
                    "coord": list(other.coord),
                    "distance_tiles": float(distance),
                    "address": list(other.get_tile().get_address()),
                    "current_action": other.get_event().to_dict(),
                }
            )
        agent_candidates.sort(
            key=lambda item: (item["distance_tiles"], item["agent_key"])
        )
        nearby_agents = agent_candidates[:attention_bandwidth]

        event_query = getattr(self.game.maze, "events_in_scope", None)
        if not callable(event_query):
            raise ValueError("runtime map does not provide an event perception index")
        event_candidates = list(event_query(center, radius))
        events = event_candidates[:attention_bandwidth]

        object_candidates = {
            str(item.get("id") or ""): {
                "object_key": str(item.get("id") or ""),
                "object_name": str(item.get("name") or item.get("id") or ""),
                "semantic": str(item.get("semantic") or ""),
                "address": list(item.get("address") or ()),
                "distance_tiles": float(item.get("distance_tiles") or 0.0),
                "relation": str(item.get("relation") or "NEARBY"),
                "state": {},
                "interactions": [],
            }
            for item in nodes_by_id.values()
            if str(item.get("kind") or "").upper() == "GAME_OBJECT"
            and str(item.get("id") or "").strip()
        }
        for affordance in self.game.game_object_interactions.nearby(center):
            distance = float(affordance.distance_to(center))
            if distance > radius:
                continue
            object_item = object_candidates.setdefault(
                affordance.object_key,
                {
                    "object_key": affordance.object_key,
                    "object_name": affordance.object_name,
                    "semantic": "",
                    "address": list(affordance.address),
                    "distance_tiles": round(distance, 3),
                    "relation": "NEARBY",
                    "state": {},
                    "interactions": [],
                },
            )
            object_item["distance_tiles"] = min(
                float(object_item["distance_tiles"]), round(distance, 3)
            )
            object_item["state"] = copy.deepcopy(dict(affordance.object_state))
            object_item["interactions"].append(
                {
                    "selection_key": affordance.selection_key,
                    "interaction_key": affordance.interaction_key,
                    "skill_name": affordance.skill_name,
                    "description": affordance.description,
                }
            )
        sorted_objects = sorted(
            object_candidates.values(),
            key=lambda item: (
                item["relation"] != "CURRENT",
                item["distance_tiles"],
                item["object_key"],
            ),
        )
        current_objects = [
            item for item in sorted_objects if item["relation"] == "CURRENT"
        ]
        nearby_objects = [
            item for item in sorted_objects if item["relation"] != "CURRENT"
        ]
        objects = [
            *current_objects,
            *nearby_objects[:attention_bandwidth],
        ]

        current_node_ids = [
            item["id"]
            for item in sorted(
                (
                    public_node(item)
                    for item in nodes_by_id.values()
                    if str(item.get("relation") or "") == "CURRENT"
                ),
                key=lambda item: (kind_order.get(item["kind"], 99), item["id"]),
            )
        ]
        return {
            "now": self.iteration.now.isoformat(),
            "agent": self.iteration.as_dict()["agent"],
            "requested_radius_tiles": requested_radius,
            "radius_tiles": radius,
            "vision_radius_tiles": vision_radius,
            "current_location": {
                "coord": list(center),
                "address": list(self.iteration.address),
                "spatial_node_ids": current_node_ids,
            },
            "spatial_nodes": spatial_nodes,
            "nearby_agents": nearby_agents,
            "game_objects": objects,
            "events": events,
            "active_conversations": list(
                getattr(
                    self.game,
                    "active_conversations_for",
                    lambda _agent_key: (),
                )(self.iteration.agent_key)
            ),
            "attention": {
                "bandwidth": attention_bandwidth,
                "nearby_space_candidates": len(nearby_spaces),
                "nearby_agent_candidates": len(agent_candidates),
                "game_object_candidates": len(nearby_objects),
                "event_candidates": len(event_candidates),
                "truncated": any(
                    count > attention_bandwidth
                    for count in (
                        len(nearby_spaces),
                        len(agent_candidates),
                        len(nearby_objects),
                        len(event_candidates),
                    )
                ),
            },
        }

    def _plan_action(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self._action is not None:
            raise ValueError("world-act already selected an action for this iteration")
        action_type = str(arguments.get("action_type") or "").strip().upper()
        if action_type not in {
            "MOVE",
            "ACT",
            "WAIT",
            "SPEAK",
            "INTERACT",
            "SET_OBJECT_STATE",
        }:
            raise ValueError(f"unsupported world action: {action_type}")
        payload = copy.deepcopy(dict(arguments))
        payload["action_type"] = action_type
        path: tuple[tuple[int, int], ...] = ()
        observation: Mapping[str, Any] | None = None
        if action_type == "MOVE":
            path = self._resolve_path(payload)
            payload["target_address"] = list(payload.get("target_address") or ())
            payload["target_coord"] = (
                list(path[-1]) if path else list(self.iteration.coord)
            )
        elif action_type == "ACT":
            predicate = str(payload.get("predicate") or "").strip()
            object_value = str(payload.get("object") or "").strip()
            if not predicate or not object_value:
                raise ValueError("ACT requires non-empty Event predicate and object")
            current_coord = tuple(self.iteration.coord)
            current_address = tuple(self.iteration.address)
            requested_coord = payload.get("target_coord")
            if requested_coord is not None:
                if not isinstance(requested_coord, (list, tuple)) or len(requested_coord) != 2:
                    raise ValueError("ACT target_coord must contain exactly two integers")
                if tuple(int(item) for item in requested_coord) != current_coord:
                    raise ValueError(
                        "ACT can only occur at the current coordinate; use MOVE first "
                        "and ACT in a later iteration"
                    )
            requested_address = payload.get("target_address")
            if requested_address is not None:
                if not isinstance(requested_address, (list, tuple)):
                    raise ValueError("ACT target_address must be a four-layer address array")
                if tuple(str(item) for item in requested_address) != current_address:
                    raise ValueError(
                        "ACT can only occur at the current address; use MOVE first "
                        "and ACT in a later iteration"
                    )
            object_key = str(payload.get("object_key") or "").strip()
            if object_key:
                current_object_keys = {
                    str(item.get("id") or "").strip()
                    for item in self.iteration.spatial_semantics
                    if str(item.get("kind") or "").upper() == "GAME_OBJECT"
                    and str(item.get("id") or "").strip()
                }
                if object_key not in current_object_keys:
                    raise ValueError(
                        "ACT object_key is not the current Game Object; use MOVE first "
                        "and ACT in a later iteration"
                    )
                payload["object_key"] = object_key
            payload["predicate"] = predicate
            payload["object"] = object_value
            # ACT is a current-location fact.  Persist the authoritative values
            # even when the model omitted these optional hints.
            payload["target_coord"] = list(current_coord)
            payload["target_address"] = list(current_address)
        elif action_type == "SPEAK":
            participants = self._participant_keys(
                payload.get("participant_agent_keys") or ()
            )
            message = str(payload.get("message") or "").strip()
            if not participants or not message:
                raise ValueError("SPEAK requires participants and a non-empty message")
            if len(participants) != 1:
                raise ValueError("SPEAK currently requires exactly one other Agent")
            payload["participant_agent_keys"] = list(participants)
            payload["message"] = message
            payload["conversation_id"] = (
                str(payload.get("conversation_id") or "").strip() or None
            )
            payload["start_new_conversation"] = bool(
                payload.get("start_new_conversation", False)
            )
            payload["end_conversation"] = bool(payload.get("end_conversation", False))
        elif action_type == "INTERACT":
            selection_key = str(payload.get("selection_key") or "").strip()
            if not selection_key:
                raise ValueError("INTERACT requires selection_key")
            observation = self.game.game_object_interactions.interact_selected(
                self.game.get_agent(self.iteration.agent_key),
                selection_key,
                step_no=self.iteration.step_no,
                request=str(payload.get("request") or "").strip() or None,
            )
            payload["selection_key"] = selection_key
        elif action_type == "SET_OBJECT_STATE":
            object_key = str(payload.get("object_key") or "").strip()
            state_patch = payload.get("state_patch")
            nearby_keys = {
                item.object_key
                for item in self.game.game_object_interactions.nearby(
                    self.iteration.coord
                )
            }
            if object_key not in nearby_keys:
                raise ValueError(f"Game Object is not available nearby: {object_key}")
            if not isinstance(state_patch, Mapping) or not state_patch:
                raise ValueError("SET_OBJECT_STATE requires a non-empty state_patch")
            payload["object_key"] = object_key
            payload["state_patch"] = copy.deepcopy(dict(state_patch))
        self._action = PlannedWorldAction(
            action_type=action_type,
            arguments=payload,
            path=path,
            observation=observation,
        )
        return {"accepted": True, "action": self._action.as_dict()}

    def _resolve_path(self, payload: Mapping[str, Any]) -> tuple[tuple[int, int], ...]:
        current = tuple(self.iteration.coord)
        target_coord = payload.get("target_coord")
        target_address = payload.get("target_address")
        candidates: list[tuple[int, int]] = []
        if target_coord is not None:
            if not isinstance(target_coord, (list, tuple)) or len(target_coord) != 2:
                raise ValueError("target_coord must contain exactly two integers")
            candidate = (int(target_coord[0]), int(target_coord[1]))
            if not (
                0 <= candidate[0] < self.game.maze.maze_width
                and 0 <= candidate[1] < self.game.maze.maze_height
            ):
                raise ValueError("target_coord is outside the map")
            if self.game.maze.tile_at(candidate).collision:
                raise ValueError("target_coord is not traversable")
            candidates = [candidate]
        elif target_address:
            if not isinstance(target_address, (list, tuple)):
                raise ValueError("target_address must be a four-layer address array")
            candidates = sorted(self.game.maze.get_address_tiles(target_address))
        else:
            raise ValueError("MOVE requires target_address or target_coord")
        routes = []
        for candidate in candidates:
            if candidate == current:
                routes.append((candidate, ()))
                continue
            route = tuple(
                tuple(coord) for coord in self.game.maze.find_path(current, candidate)
            )
            if route:
                routes.append((candidate, route[1:] if route[0] == current else route))
        if not routes:
            raise ValueError("MOVE target is unreachable")
        _, path = min(routes, key=lambda item: (len(item[1]), item[0]))
        return path

    def _participant_keys(self, values) -> tuple[str, ...]:
        selected: set[str] = set()
        for raw in values:
            value = str(raw)
            key = (
                value
                if value in self.game.agents
                else self.game.agent_keys_by_name.get(value)
            )
            if key and key != self.iteration.agent_key:
                selected.add(key)
        return tuple(sorted(selected))


__all__ = ["PlannedWorldAction", "SimulationMCPServer"]
