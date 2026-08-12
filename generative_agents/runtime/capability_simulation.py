"""Continuous scene state and StepResult runner for capability compositions."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Mapping, Protocol

from generative_agents.config import WorkflowDefinition
from generative_agents.config.capabilities import CapabilityContract
from generative_agents.config.scenarios import ExperimentCapabilityExtension
from generative_agents.config.spatial_assets import SpatialAssetContract
from generative_agents.config.tools import ToolContract

from .capability_engine import CapabilityRuntimeEngine
from .results import (
    ActionSnapshot,
    ActivityKind,
    AgentStepResult,
    DomainEventRecord,
    StepResultBuilder,
    deterministic_record_id,
)
from .workflow_engine import WorkflowExecutor


class StepCommitter(Protocol):
    def commit(self, result, *, force_checkpoint: bool): ...


@dataclass(slots=True)
class ContinuousEntity:
    entity_ref: str
    entity_kind: str
    x_m: float
    y_m: float
    heading_degrees: float
    radius_m: float
    speed_mps: float = 0.0
    route: list[dict[str, float]] = field(default_factory=list)
    route_index: int = 0

    def motion(self) -> dict[str, Any]:
        heading = math.radians(self.heading_degrees)
        return {
            "entity_ref": self.entity_ref,
            "entity_kind": self.entity_kind,
            "x_m": self.x_m,
            "y_m": self.y_m,
            "heading_degrees": self.heading_degrees,
            "speed_mps": self.speed_mps,
            "velocity_x_mps": self.speed_mps * math.cos(heading),
            "velocity_y_mps": self.speed_mps * math.sin(heading),
            "radius_m": self.radius_m,
            "route_index": self.route_index,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.motion(),
            "route": self.route,
        }


class CapabilityScene:
    """Map/tool/Agent state exposed to generic capability handlers."""

    def __init__(
        self,
        snapshot: Mapping[str, Any],
        context: Any,
        *,
        workflows: Mapping[str, WorkflowDefinition] | None = None,
        workflow_functions: Mapping[str, str] | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.context = context
        self.extension = ExperimentCapabilityExtension.model_validate(
            snapshot["experiment_extension"]
        )
        self.entities: dict[str, ContinuousEntity] = {}
        self.actor_entities: dict[str, str] = {}
        self.tool_entities: dict[str, str] = {}
        self.actor_roles = {actor.actor_key: actor.role for actor in self.extension.actors}
        self.actor_active_tools = {
            actor.actor_key: actor.active_tool_instance_key
            for actor in self.extension.actors
            if actor.active_tool_instance_key
        }
        self.placements: dict[str, dict[str, Any]] = {}
        self.metrics: dict[str, dict[str, Any]] = {}
        self.collision_count = 0
        self.last_decisions: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._trajectory: list[dict[str, Any]] = []
        self._last_task_time: dict[str, int] = {}
        self._task_intervals: dict[str, int] = {}
        self._current_elapsed_ms = 0
        self._channel_values: dict[str, Any] = {}
        self._engine: CapabilityRuntimeEngine | None = None
        self._workflow_executor = WorkflowExecutor(
            workflows or {}, function_sources=workflow_functions or {}
        )
        self._build_entities()
        self._build_placements()

    def bind_engine(self, engine: CapabilityRuntimeEngine) -> None:
        self._engine = engine

    def _build_entities(self) -> None:
        for actor in self.extension.actors:
            entity_ref = f"actor:{actor.actor_key}"
            entity = ContinuousEntity(
                entity_ref=entity_ref,
                entity_kind=actor.role,
                x_m=actor.initial_pose.x_m,
                y_m=actor.initial_pose.y_m,
                heading_degrees=actor.initial_pose.heading_degrees,
                radius_m=actor.radius_m,
                route=[pose.model_dump(mode="json") for pose in actor.route],
            )
            self.entities[entity_ref] = entity
            self.actor_entities[actor.actor_key] = entity_ref
        tools = self.snapshot["tools"]
        for tool_instance in self.extension.tool_instances:
            contract = ToolContract.model_validate(
                tools[tool_instance.tool_revision_id]["contract"]
            )
            entity_ref = f"tool:{tool_instance.instance_key}"
            entity = ContinuousEntity(
                entity_ref=entity_ref,
                entity_kind=contract.kind,
                x_m=tool_instance.initial_pose.x_m,
                y_m=tool_instance.initial_pose.y_m,
                heading_degrees=tool_instance.initial_pose.heading_degrees,
                radius_m=tool_instance.radius_m,
                speed_mps=float(tool_instance.state_overrides.get("speed_mps", 0)),
                route=[pose.model_dump(mode="json") for pose in tool_instance.route],
            )
            self.entities[entity_ref] = entity
            self.tool_entities[tool_instance.instance_key] = entity_ref
            operator = tool_instance.operator_actor_key
            if operator:
                self._sync_operator(operator, entity)

    def _build_placements(self) -> None:
        world = self.snapshot["map_revision"]["world"]
        scene = (world.get("definition") or {}).get("spatial_scene") or {}
        spatial_assets = self.snapshot["spatial_assets"]
        for placement in scene.get("placements") or []:
            revision_id = placement["spatial_asset_revision_id"]
            contract = SpatialAssetContract.model_validate(
                spatial_assets[revision_id]["contract"]
            )
            state = dict(contract.initial_state)
            state.update(placement.get("state_overrides") or {})
            self.placements[placement["instance_key"]] = {
                "instance_key": placement["instance_key"],
                "x_m": float(placement["x_m"]),
                "y_m": float(placement["y_m"]),
                "rotation_degrees": float(placement.get("rotation_degrees", 0)),
                "width_m": contract.physics.width_m,
                "height_m": contract.physics.height_m,
                "kind": contract.kind,
                "tags": list(contract.semantics.tags),
                "state": state,
            }

    def set_task_intervals(self, engine: CapabilityRuntimeEngine) -> None:
        self._task_intervals = {
            key: int(instance.definition.run_policy.interval_ms or self.extension.clock.base_tick_ms)
            for key, instance in engine.instances.items()
        }
        for key, instance in engine.instances.items():
            if instance.definition.run_policy.trigger == "DECISION":
                actor_key = instance.target_ref.split(":", 1)[-1]
                actor = next(
                    (item for item in self.extension.actors if item.actor_key == actor_key),
                    None,
                )
                self._task_intervals[key] = (
                    actor.reasoning_interval_ms
                    if actor is not None
                    else self.extension.clock.base_tick_ms
                )

    def delta_ms(self, task_key: str, virtual_time_ms: int) -> int:
        previous = self._last_task_time.get(task_key)
        self._last_task_time[task_key] = virtual_time_ms
        if previous is None:
            return self._task_intervals.get(task_key, self.extension.clock.base_tick_ms)
        return max(self.extension.clock.base_tick_ms, virtual_time_ms - previous)

    def _entity(self, target_ref: str) -> ContinuousEntity:
        normalized = target_ref
        if normalized.startswith("agent:"):
            normalized = "actor:" + normalized.split(":", 1)[1]
        entity = self.entities.get(normalized)
        if entity is None:
            raise KeyError(f"target is not a movable entity: {target_ref}")
        return entity

    def set_route(self, target_ref: str, route: Any) -> None:
        entity = self._entity(target_ref)
        if isinstance(route, Mapping):
            route = route.get("points") or route.get("route") or []
        normalized = [
            {
                "x_m": float(point["x_m"]),
                "y_m": float(point["y_m"]),
                "heading_degrees": float(point.get("heading_degrees", 0)),
            }
            for point in (route or [])
        ]
        if normalized and normalized != entity.route:
            entity.route = normalized
            entity.route_index = 0

    def advance_target(
        self,
        target_ref: str,
        *,
        target_speed_mps: float,
        max_acceleration_mps2: float,
        max_deceleration_mps2: float,
        delta_ms: int,
    ) -> dict[str, Any]:
        entity = self._entity(target_ref)
        dt = delta_ms / 1000.0
        old_speed = entity.speed_mps
        if target_speed_mps >= old_speed:
            entity.speed_mps = min(
                target_speed_mps, old_speed + max_acceleration_mps2 * dt
            )
        else:
            entity.speed_mps = max(
                target_speed_mps, old_speed - max_deceleration_mps2 * dt
            )
        remaining = max(0.0, (old_speed + entity.speed_mps) * 0.5 * dt)
        while remaining > 1e-9 and entity.route_index < len(entity.route):
            point = entity.route[entity.route_index]
            dx = float(point["x_m"]) - entity.x_m
            dy = float(point["y_m"]) - entity.y_m
            distance = math.hypot(dx, dy)
            if distance <= 1e-9:
                entity.route_index += 1
                continue
            entity.heading_degrees = math.degrees(math.atan2(dy, dx))
            consumed = min(remaining, distance)
            entity.x_m += dx / distance * consumed
            entity.y_m += dy / distance * consumed
            remaining -= consumed
            if consumed >= distance - 1e-9:
                entity.route_index += 1
        if entity.route_index >= len(entity.route):
            entity.speed_mps = 0.0
        for tool_instance in self.extension.tool_instances:
            if f"tool:{tool_instance.instance_key}" == entity.entity_ref:
                if tool_instance.operator_actor_key:
                    self._sync_operator(tool_instance.operator_actor_key, entity)
                break
        motion = entity.motion()
        self._trajectory.append(
            {"elapsed_ms": self._current_elapsed_ms, **motion}
        )
        if self._engine is not None:
            self._engine.publish_event("event/spatial_position_changed", motion)
        return motion

    def _sync_operator(self, actor_key: str, tool: ContinuousEntity) -> None:
        actor_ref = self.actor_entities.get(actor_key)
        if actor_ref is None:
            return
        actor = self.entities[actor_ref]
        actor.x_m = tool.x_m
        actor.y_m = tool.y_m
        actor.heading_degrees = tool.heading_degrees
        actor.speed_mps = tool.speed_mps

    def read_channel(self, channel_ref: str) -> Any:
        if channel_ref in self._channel_values:
            return self._channel_values[channel_ref]
        if not channel_ref.startswith("state:"):
            return None
        parts = channel_ref.split(":")
        if len(parts) >= 4 and parts[1] in {"actor", "agent", "tool"}:
            kind = "actor" if parts[1] == "agent" else parts[1]
            entity_ref = f"{kind}:{parts[2]}"
            entity = self.entities.get(entity_ref)
            if entity is None:
                return None
            if parts[3] == "motion":
                return entity.motion()
            if parts[3] == "route":
                return {"points": list(entity.route)}
        if len(parts) >= 4 and parts[1] == "interaction" and parts[3] == "motions":
            occupied_actor_refs = {
                f"actor:{actor_key}" for actor_key in self.actor_active_tools
            }
            return [
                entity.motion()
                for entity in self.entities.values()
                if entity.entity_ref not in occupied_actor_refs
            ]
        if len(parts) >= 4 and parts[1] in {"map-object", "zone"}:
            placement = self.placements.get(parts[2])
            if placement is None:
                return None
            if parts[3] in {"signal", "state"}:
                return dict(placement["state"])
        return None

    def channel_published(self, channel_ref: str, value: Any, data_type: str) -> None:
        self._channel_values[channel_ref] = value
        parts = channel_ref.split(":")
        if (
            len(parts) >= 4
            and parts[0] == "state"
            and parts[1] in {"map-object", "zone"}
            and parts[3] in {"state", "signal"}
            and parts[2] in self.placements
            and isinstance(value, Mapping)
        ):
            previous = dict(self.placements[parts[2]]["state"])
            self.placements[parts[2]]["state"].update(value)
            if previous != self.placements[parts[2]]["state"]:
                self.publish_state_change(
                    channel_ref,
                    {
                        "target_ref": f"{parts[1]}:{parts[2]}",
                        "previous": previous,
                        "current": dict(self.placements[parts[2]]["state"]),
                    },
                )
                if self._engine is not None:
                    self._engine.publish_state_change(
                        channel_ref, dict(self.placements[parts[2]]["state"])
                    )
        if data_type.startswith("metric/"):
            self.record_metric(channel_ref, value, "")
        for metric in self.extension.metrics:
            if metric.source_channel == channel_ref:
                self.record_metric(metric.metric_key, value, metric.unit)

    def entities_in_zone(
        self, target_ref: str, entity_types: list[str]
    ) -> list[dict[str, Any]]:
        key = target_ref.split(":", 1)[-1]
        zone = self.placements.get(key)
        if zone is None:
            return []
        half_width = float(zone["width_m"]) / 2
        half_height = float(zone["height_m"]) / 2
        allowed = set(entity_types)
        return [
            entity.motion()
            for entity in self.entities.values()
            if (not allowed or entity.entity_kind in allowed)
            and abs(entity.x_m - zone["x_m"]) <= half_width
            and abs(entity.y_m - zone["y_m"]) <= half_height
        ]

    def record_decision(
        self,
        task_key: str,
        target_ref: str,
        action: str,
        context: Mapping[str, Any],
    ) -> None:
        previous = self.last_decisions.get(target_ref)
        current = {"task_key": task_key, "action": action, **dict(context)}
        self.last_decisions[target_ref] = current
        if previous is None or previous.get("action") != action:
            self._events.append(
                {
                    "event_type": "traffic.passage-decision",
                    "agent_keys": self._agent_keys_for_target(target_ref),
                    "payload": {
                        "title": f"{target_ref} -> {action}",
                        "target_ref": target_ref,
                        **current,
                    },
                }
            )

    def record_metric(self, metric_key: str, value: Any, unit: str) -> None:
        normalized = value.get("value") if isinstance(value, Mapping) else value
        self.metrics[metric_key] = {"value": normalized, "unit": unit}

    def publish_state_change(self, state_key: str, payload: Mapping[str, Any]) -> None:
        self._events.append(
            {
                "event_type": "capability.state-changed",
                "agent_keys": [],
                "payload": {"title": state_key, **dict(payload)},
            }
        )

    def _agent_keys_for_target(self, target_ref: str) -> list[str]:
        if target_ref.startswith(("actor:", "agent:")):
            return [target_ref.split(":", 1)[1]]
        if target_ref.startswith("tool:"):
            tool_key = target_ref.split(":", 1)[1]
            return [
                item.operator_actor_key
                for item in self.extension.tool_instances
                if item.instance_key == tool_key and item.operator_actor_key
            ]
        return []

    def execute_capability_workflow(
        self,
        workflow_key: str | None,
        inputs: Mapping[str, Any],
        *,
        parameters: Mapping[str, Any],
        state: dict[str, Any],
        task_key: str,
    ) -> Mapping[str, Any]:
        if not workflow_key:
            raise ValueError("WORKFLOW capability requires an entrypoint")

        def llm_handler(node, node_inputs, runtime_context):
            model = self.context.models.get("chat")
            prompt = json.dumps(
                {"node": node.title, "inputs": node_inputs, "context": runtime_context},
                ensure_ascii=False,
            )
            response = model.complete(prompt)
            return getattr(response, "text", str(response))

        result = self._workflow_executor.execute(
            workflow_key,
            inputs,
            llm_handler=llm_handler,
            runtime_context={"parameters": parameters, "task_key": task_key},
            state=state,
            invocation_id=f"capability:{task_key}:{self._current_elapsed_ms}",
        )
        return result.value if isinstance(result.value, Mapping) else {"result": result.value}

    def execute_llm_capability(
        self,
        contract: CapabilityContract,
        inputs: Mapping[str, Any],
        *,
        parameters: Mapping[str, Any],
        state: dict[str, Any],
        task_key: str,
    ) -> Mapping[str, Any]:
        prompt_template = contract.implementation.config.get(
            "prompt",
            "Return one JSON object whose keys are declared capability outputs.\n"
            "Inputs: {inputs}\nParameters: {parameters}",
        )
        prompt = str(prompt_template).format(
            inputs=json.dumps(inputs, ensure_ascii=False),
            parameters=json.dumps(parameters, ensure_ascii=False),
            state=json.dumps(state, ensure_ascii=False),
        )
        model = self.context.models.get("chat")
        response = model.complete(prompt)
        text = getattr(response, "text", str(response)).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].lstrip()
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("LLM capability must return a JSON object")
        return value

    def begin_interval(self, elapsed_ms: int) -> None:
        self._current_elapsed_ms = elapsed_ms
        self._trajectory = []

    def finish_interval(self, elapsed_ms: int) -> dict[str, Any]:
        self._current_elapsed_ms = elapsed_ms
        self._detect_safety_events()
        return {
            "elapsed_ms": elapsed_ms,
            "entities": {
                key: value.snapshot() for key, value in sorted(self.entities.items())
            },
            "placements": {
                key: {
                    "state": dict(value["state"]),
                    "x_m": value["x_m"],
                    "y_m": value["y_m"],
                }
                for key, value in sorted(self.placements.items())
                if value["state"]
            },
            "metrics": dict(self.metrics),
            "trajectory_samples": list(self._trajectory),
        }

    def _detect_safety_events(self) -> None:
        actors = list(self.entities.values())
        minimum = math.inf
        collision_pair: tuple[str, str] | None = None
        for index, first in enumerate(actors):
            for second in actors[index + 1 :]:
                # A driver and the vehicle occupied by that driver are one
                # physical participant, not a collision pair.
                if self._same_operator_pair(first.entity_ref, second.entity_ref):
                    continue
                clearance = max(
                    0.0,
                    math.hypot(first.x_m - second.x_m, first.y_m - second.y_m)
                    - first.radius_m
                    - second.radius_m,
                )
                if clearance < minimum:
                    minimum = clearance
                if clearance <= 0:
                    collision_pair = (first.entity_ref, second.entity_ref)
        if math.isfinite(minimum):
            self.record_metric("scene-minimum-distance", minimum, "m")
        if collision_pair:
            self.collision_count += 1
            for metric in self.extension.metrics:
                if metric.kind == "COLLISION":
                    self.record_metric(metric.metric_key, self.collision_count, "count")
            self._events.append(
                {
                    "event_type": "safety.collision",
                    "agent_keys": sorted(
                        set(
                            self._agent_keys_for_target(collision_pair[0])
                            + self._agent_keys_for_target(collision_pair[1])
                        )
                    ),
                    "payload": {
                        "title": "Collision",
                        "participants": list(collision_pair),
                        "clearance_m": 0,
                        "importance_score": 1,
                    },
                }
            )
        elif minimum < 1.0:
            self._events.append(
                {
                    "event_type": "safety.near-miss",
                    "agent_keys": [],
                    "payload": {
                        "title": "Near miss",
                        "minimum_distance_m": minimum,
                        "importance_score": 0.8,
                    },
                }
            )

    def _same_operator_pair(self, first: str, second: str) -> bool:
        values = {first, second}
        for actor_key, tool_key in self.actor_active_tools.items():
            if values == {f"actor:{actor_key}", f"tool:{tool_key}"}:
                return True
        return False

    def drain_events(self) -> list[dict[str, Any]]:
        events = self._events
        self._events = []
        return events

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "entities": {
                key: value.snapshot() for key, value in sorted(self.entities.items())
            },
            "placements": {
                key: dict(value["state"]) for key, value in sorted(self.placements.items())
            },
            "metrics": dict(self.metrics),
            "last_decisions": dict(self.last_decisions),
            "last_task_time": dict(self._last_task_time),
            "channel_values": dict(self._channel_values),
            "collision_count": self.collision_count,
        }

    def restore_state(self, document: Mapping[str, Any]) -> None:
        checkpoint_entities = document.get("entities") or {}
        if set(checkpoint_entities) != set(self.entities):
            raise ValueError("checkpoint entity identities do not match capability snapshot")
        for key, state in checkpoint_entities.items():
            entity = self.entities[key]
            entity.x_m = float(state["x_m"])
            entity.y_m = float(state["y_m"])
            entity.heading_degrees = float(state["heading_degrees"])
            entity.speed_mps = float(state["speed_mps"])
            entity.route = list(state.get("route") or [])
            entity.route_index = int(state.get("route_index", 0))
        for key, state in (document.get("placements") or {}).items():
            if key in self.placements:
                self.placements[key]["state"] = dict(state)
        self.metrics = dict(document.get("metrics") or {})
        self.last_decisions = dict(document.get("last_decisions") or {})
        self._last_task_time = {
            key: int(value)
            for key, value in (document.get("last_task_time") or {}).items()
        }
        self._channel_values = dict(document.get("channel_values") or {})
        self.collision_count = int(document.get("collision_count", 0))


class CapabilityGameFacade:
    """Checkpoint adapter shared with the existing durable committer."""

    def __init__(self, runner: "CapabilitySimulationRunner") -> None:
        self.runner = runner
        self.conversation: dict[str, Any] = {}

    def snapshot_state(self) -> dict[str, Any]:
        return self.runner.snapshot_state()

    @staticmethod
    def storage_exporters() -> dict[str, Any]:
        return {}


@dataclass(slots=True)
class CapabilitySimulationRunner:
    context: Any
    snapshot: Mapping[str, Any]
    scene: CapabilityScene
    engine: CapabilityRuntimeEngine
    committer: StepCommitter
    checkpoint_interval_steps: int = 1
    completed_steps: int = 0
    game: CapabilityGameFacade = field(init=False)
    _trace_cursor: int = 0
    _previous_coords: dict[str, tuple[int, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.game = CapabilityGameFacade(self)
        self.scene.bind_engine(self.engine)
        self.scene.set_task_intervals(self.engine)
        self._previous_coords = {
            actor.actor_key: self._actor_coord(actor.actor_key)
            for actor in self.scene.extension.actors
        }

    def _actor_entity(self, actor_key: str) -> ContinuousEntity:
        active_tool = self.scene.actor_active_tools.get(actor_key)
        ref = f"tool:{active_tool}" if active_tool else f"actor:{actor_key}"
        return self.scene.entities[ref]

    def _actor_coord(self, actor_key: str) -> tuple[int, int]:
        entity = self._actor_entity(actor_key)
        return (round(entity.x_m), round(entity.y_m))

    def run(self, steps: int, *, stride_minutes: int = 1) -> int:
        del stride_minutes
        if steps < 1:
            raise ValueError("steps must be positive")
        interval = self.scene.extension.clock.snapshot_interval_ms
        for offset in range(steps):
            if self.context.control.cancel_requested or self.context.control.pause_requested:
                break
            step_no = self.completed_steps + 1
            start_elapsed = self.engine.scheduler.virtual_time_ms
            remaining_duration = self.scene.extension.clock.duration_ms - start_elapsed
            if remaining_duration <= 0:
                break
            step_interval = min(interval, remaining_duration)
            self.scene.begin_interval(start_elapsed)
            self.engine.run(step_interval)
            elapsed = self.engine.scheduler.virtual_time_ms
            virtual_time = self.context.clock.get_date() + timedelta(milliseconds=elapsed)
            snapshot = self.scene.finish_interval(elapsed)
            builder = StepResultBuilder(
                run_id=self.context.run_id,
                attempt_id=self.context.attempt_id,
                step_no=step_no,
                virtual_time=virtual_time,
            )
            for actor in self.scene.extension.actors:
                entity = self._actor_entity(actor.actor_key)
                from_coord = self._previous_coords[actor.actor_key]
                to_coord = self._actor_coord(actor.actor_key)
                decision = self.scene.last_decisions.get(
                    f"actor:{actor.actor_key}",
                    self.scene.last_decisions.get(
                        f"tool:{actor.active_tool_instance_key}", {}
                    ),
                )
                moving = entity.speed_mps > 1e-6 or from_coord != to_coord
                builder.add_agent(
                    AgentStepResult(
                        agent_key=actor.experiment_agent_key,
                        from_coord=from_coord,
                        to_coord=to_coord,
                        path=(from_coord, to_coord) if moving else (),
                        action=ActionSnapshot(
                            description=str(
                                decision.get("action")
                                or ("moving" if moving else "waiting")
                            ),
                            emoji="🚗" if actor.role == "DRIVER" else "🚶",
                        ),
                        activity_kind=ActivityKind.MOVING if moving else ActivityKind.OTHER,
                        location=("capability-scene", "intersection"),
                        currently=f"{actor.role.lower()} at ({entity.x_m:.2f}, {entity.y_m:.2f})",
                        path_source="CAPABILITY",
                        decision_context={
                            "actor_key": actor.actor_key,
                            "role": actor.role,
                            "active_tool_instance_key": actor.active_tool_instance_key,
                            "motion": entity.motion(),
                            "decision": decision,
                            "elapsed_ms": elapsed,
                        },
                    )
                )
                self._previous_coords[actor.actor_key] = to_coord

            events = self.scene.drain_events()
            traces = self.engine.traces[self._trace_cursor :]
            self._trace_cursor = len(self.engine.traces)
            events.append(
                {
                    "event_type": "capability.snapshot",
                    "agent_keys": [actor.experiment_agent_key for actor in self.scene.extension.actors],
                    "payload": {
                        "title": f"Capability snapshot {step_no}",
                        **snapshot,
                        "channels": self.engine.channels.snapshot(),
                    },
                }
            )
            events.append(
                {
                    "event_type": "capability.execution-batch",
                    "agent_keys": [],
                    "payload": {
                        "title": f"Capability executions {step_no}",
                        "executions": [
                            {
                                "task_key": item.task_key,
                                "virtual_time_ms": item.virtual_time_ms,
                                "trigger": item.trigger,
                                "status": item.status,
                                "target_ref": item.target_ref,
                                "inputs": item.inputs,
                                "outputs": item.outputs,
                                "state": item.state,
                                "missing_inputs": list(item.missing_inputs),
                            }
                            for item in traces
                        ],
                    },
                }
            )
            for sequence, event in enumerate(events, start=1):
                builder.add_domain_event(
                    DomainEventRecord(
                        event_id=deterministic_record_id(
                            self.context.run_id,
                            step_no,
                            "domain-event",
                            f"{sequence}:{event['event_type']}",
                        ),
                        sequence=sequence,
                        event_type=event["event_type"],
                        agent_keys=tuple(event.get("agent_keys") or ()),
                        payload=event["payload"],
                    )
                )
            result = builder.freeze()
            stop_reached = self._stop_reached()
            terminal = (
                offset == steps - 1
                or stop_reached
                or self.context.control.pause_requested
                or self.context.control.cancel_requested
            )
            force_checkpoint = (
                terminal or step_no % self.checkpoint_interval_steps == 0
            )
            self.committer.commit(result, force_checkpoint=force_checkpoint)
            self.completed_steps = step_no
            if stop_reached:
                break
        return self.completed_steps

    def _stop_reached(self) -> bool:
        operators = {
            "LT": lambda value, threshold: value < threshold,
            "LTE": lambda value, threshold: value <= threshold,
            "EQ": lambda value, threshold: value == threshold,
            "GTE": lambda value, threshold: value >= threshold,
            "GT": lambda value, threshold: value > threshold,
        }
        for condition in self.scene.extension.stop_conditions:
            metric = self.scene.metrics.get(condition.metric_key)
            if metric is None:
                continue
            raw_value = metric.get("value")
            if isinstance(raw_value, Mapping):
                raw_value = raw_value.get("value")
            if isinstance(raw_value, (int, float)) and operators[condition.operator](
                float(raw_value), condition.threshold
            ):
                return True
        return False

    def snapshot_state(self) -> dict[str, Any]:
        elapsed = self.engine.scheduler.virtual_time_ms
        virtual_time = self.context.clock.get_date() + timedelta(milliseconds=elapsed)
        return {
            "schema_version": "ga-capability-checkpoint/v1",
            "virtual_time": virtual_time.isoformat(),
            "scene": self.scene.snapshot_state(),
            "capability_engine": self.engine.snapshot_state(),
        }

    def restore_state(self, checkpoint_state: Mapping[str, Any]) -> None:
        if checkpoint_state.get("schema_version") != "ga-capability-checkpoint/v1":
            raise ValueError("checkpoint is not a capability simulation checkpoint")
        self.scene.restore_state(checkpoint_state["scene"])
        self.engine.restore_state(checkpoint_state["capability_engine"])
        self._previous_coords = {
            actor.actor_key: self._actor_coord(actor.actor_key)
            for actor in self.scene.extension.actors
        }


def build_capability_runner(
    context: Any,
    snapshot: Mapping[str, Any],
    *,
    workflows: Mapping[str, WorkflowDefinition] | None = None,
    workflow_functions: Mapping[str, str] | None = None,
    checkpoint_state: Mapping[str, Any] | None = None,
    checkpoint_interval_steps: int = 1,
    committer: StepCommitter | None = None,
) -> CapabilitySimulationRunner:
    scene = CapabilityScene(
        snapshot,
        context,
        workflows=workflows,
        workflow_functions=workflow_functions,
    )
    engine = CapabilityRuntimeEngine(snapshot, scene)
    runner = CapabilitySimulationRunner(
        context=context,
        snapshot=snapshot,
        scene=scene,
        engine=engine,
        committer=committer,  # type: ignore[arg-type] - installed before execution
        checkpoint_interval_steps=checkpoint_interval_steps,
    )
    if checkpoint_state is not None:
        runner.restore_state(checkpoint_state)
    return runner


__all__ = [
    "CapabilityScene",
    "CapabilitySimulationRunner",
    "ContinuousEntity",
    "build_capability_runner",
]
