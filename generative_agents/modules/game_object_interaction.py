"""Object identities, interaction inboxes and committed state owned by one Run."""

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
    description: str
    default_request: str
    interaction_radius_tiles: float
    coord: tuple[float, float]
    bounds: tuple[float, float, float, float]
    address: tuple[str, ...]
    object_state: Mapping[str, Any]
    vision_radius: int = 4
    attention_bandwidth: int = 8

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
    """Own object bindings, state and requests without executing model code."""

    def __init__(self, world: Mapping[str, Any], *, clock) -> None:
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            world: 当前运行使用的世界配置或运行时世界对象。 类型：`Mapping[str, Any]`。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。

        返回:
            无返回值。
        """
        self._clock = clock
        self._affordances = tuple(self._from_world(world))
        keys = [item.object_key for item in self._affordances]
        if len(keys) != len(set(keys)):
            raise ValueError("each Game Object must bind exactly one root Skill")
        for item in self._affordances:
            if not 0 <= item.vision_radius <= 100 or not 0 <= item.attention_bandwidth <= 100:
                raise ValueError("Game Object perception limits must be between 0 and 100")
        self._runtime_state = {
            key: {"requests": [], "last_action": None, "action_keys": {},
                  "last_step": 0, "consecutive_fallbacks": 0}
            for key in keys
        }
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
        """Validate an interaction; execution waits for the object's own turn."""

        nearby = self.nearby(tuple(agent.coord))
        selected = next(
            (item for item in nearby if item.selection_key == selection_key),
            None,
        )
        if selected is None:
            raise ValueError(
                f"Game Object interaction is not available nearby: {selection_key}"
            )
        request = str(request or selected.default_request).strip()
        if not request:
            raise ValueError("Game Object interaction request cannot be empty")
        return {
            "object_key": selected.object_key,
            "object_name": selected.object_name,
            "interaction_key": selected.interaction_key,
            "skill_name": selected.skill_name,
            "observed_step": step_no,
            "observed_at": self._clock.get_date().isoformat(),
            "request": request,
            "agent_key": agent.agent_key,
            "agent_name": agent.name,
            "coord": list(agent.coord),
            "agent_decision": "PENDING",
        }

    def binding(self, object_key: str) -> GameObjectAffordance:
        for item in self._affordances:
            if item.object_key == object_key:
                return item
        raise ValueError(f"Game Object has no bound Skill: {object_key}")

    def has_skill(self, object_key: str) -> bool:
        return object_key in self._runtime_state

    def runtime_state(self, object_key: str) -> dict[str, Any]:
        return copy.deepcopy(self._runtime_state[object_key])

    def enqueue_request(self, observation: Mapping[str, Any], request_id: str) -> dict:
        value = {**copy.deepcopy(dict(observation)), "request_id": request_id}
        pending = self._runtime_state[str(value["object_key"])]["requests"]
        if any(item["request_id"] == request_id for item in pending):
            raise ValueError("interaction request is already committed")
        pending.append(value)
        return copy.deepcopy(value)

    def commit_iteration(self, object_key: str, *, step_no: int, action: Mapping,
                         responses: list[dict], fallback: bool) -> None:
        state = self._runtime_state[object_key]
        if step_no <= state["last_step"]:
            raise ValueError("Game Object already committed this Step")
        key = str((action.get("arguments") or {}).get("idempotency_key") or "")
        if key:
            if key in state["action_keys"]:
                raise ValueError("Game Object action idempotency key is already committed")
            state["action_keys"][key] = step_no
        replied = {item["request_id"] for item in responses}
        state["requests"] = [item for item in state["requests"] if item["request_id"] not in replied]
        state["last_action"] = {"step_no": step_no, **copy.deepcopy(dict(action))}
        state["last_step"] = step_no
        state["consecutive_fallbacks"] = state["consecutive_fallbacks"] + 1 if fallback else 0

    def snapshot_runtime(self) -> dict:
        return copy.deepcopy(self._runtime_state)

    def restore_runtime(self, snapshot: Mapping, *, agent_keys: set[str]) -> None:
        if not isinstance(snapshot, Mapping) or set(snapshot) != set(self._runtime_state):
            raise ValueError("checkpoint Game Object runtime keys do not match the map")
        for key, value in snapshot.items():
            if (not isinstance(value, Mapping) or type(value.get("last_step")) is not int
                    or value["last_step"] < 0 or not isinstance(value.get("action_keys"), dict)
                    or not isinstance(value.get("requests"), list)
                    or type(value.get("consecutive_fallbacks")) is not int
                    or value["consecutive_fallbacks"] < 0):
                raise ValueError("invalid Game Object runtime checkpoint")
            for action_key, step in value["action_keys"].items():
                if not isinstance(action_key, str) or not action_key or type(step) is not int or not 1 <= step <= value["last_step"]:
                    raise ValueError("invalid Game Object activity ledger checkpoint")
            last = value.get("last_action")
            if ((value["last_step"] == 0 and last is not None)
                    or (value["last_step"] > 0 and (not isinstance(last, Mapping) or last.get("step_no") != value["last_step"]))):
                raise ValueError("Game Object last action disagrees with its checkpoint Step")
            ids = set()
            for request in value["requests"]:
                if (not isinstance(request, Mapping) or request.get("object_key") != key
                        or request.get("agent_key") not in agent_keys
                        or not request.get("request_id") or request["request_id"] in ids):
                    raise ValueError("invalid Game Object request checkpoint")
                ids.add(request["request_id"])
        self._runtime_state = copy.deepcopy(dict(snapshot))

    def public_state(self, object_key: str) -> dict:
        state = self.object_state(object_key)
        # An autonomous object's parameters/ledger are not visible merely because
        # someone can see its appearance. The visual state label is public.
        return {"state": state["state"]} if self.has_skill(object_key) and "state" in state else (
            {} if self.has_skill(object_key) else state
        )

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
            asset_id = str(placement.get("spatial_asset_id") or "")
            contract = assets.get(asset_id)
            if not isinstance(contract, Mapping) or contract.get("kind") != "OBJECT":
                continue
            object_key = str(placement.get("instance_key") or asset_id)
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
                    interaction_key=str(binding.get("interaction_key") or "interact"),
                    skill_name=str(binding.get("skill_name") or ""),
                    description=str(binding.get("description") or "与对象交互"),
                    default_request=str(binding.get("default_request") or "请提供当前状态和可执行信息。"),
                    interaction_radius_tiles=float(
                        binding.get("interaction_radius_tiles", 2.0)
                    ),
                    coord=(x + width / 2.0, y + height / 2.0),
                    bounds=(x, y, width, height),
                    address=address(node),
                    object_state=copy.deepcopy(dict(state)),
                    vision_radius=int(binding.get("vision_radius", 4)),
                    attention_bandwidth=int(binding.get("attention_bandwidth", 8)),
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
        editor = world.get("editor") or {}
        assets = editor.get("spatial_assets") if isinstance(editor, Mapping) else {}
        assets = assets if isinstance(assets, Mapping) else {}
        for placement in scene.get("placements") or ():
            if not isinstance(placement, Mapping):
                continue
            asset_id = str(placement.get("spatial_asset_id") or "")
            contract = assets.get(asset_id)
            if not isinstance(contract, Mapping) or contract.get("kind") != "OBJECT":
                continue
            x = float(placement.get("x_tiles", 0))
            y = float(placement.get("y_tiles", 0))
            physics = contract.get("physics") or {}
            width = max(1.0, float(physics.get("width_tiles", 1)))
            height = max(1.0, float(physics.get("height_tiles", 1)))
            state = copy.deepcopy(dict(contract.get("initial_state") or {}))
            state.update(copy.deepcopy(dict(placement.get("state_overrides") or {})))
            for binding in contract.get("skill_bindings") or ():
                if not isinstance(binding, Mapping):
                    continue
                yield GameObjectAffordance(
                    object_key=str(placement.get("instance_key") or asset_id),
                    object_name=str(
                        contract.get("name") or placement.get("instance_key")
                    ),
                    interaction_key=str(binding.get("interaction_key") or "interact"),
                    skill_name=str(binding.get("skill_name") or ""),
                    description=str(binding.get("description") or "与对象交互"),
                    default_request=str(binding.get("default_request") or "请提供当前状态和可执行信息。"),
                    interaction_radius_tiles=float(
                        binding.get("interaction_radius_tiles", 2.0)
                    ),
                    coord=(x, y),
                    bounds=(
                        x - (width - 1.0) / 2.0,
                        y - (height - 1.0) / 2.0,
                        width,
                        height,
                    ),
                    address=(),
                    object_state=state,
                    vision_radius=int(binding.get("vision_radius", 4)),
                    attention_bandwidth=int(binding.get("attention_bandwidth", 8)),
                )


__all__ = ["GameObjectAffordance", "GameObjectInteractionSystem"]
