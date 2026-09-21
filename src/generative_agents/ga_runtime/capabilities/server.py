"""Run-scoped MCP capabilities available to Brain and child Skills."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from generative_agents.ga_protocol.facts.movement import movement_activity_from_predicate

from generative_agents.ga_runtime.engine.iteration import IterationContext


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

    @property
    def memory_owner_key(self) -> str:
        return self.iteration.agent_key

    def _observer(self):
        return self.game.get_agent(self.iteration.agent_key)

    def _public_object_state(self, object_key: str) -> dict:
        system = self.game.game_object_interactions
        reader = getattr(system, "public_state", system.object_state)
        return reader(object_key)

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
                "name": "world-navigate",
                "description": (
                    "Read-only navigation from your current position. Query a coordinate "
                    "within your vision radius, or an exact perceived or remembered arena/object address. "
                    "Returns reachability, distance and next_coord (only the FIRST path tile), "
                    "never undiscovered map semantics. Does not move you. To continue toward "
                    "the queried destination, submit MOVE with the SAME target_coord or "
                    "target_address; do not replace it with next_coord unless you intend "
                    "to stop at that adjacent tile. The kernel applies the Step movement budget."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_coord": {"type": "array", "items": {"type": "integer"},
                                         "minItems": 2, "maxItems": 2},
                        "target_address": {"type": "array", "items": {"type": "string"},
                                           "minItems": 3, "maxItems": 4},
                    },
                    "additionalProperties": False,
                    "oneOf": [{"required": ["target_coord"]}, {"required": ["target_address"]}],
                },
            },
            {
                "name": "world-act",
                "description": (
                    "Select the single replayable world-changing action for this "
                    "Agent iteration. Call at most once. SET_OBJECT_STATE uses object_key and "
                    "state_patch; use state_patch.state for an observed visual state name, "
                    "or an empty string to restore the default appearance."
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
                        "predicate": {"type": "string", "description": (
                            "For MOVE, an optional short natural-language phrase for the activity "
                            "performed while moving (at most 240 characters), without locations, "
                            "coordinates, plans or claims of arrival. The committed movement_activity "
                            "preserves this phrase. Actual displacement and arrival at this MOVE's "
                            "target are determined by the kernel. For ACT, the Event predicate."
                        )},
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
                        "target_node_id": {
                            "type": "string",
                            "description": (
                                "Optional stable spatial node identity for MOVE. "
                                "When supplied it must exist at the destination."
                            ),
                        },
                        "participant_agent_keys": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "message": {"type": "string"},
                        "conversation_id": {
                            "type": "string",
                            "description": (
                                "Continue an open conversation UUID belonging to you "
                                "and the selected participant. Omit for a new conversation "
                                "or when changing participants. Do not invent an ID."
                            ),
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
            tool = next((item for item in self.tools() if item["name"] == name), None)
            if tool is None:
                raise ValueError(f"Unknown simulation MCP tool: {name}")
            schema = tool["inputSchema"]
            if not isinstance(arguments, dict) or set(arguments) - set(schema.get("properties", {})):
                raise ValueError("unknown arguments; actor identity is injected by the runtime")
            if set(schema.get("required", [])) - set(arguments):
                raise ValueError("required MCP arguments are missing")
            if name == "world-perceive":
                value = self._perceive(arguments)
            elif name == "world-navigate":
                value = self._navigate(arguments)
            elif name == "world-act":
                value = self._plan_action(arguments)
            elif name == "memory-stream-search" and self.memory_stream is not None:
                value = self.memory_stream.search(
                    agent_key=self.memory_owner_key,
                    query=str(arguments.get("query") or ""),
                    limit=int(arguments.get("limit") or 8),
                )
            elif name == "memory-stream-append" and self.memory_stream is not None:
                payload = dict(arguments)
                payload["agent_key"] = self.memory_owner_key
                payload.setdefault("kind", "event")
                payload.setdefault("poignancy", 1)
                payload.setdefault("address", list(self.iteration.address))
                value = self.memory_stream.append(**payload)
            elif name == "memory-stream-supersede" and self.memory_stream is not None:
                value = self.memory_stream.supersede(
                    agent_key=self.memory_owner_key,
                    memory_id=str(arguments.get("memory_id") or ""),
                    content=str(arguments.get("content") or ""),
                    reason=str(arguments.get("reason") or "").strip() or None,
                )
            elif name == "memory-stream-invalidate" and self.memory_stream is not None:
                value = self.memory_stream.invalidate(
                    agent_key=self.memory_owner_key,
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

    def _navigate(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if set(arguments) - {"target_coord", "target_address"} or len(arguments) != 1:
            raise ValueError("navigation requires exactly one target; identity is injected")
        agent = self.game.get_agent(self.iteration.agent_key)
        radius = max(0, int((getattr(agent, "percept_config", {}) or {}).get("vision_r", 4)))
        current = tuple(self.iteration.coord)
        coord = arguments.get("target_coord")
        if coord is not None:
            if not isinstance(coord, (list, tuple)) or len(coord) != 2 or any(type(v) is not int for v in coord):
                raise ValueError("target_coord must contain exactly two integers")
            if max(abs(coord[i] - current[i]) for i in (0, 1)) > radius:
                raise ValueError("navigation target is outside this Agent's vision")
        else:
            address = arguments.get("target_address")
            if (not isinstance(address, (list, tuple)) or not 3 <= len(address) <= 4
                    or any(not isinstance(v, str) or not v.strip() for v in address)):
                raise ValueError("navigation requires an exact arena/object address")
            address = list(address)
            known = address == list(self.iteration.address[:len(address)])
            tree = getattr(getattr(agent, "spatial", None), "tree", {})
            for part in address:
                if isinstance(tree, dict) and part in tree:
                    tree = tree[part]
                elif isinstance(tree, list) and part == address[-1] and part in tree:
                    tree = True
                else:
                    tree = None
                    break
            known = known or tree is not None
            if not known:
                perception = self._perceive({})
                known = any(
                    list(node.get("address") or []) == address
                    for collection in ("spatial_nodes", "game_objects")
                    for node in perception.get(collection, [])
                )
            if not known:
                raise ValueError("navigation target is not perceived or remembered by this Agent")
        try:
            path = self._resolve_path(arguments, navigation=True)
        except ValueError as exc:
            if str(exc) in {"target_coord is not traversable", "navigation target is unreachable"}:
                return {"reachable": False, "reason": "BLOCKED_OR_DISCONNECTED"}
            raise
        return {"reachable": True, "distance_tiles": len(path),
                "next_coord": list(path[0]) if path else list(current),
                "movement_required": bool(path),
                "requested_target": copy.deepcopy(dict(arguments)),
                "next_coord_role": "FIRST_PATH_TILE_NOT_DESTINATION"}

    def _perceive(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        agent = self._observer()
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
            if key == self.memory_owner_key:
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
                "state": self._public_object_state(str(item.get("id") or "")),
                **({"available_visual_states": list(item["available_visual_states"]), "default_visual_state": "", "state_change_radius_tiles": max([float(binding.get("interaction_radius_tiles", 2)) for binding in item.get("skill_bindings", [])] or [2])} if item.get("available_visual_states") else {}),
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
            object_item["state"] = self._public_object_state(affordance.object_key)
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
            **{key: value for key, value in self.iteration.as_dict().items()
               if key in {"agent", "game_object"}},
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
                )(self.memory_owner_key)
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
            # This is still a planned request. Only World Commit can turn the
            # activity into a formal fact after a real displacement succeeds.
            movement_activity_from_predicate(payload.get("predicate"))
            requested_address = payload.get("target_address")
            requested_coord = payload.get("target_coord")
            path = self._resolve_path(payload)
            destination = tuple(path[-1])
            destination_address = tuple(
                str(part) for part in self.game.maze.tile_at(destination).get_address()
            )
            if not destination_address:
                raise ValueError("MOVE destination has no spatial address")
            payload["requested_target_address"] = (
                list(requested_address) if requested_address is not None else None
            )
            payload["requested_target_coord"] = (
                list(requested_coord) if requested_coord is not None else None
            )
            # The planned action only exposes the canonical destination.  Raw
            # model hints remain available under ``requested_*`` for audit, but
            # can no longer masquerade as committed world facts.
            payload["target_address"] = list(destination_address)
            payload["target_coord"] = list(destination)
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
            self.game.validate_conversation_message(
                self.iteration.agent_key,
                participants,
                requested_conversation_id=payload["conversation_id"],
                start_new=payload["start_new_conversation"],
            )
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
            if getattr(self.game.game_object_interactions, "has_skill", lambda key: False)(object_key):
                raise ValueError("a Skill-bound object controls its own state; use INTERACT to request a change")
            state_patch = payload.get("state_patch")
            nearby_keys = {
                item.object_key
                for item in self.game.game_object_interactions.nearby(
                    self.iteration.coord
                )
            }
            # State visuals are directly usable objects, independent of passive Skills.
            # Keep the action local (two Tiles), and never turn visual states into
            # a whitelist of legal business state values.
            agent = self.game.get_agent(self.iteration.agent_key)
            vision = max(0, int((getattr(agent, "percept_config", {}) or {}).get("vision_r", 4)))
            for node in self.game.maze.semantic_nodes_in_scope(self.iteration.coord, min(2, vision)):
                if node.get("kind") != "GAME_OBJECT" or not node.get("available_visual_states") or node.get("skill_bindings"):
                    continue
                bounds = node["bounds"]
                x, y = self.iteration.coord
                nearest = (
                    min(max(x, bounds["x"]), bounds["x"] + bounds["width"] - 1),
                    min(max(y, bounds["y"]), bounds["y"] + bounds["height"] - 1),
                )
                if math.dist((x, y), nearest) <= 2:
                    nearby_keys.add(str(node["id"]))
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

    def _resolve_path(self, payload: Mapping[str, Any], *, navigation=False) -> tuple[tuple[int, int], ...]:
        current = tuple(self.iteration.coord)
        target_coord = payload.get("target_coord")
        target_address = payload.get("target_address")
        target_node_id = str(payload.get("target_node_id") or "").strip()
        normalized_address: tuple[str, ...] = ()
        if target_address is not None:
            if not isinstance(target_address, (list, tuple)):
                raise ValueError("target_address must be a spatial address array")
            normalized_address = tuple(str(part).strip() for part in target_address)
            if not normalized_address or any(not part for part in normalized_address):
                raise ValueError("target_address must not contain empty levels")
            if len(normalized_address) > 4:
                raise ValueError("target_address cannot exceed four spatial levels")
        candidates: list[tuple[int, int]] = []
        if target_coord is not None:
            if (not isinstance(target_coord, (list, tuple)) or len(target_coord) != 2
                    or any(type(v) is not int for v in target_coord)):
                raise ValueError("target_coord must contain exactly two integers")
            candidate = (int(target_coord[0]), int(target_coord[1]))
            if not (
                0 <= candidate[0] < self.game.maze.width_tiles
                and 0 <= candidate[1] < self.game.maze.height_tiles
            ):
                raise ValueError("target_coord is outside the map")
            if self.game.maze.tile_at(candidate).collision:
                raise ValueError("target_coord is not traversable")
            if normalized_address:
                address_coords = set(
                    self.game.maze.get_address_tiles(normalized_address)
                )
                if candidate not in address_coords:
                    raise ValueError(
                        "target_coord does not belong to target_address"
                    )
            candidates = [candidate]
        elif normalized_address:
            candidates = sorted(
                self.game.maze.get_address_tiles(normalized_address)
            )
        else:
            raise ValueError("MOVE requires target_address or target_coord")
        if target_node_id:
            candidates = [
                candidate
                for candidate in candidates
                if target_node_id
                in {
                    str(item.get("id") or "")
                    for item in (
                        self.game.maze.tile_at(candidate).spatial_semantics or ()
                    )
                }
            ]
            if not candidates:
                raise ValueError(
                    "target_node_id does not exist at the MOVE destination"
                )
        if navigation and current in candidates and not self.game.maze.tile_at(current).collision:
            return ()
        if target_coord is None and current in candidates:
            raise ValueError(
                "MOVE target_address is already the current location; "
                "use ACT or WAIT, or provide a different target_coord"
            )
        routes = []
        for candidate in candidates:
            if candidate == current:
                continue
            route = tuple(
                tuple(coord) for coord in self.game.maze.find_path(current, candidate)
            )
            if route:
                routes.append((candidate, route[1:] if route[0] == current else route))
        if not routes:
            if navigation:
                raise ValueError("navigation target is unreachable")
            raise ValueError(
                "MOVE target is the current coordinate or is unreachable; "
                "use ACT or WAIT when no displacement is intended"
            )
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
