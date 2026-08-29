"""Agent-initiated, passive interaction with nearby map Game Objects."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class GameObjectAffordance:
    """智能体当前可以感知并主动选择的一项游戏对象交互机会。"""

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
        """执行 `GameObjectAffordance` 的`selection``key`操作。

        返回:
            返回处理后的文本或稳定标识。
        """
        return f"{self.object_key}/{self.interaction_key}"

    def distance_to(self, coord: tuple[int, int]) -> float:
        """执行 `GameObjectAffordance` 的`distance``to`操作。

        参数:
            coord: 地图坐标，按 `(行, 列)` 或项目约定的二维顺序表示。 类型：`tuple[int, int]`。

        返回:
            返回计算得到的浮点数值。
        """
        x, y, width, height = self.bounds
        nearest_x = min(max(float(coord[0]), x), x + max(0.0, width - 1.0))
        nearest_y = min(max(float(coord[1]), y), y + max(0.0, height - 1.0))
        return math.dist((float(coord[0]), float(coord[1])), (nearest_x, nearest_y))

    def as_agent_context(self, coord: tuple[int, int]) -> dict[str, Any]:
        """执行 `GameObjectAffordance` 的`as`智能体运行上下文操作。

        参数:
            coord: 地图坐标，按 `(行, 列)` 或项目约定的二维顺序表示。 类型：`tuple[int, int]`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
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
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            world: 当前运行使用的世界配置或运行时世界对象。 类型：`Mapping[str, Any]`。
            skill_executor: 执行运行私有技能调用的适配器。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。

        返回:
            无返回值。
        """
        self._executor = skill_executor
        self._clock = clock
        self._affordances = tuple(self._from_world(world))
        self._object_states = {
            object_key: copy.deepcopy(dict(initial_state))
            for object_key, initial_state in self._initial_states_from_world(world)
        }
        for item in self._affordances:
            self._object_states.setdefault(
                item.object_key, copy.deepcopy(dict(item.object_state))
            )

    @property
    def affordances(self) -> tuple[GameObjectAffordance, ...]:
        """执行 `GameObjectInteractionSystem` 的`affordances`操作。

        返回:
            返回按接口约定组织的结果集合。
        """
        return self._affordances

    def nearby(self, coord: tuple[int, int]) -> list[GameObjectAffordance]:
        """执行 `GameObjectInteractionSystem` 的`nearby`操作。

        参数:
            coord: 地图坐标，按 `(行, 列)` 或项目约定的二维顺序表示。 类型：`tuple[int, int]`。

        返回:
            返回按接口约定组织的结果集合。
        """
        return sorted(
            (
                replace(
                    item,
                    object_state=copy.deepcopy(
                        self._object_states.get(item.object_key, {})
                    ),
                )
                for item in self._affordances
                if item.distance_to(coord) <= item.interaction_radius_tiles
            ),
            key=lambda item: (
                item.distance_to(coord),
                item.object_key,
                item.interaction_key,
            ),
        )

    def interact_selected(
        self,
        agent,
        selection_key: str,
        *,
        step_no: int,
        request: str | None = None,
    ) -> dict[str, Any]:
        """Invoke exactly one nearby Game Object passive Skill selected by the Agent."""

        nearby = self.nearby(tuple(agent.coord))
        selected = next(
            (item for item in nearby if item.selection_key == selection_key),
            None,
        )
        if selected is None:
            raise ValueError(
                f"Game Object interaction is not available nearby: {selection_key}"
            )
        if self._executor is None:
            raise ValueError("Game Object passive Skill runtime is unavailable")
        request = str(request or selected.default_request).strip()
        if not request:
            raise ValueError("Game Object interaction request cannot be empty")
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
        return {
            "object_key": selected.object_key,
            "object_name": selected.object_name,
            "interaction_key": selected.interaction_key,
            "skill_name": result.skill,
            "skill_revision": result.revision,
            "observed_step": step_no,
            "observed_at": self._clock.get_date().isoformat(),
            "request": request,
            "response": result.output_text,
            "agent_decision": "COMPLETED",
            "trace": list(result.trace),
        }

    def object_state(self, object_key: str) -> dict[str, Any]:
        """Return a defensive copy of one replayable Game Object state."""

        if object_key not in self._object_states:
            raise ValueError(f"Game Object does not exist: {object_key}")
        return copy.deepcopy(self._object_states[object_key])

    def apply_state_patch(
        self, object_key: str, patch: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Apply a shallow state patch and return exact before/after snapshots."""

        if object_key not in self._object_states:
            raise ValueError(f"Game Object does not exist: {object_key}")
        if not isinstance(patch, Mapping) or not patch:
            raise ValueError("Game Object state patch cannot be empty")
        before = copy.deepcopy(self._object_states[object_key])
        after = copy.deepcopy(before)
        after.update(copy.deepcopy(dict(patch)))
        self._object_states[object_key] = after
        return before, copy.deepcopy(after)

    def snapshot_state(self) -> dict[str, dict[str, Any]]:
        """Return all mutable Game Object state for checkpoints and replay."""

        return copy.deepcopy(self._object_states)

    def restore_state(self, snapshot: Mapping[str, Mapping[str, Any]]) -> None:
        """Restore Game Object state from a verified checkpoint snapshot."""

        expected = set(self._object_states)
        received = {str(key) for key in snapshot}
        if received != expected:
            raise ValueError("checkpoint Game Object state keys do not match the map")
        self._object_states = {
            str(key): copy.deepcopy(dict(value)) for key, value in snapshot.items()
        }

    @staticmethod
    def _from_world(world: Mapping[str, Any]):
        """执行`from`世界的内部处理，供当前模块或类复用。

        参数:
            world: 当前运行使用的世界配置或运行时世界对象。 类型：`Mapping[str, Any]`。

        返回:
            无返回值。
        """
        yield from GameObjectInteractionSystem._from_editor_v2(world)
        yield from GameObjectInteractionSystem._from_spatial_scene(world)

    @staticmethod
    def _initial_states_from_world(world: Mapping[str, Any]):
        """Yield every Game Object state, including objects without a Skill."""

        editor = world.get("editor_v2")
        if isinstance(editor, Mapping):
            for node in editor.get("hierarchy_nodes", ()):
                if not isinstance(node, Mapping) or node.get("kind") != "GAME_OBJECT":
                    continue
                object_key = str(node.get("id") or "")
                if object_key:
                    yield object_key, copy.deepcopy(
                        dict(node.get("initial_state") or {})
                    )

        scene = world.get("spatial_scene")
        if not isinstance(scene, Mapping):
            return
        legacy_editor = world.get("editor") or {}
        assets = (
            legacy_editor.get("spatial_assets")
            if isinstance(legacy_editor, Mapping)
            else {}
        )
        assets = assets if isinstance(assets, Mapping) else {}
        for placement in scene.get("placements") or ():
            if not isinstance(placement, Mapping):
                continue
            revision_id = str(placement.get("spatial_asset_revision_id") or "")
            contract = assets.get(revision_id)
            if not isinstance(contract, Mapping) or contract.get("kind") != "OBJECT":
                continue
            object_key = str(placement.get("instance_key") or revision_id)
            state = copy.deepcopy(dict(contract.get("initial_state") or {}))
            state.update(copy.deepcopy(dict(placement.get("state_overrides") or {})))
            if object_key:
                yield object_key, state

    @staticmethod
    def _from_editor_v2(world: Mapping[str, Any]):
        """执行`from``editor``v2`的内部处理，供当前模块或类复用。

        参数:
            world: 当前运行使用的世界配置或运行时世界对象。 类型：`Mapping[str, Any]`。

        返回:
            无返回值。
        """
        editor = world.get("editor_v2")
        if not isinstance(editor, Mapping):
            return
        nodes = {
            str(item.get("id")): item
            for item in editor.get("hierarchy_nodes", ())
            if isinstance(item, Mapping) and item.get("id")
        }

        def address(node: Mapping[str, Any]) -> tuple[str, ...]:
            """执行 `GameObjectInteractionSystem` 的`address`操作。

            参数:
                node: 当前遍历、校验或转换的树节点。 类型：`Mapping[str, Any]`。

            返回:
                返回按接口约定组织的结果集合。
            """
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
            state = node.get("initial_state") or {}
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
        """执行`from`空间数据`scene`的内部处理，供当前模块或类复用。

        参数:
            world: 当前运行使用的世界配置或运行时世界对象。 类型：`Mapping[str, Any]`。

        返回:
            无返回值。
        """
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
                    object_name=str(
                        contract.get("name") or placement.get("instance_key")
                    ),
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
