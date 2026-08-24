"""Agent-initiated, passive interaction with nearby map Game Objects."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class GameObjectAffordance:
    object_key: str
    object_name: str
    interaction_key: str
    skill_name: str
    skill_revision: str | None
    description: str
    default_request: str
    interaction_radius_tiles: float
    coord: tuple[float, float]
    bounds: tuple[float, float, float, float]
    address: tuple[str, ...]
    object_state: Mapping[str, Any]

    @property
    def selection_key(self) -> str:
        return f"{self.object_key}/{self.interaction_key}"

    def distance_to(self, coord: tuple[int, int]) -> float:
        x, y, width, height = self.bounds
        nearest_x = min(max(float(coord[0]), x), x + max(0.0, width - 1.0))
        nearest_y = min(max(float(coord[1]), y), y + max(0.0, height - 1.0))
        return math.dist((float(coord[0]), float(coord[1])), (nearest_x, nearest_y))

    def as_agent_context(self, coord: tuple[int, int]) -> dict[str, Any]:
        return {
            "selection_key": self.selection_key,
            "object_key": self.object_key,
            "object_name": self.object_name,
            "interaction_key": self.interaction_key,
            "skill_name": self.skill_name,
            "description": self.description,
            "distance_tiles": round(self.distance_to(coord), 3),
            "address": list(self.address),
        }


class GameObjectInteractionSystem:
    """Expose nearby affordances and invoke one only after Agent selection."""

    def __init__(self, world: Mapping[str, Any], *, skill_executor, clock) -> None:
        self._executor = skill_executor
        self._clock = clock
        self._affordances = tuple(self._from_world(world))

    @property
    def affordances(self) -> tuple[GameObjectAffordance, ...]:
        return self._affordances

    def nearby(self, coord: tuple[int, int]) -> list[GameObjectAffordance]:
        return sorted(
            (
                item
                for item in self._affordances
                if item.distance_to(coord) <= item.interaction_radius_tiles
            ),
            key=lambda item: (
                item.distance_to(coord),
                item.object_key,
                item.interaction_key,
            ),
        )

    def interact(self, agent, planned_path, *, step_no: int) -> dict[str, Any] | None:
        """Ask the Agent to choose; proximity alone never runs an object Skill."""

        nearby = self.nearby(tuple(agent.coord))
        if not nearby or self._executor is None:
            return None
        options = [item.as_agent_context(tuple(agent.coord)) for item in nearby]
        selected_key = agent.choose_game_object_interaction(options, planned_path)
        if not selected_key or selected_key == "NONE":
            return None
        selected = next(
            (item for item in nearby if item.selection_key == selected_key),
            None,
        )
        if selected is None:
            return None

        request = selected.default_request
        context = {
            "interaction_mode": "PASSIVE_REQUEST_RESPONSE",
            "step_no": step_no,
            "virtual_time": self._clock.get_date().isoformat(),
            "agent": {
                "agent_key": agent.agent_key,
                "name": agent.name,
                "coord": list(agent.coord),
                "current_action": agent.get_event().get_describe(),
            },
            "game_object": {
                "object_key": selected.object_key,
                "name": selected.object_name,
                "coord": list(selected.coord),
                "address": list(selected.address),
            },
            "object_state": copy.deepcopy(dict(selected.object_state)),
        }
        result = self._executor.run(
            selected.skill_name,
            request,
            context=context,
        )
        directive = agent.receive_game_object_observation(
            object_key=selected.object_key,
            object_name=selected.object_name,
            interaction_key=selected.interaction_key,
            skill_name=result.skill,
            skill_revision=result.revision,
            request=request,
            response=result.output_text,
            address=selected.address,
        )
        return {
            "object_key": selected.object_key,
            "object_name": selected.object_name,
            "interaction_key": selected.interaction_key,
            "skill_name": result.skill,
            "skill_revision": result.revision,
            "request": request,
            "response": result.output_text,
            "agent_decision": directive,
            "trace": list(result.trace),
        }

    @staticmethod
    def _from_world(world: Mapping[str, Any]):
        yield from GameObjectInteractionSystem._from_editor_v2(world)
        yield from GameObjectInteractionSystem._from_spatial_scene(world)

    @staticmethod
    def _from_editor_v2(world: Mapping[str, Any]):
        editor = world.get("editor_v2")
        if not isinstance(editor, Mapping):
            return
        nodes = {
            str(item.get("id")): item
            for item in editor.get("hierarchy_nodes", ())
            if isinstance(item, Mapping) and item.get("id")
        }

        def address(node: Mapping[str, Any]) -> tuple[str, ...]:
            parts: list[str] = []
            current: Mapping[str, Any] | None = node
            seen: set[str] = set()
            while current is not None:
                current_id = str(current.get("id") or "")
                if not current_id or current_id in seen:
                    break
                seen.add(current_id)
                parts.append(str(current.get("name") or current_id))
                parent_id = str(current.get("parent_id") or "")
                current = nodes.get(parent_id) if parent_id else None
            return tuple(reversed(parts))

        for node in nodes.values():
            if node.get("kind") != "GAME_OBJECT":
                continue
            bounds = node.get("bounds") or {}
            x = float(bounds.get("x", 0))
            y = float(bounds.get("y", 0))
            width = max(1.0, float(bounds.get("width", 1)))
            height = max(1.0, float(bounds.get("height", 1)))
            state = (node.get("extensions") or {}).get("state") or {}
            for binding in node.get("skill_bindings") or ():
                if not isinstance(binding, Mapping):
                    continue
                yield GameObjectAffordance(
                    object_key=str(node["id"]),
                    object_name=str(node.get("name") or node["id"]),
                    interaction_key=str(binding.get("interaction_key") or ""),
                    skill_name=str(binding.get("skill_name") or ""),
                    skill_revision=None,
                    description=str(binding.get("description") or ""),
                    default_request=str(binding.get("default_request") or ""),
                    interaction_radius_tiles=float(
                        binding.get("interaction_radius_m", 2.0)
                    ),
                    coord=(x + width / 2.0, y + height / 2.0),
                    bounds=(x, y, width, height),
                    address=address(node),
                    object_state=copy.deepcopy(dict(state)),
                )

    @staticmethod
    def _from_spatial_scene(world: Mapping[str, Any]):
        scene = world.get("spatial_scene")
        if not isinstance(scene, Mapping):
            return
        meters_per_tile = max(0.000001, float(scene.get("meters_per_tile", 1.0)))
        editor = world.get("editor") or {}
        assets = editor.get("spatial_assets") if isinstance(editor, Mapping) else {}
        assets = assets if isinstance(assets, Mapping) else {}
        for placement in scene.get("placements") or ():
            if not isinstance(placement, Mapping):
                continue
            revision_id = str(placement.get("spatial_asset_revision_id") or "")
            contract = assets.get(revision_id)
            if not isinstance(contract, Mapping) or contract.get("kind") != "OBJECT":
                continue
            x = float(placement.get("x_m", 0)) / meters_per_tile
            y = float(placement.get("y_m", 0)) / meters_per_tile
            state = copy.deepcopy(dict(contract.get("initial_state") or {}))
            state.update(copy.deepcopy(dict(placement.get("state_overrides") or {})))
            for binding in contract.get("skill_bindings") or ():
                if not isinstance(binding, Mapping):
                    continue
                yield GameObjectAffordance(
                    object_key=str(placement.get("instance_key") or revision_id),
                    object_name=str(contract.get("name") or placement.get("instance_key")),
                    interaction_key=str(binding.get("interaction_key") or ""),
                    skill_name=str(binding.get("skill_name") or ""),
                    skill_revision=None,
                    description=str(binding.get("description") or ""),
                    default_request=str(binding.get("default_request") or ""),
                    interaction_radius_tiles=(
                        float(binding.get("interaction_radius_m", 2.0))
                        / meters_per_tile
                    ),
                    coord=(x, y),
                    bounds=(x, y, 1.0, 1.0),
                    address=(),
                    object_state=state,
                )


__all__ = ["GameObjectAffordance", "GameObjectInteractionSystem"]
