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

    def tools(self) -> list[dict[str, Any]]:
        tools = [
            {
                "name": "world-perceive",
                "description": (
                    "Observe spatial semantics, events, nearby Agents, and nearby "
                    "Game Objects without changing the simulation world."
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
                        },
                        "target_coord": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
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
        radius = int(
            arguments.get("radius_tiles")
            if arguments.get("radius_tiles") is not None
            else getattr(agent, "percept_config", {}).get("vision_r", 4)
        )
        radius = max(0, min(radius, 100))
        center = tuple(agent.coord)
        tiles = []
        x_min = max(0, center[0] - radius)
        x_max = min(self.game.maze.maze_width - 1, center[0] + radius)
        y_min = max(0, center[1] - radius)
        y_max = min(self.game.maze.maze_height - 1, center[1] + radius)
        for y in range(y_min, y_max + 1):
            for x in range(x_min, x_max + 1):
                tile = self.game.maze.tile_at((x, y))
                events = [event.to_dict() for event in tile.get_events()]
                semantics = [
                    copy.deepcopy(dict(item))
                    for item in getattr(tile, "spatial_semantics", ())
                ]
                if tile.address != [tile.address[0]] or events or semantics:
                    tiles.append(
                        {
                            "coord": [x, y],
                            "address": list(tile.address),
                            "collision": bool(tile.collision),
                            "spatial_semantics": semantics,
                            "events": events,
                        }
                    )
        nearby_agents = [
            {
                "agent_key": key,
                "name": other.name,
                "coord": list(other.coord),
                "address": list(other.get_tile().get_address()),
                "current_action": other.get_event().to_dict(),
            }
            for key, other in sorted(self.game.agents.items())
            if key != self.iteration.agent_key
            and max(abs(other.coord[0] - center[0]), abs(other.coord[1] - center[1]))
            <= radius
        ]
        objects = [
            {
                **item.as_agent_context(center),
                "state": copy.deepcopy(dict(item.object_state)),
            }
            for item in self.game.game_object_interactions.nearby(center)
        ]
        return {
            "now": self.iteration.now.isoformat(),
            "agent": self.iteration.as_dict()["agent"],
            "radius_tiles": radius,
            "tiles": tiles,
            "nearby_agents": nearby_agents,
            "game_objects": objects,
            "active_conversations": list(
                getattr(
                    self.game,
                    "active_conversations_for",
                    lambda _agent_key: (),
                )(self.iteration.agent_key)
            ),
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
            payload["target_coord"] = list(path[-1]) if path else list(self.iteration.coord)
        elif action_type == "ACT":
            predicate = str(payload.get("predicate") or "").strip()
            object_value = str(payload.get("object") or "").strip()
            if not predicate or not object_value:
                raise ValueError(
                    "ACT requires non-empty Event predicate and object"
                )
            payload["predicate"] = predicate
            payload["object"] = object_value
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
            payload["conversation_id"] = str(
                payload.get("conversation_id") or ""
            ).strip() or None
            payload["start_new_conversation"] = bool(
                payload.get("start_new_conversation", False)
            )
            payload["end_conversation"] = bool(
                payload.get("end_conversation", False)
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
            route = tuple(tuple(coord) for coord in self.game.maze.find_path(current, candidate))
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
