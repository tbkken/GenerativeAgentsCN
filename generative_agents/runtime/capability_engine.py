"""Typed capability graph runtime used by composed simulations.

There is deliberately no traffic-scenario switch in this module.  Mounts are
expanded from published bundle revisions, values travel through declared ports,
and every implementation is invoked through the same execution contract.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from generative_agents.config.capabilities import (
    CapabilityBundleContract,
    CapabilityContract,
    CapabilityInstanceDefinition,
)
from generative_agents.config.scenarios import ExperimentCapabilityExtension

from .multirate import (
    CapabilityInvocation,
    MultiRateCapabilityScheduler,
    ScheduledCapabilityTask,
)
from .workflow_functions import invoke_inline_workflow_function


class CapabilityExecutionError(RuntimeError):
    code = "CAPABILITY_EXECUTION_FAILED"


@dataclass(frozen=True, slots=True)
class ChannelSample:
    channel_ref: str
    data_type: str
    value: Any
    virtual_time_ms: int
    sequence: int
    producer: str


class CapabilityChannelStore:
    """Latest-value scene channels plus delivery-aware internal endpoints."""

    def __init__(self) -> None:
        self._latest: dict[str, ChannelSample] = {}
        self._sequence = 0

    def publish(
        self,
        channel_ref: str,
        value: Any,
        *,
        data_type: str,
        virtual_time_ms: int,
        producer: str,
    ) -> ChannelSample:
        self._sequence += 1
        sample = ChannelSample(
            channel_ref=channel_ref,
            data_type=data_type,
            value=value,
            virtual_time_ms=virtual_time_ms,
            sequence=self._sequence,
            producer=producer,
        )
        self._latest[channel_ref] = sample
        return sample

    def latest(self, channel_ref: str) -> ChannelSample | None:
        return self._latest.get(channel_ref)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            key: {
                "data_type": sample.data_type,
                "value": sample.value,
                "virtual_time_ms": sample.virtual_time_ms,
                "sequence": sample.sequence,
                "producer": sample.producer,
            }
            for key, sample in sorted(self._latest.items())
        }

    def restore(self, document: Mapping[str, Mapping[str, Any]]) -> None:
        self._latest.clear()
        self._sequence = 0
        for key, value in document.items():
            sample = ChannelSample(
                channel_ref=key,
                data_type=str(value["data_type"]),
                value=value.get("value"),
                virtual_time_ms=int(value.get("virtual_time_ms", 0)),
                sequence=int(value.get("sequence", 0)),
                producer=str(value.get("producer", "checkpoint")),
            )
            self._latest[key] = sample
            self._sequence = max(self._sequence, sample.sequence)


@dataclass(slots=True)
class RuntimeCapabilityInstance:
    task_key: str
    mount_key: str
    definition: CapabilityInstanceDefinition
    contract: CapabilityContract
    target_ref: str
    parameters: dict[str, Any]
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeMountSpec:
    mount_key: str
    capability_bundle_revision_id: str | None
    capability_revision_id: str | None
    target_bindings: Mapping[str, str]
    parameters: Mapping[str, Any]
    input_bindings: Mapping[str, str]
    output_bindings: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CapabilityTraceRecord:
    task_key: str
    virtual_time_ms: int
    trigger: str
    status: str
    target_ref: str
    inputs: Mapping[str, Any]
    outputs: Mapping[str, Any]
    state: Mapping[str, Any]
    missing_inputs: tuple[str, ...] = ()


BuiltinHandler = Callable[
    [RuntimeCapabilityInstance, Mapping[str, Any], CapabilityInvocation, Any],
    Mapping[str, Any],
]


def _motion_velocity(motion: Mapping[str, Any]) -> tuple[float, float]:
    if "velocity_x_mps" in motion or "velocity_y_mps" in motion:
        return (
            float(motion.get("velocity_x_mps", 0)),
            float(motion.get("velocity_y_mps", 0)),
        )
    speed = float(motion.get("speed_mps", 0))
    heading = math.radians(float(motion.get("heading_degrees", 0)))
    return speed * math.cos(heading), speed * math.sin(heading)


def _relative_motion(
    _instance: RuntimeCapabilityInstance,
    inputs: Mapping[str, Any],
    _invocation: CapabilityInvocation,
    _scene: Any,
) -> Mapping[str, Any]:
    subject = inputs["subject_motion"]
    obj = inputs["object_motion"]
    dx = float(obj["x_m"]) - float(subject["x_m"])
    dy = float(obj["y_m"]) - float(subject["y_m"])
    distance = math.hypot(dx, dy)
    svx, svy = _motion_velocity(subject)
    ovx, ovy = _motion_velocity(obj)
    rvx, rvy = ovx - svx, ovy - svy
    closing_speed = 0.0
    if distance > 1e-9:
        closing_speed = -((dx * rvx + dy * rvy) / distance)
    ttc = distance / closing_speed if closing_speed > 1e-9 else None
    clearance = max(
        0.0,
        distance
        - float(subject.get("radius_m", 0))
        - float(obj.get("radius_m", 0)),
    )
    return {
        "relative_motion": {
            "subject_ref": subject.get("entity_ref"),
            "object_ref": obj.get("entity_ref"),
            "distance_m": distance,
            "clearance_m": clearance,
            "closing_speed_mps": closing_speed,
            "time_to_collision_s": ttc,
        }
    }


def _gap_acceptance(
    instance: RuntimeCapabilityInstance,
    inputs: Mapping[str, Any],
    _invocation: CapabilityInvocation,
    scene: Any,
) -> Mapping[str, Any]:
    relative = inputs["relative_motion"]
    safe_gap = float(instance.parameters["safe_gap_s"])
    collision_distance = float(instance.parameters["collision_distance_m"])
    desired_speed = float(instance.parameters["desired_speed_mps"])
    clearance = float(relative.get("clearance_m", relative.get("distance_m", 0)))
    ttc = relative.get("time_to_collision_s")
    signal = inputs.get("signal_state") or {}
    signal_state = str(signal.get("state", signal.get("phase", ""))).upper()
    signal_blocks = signal_state in {"RED", "VEHICLE-RED", "PEDESTRIAN-RED"}
    unsafe_gap = clearance <= collision_distance or (
        ttc is not None and 0 <= float(ttc) < safe_gap
    )
    action = "YIELD" if unsafe_gap or signal_blocks else "PROCEED"
    reason = "signal" if signal_blocks else "gap" if unsafe_gap else "safe-gap"
    scene.record_decision(
        instance.task_key,
        instance.target_ref,
        action,
        {"reason": reason, "relative_motion": relative},
    )
    return {
        "passage_action": {
            "action": action,
            "target_speed_mps": desired_speed if action == "PROCEED" else 0.0,
            "reason": reason,
        }
    }


def _passage_to_walk(
    _instance: RuntimeCapabilityInstance,
    inputs: Mapping[str, Any],
    _invocation: CapabilityInvocation,
    _scene: Any,
) -> Mapping[str, Any]:
    passage = dict(inputs["passage_action"])
    return {
        "movement_command": {
            "action": passage.get("action", "YIELD"),
            "target_speed_mps": float(passage.get("target_speed_mps", 0)),
            "reason": passage.get("reason", "decision"),
        }
    }


def _speed_control(
    instance: RuntimeCapabilityInstance,
    inputs: Mapping[str, Any],
    _invocation: CapabilityInvocation,
    _scene: Any,
) -> Mapping[str, Any]:
    passage = inputs["passage_action"]
    motion = inputs["motion"]
    action = passage.get("action", "YIELD")
    target = float(passage.get("target_speed_mps", 0)) if action == "PROCEED" else 0.0
    current = float(motion.get("speed_mps", 0))
    emergency = action in {"BRAKE", "EMERGENCY_BRAKE"}
    return {
        "speed_command": {
            "target_speed_mps": target,
            "deceleration_mps2": float(
                instance.parameters[
                    "emergency_deceleration_mps2"
                    if emergency
                    else "comfortable_deceleration_mps2"
                ]
            ),
            "accelerating": target > current,
            "action": action,
        }
    }


def _continuous_walk(
    instance: RuntimeCapabilityInstance,
    inputs: Mapping[str, Any],
    invocation: CapabilityInvocation,
    scene: Any,
) -> Mapping[str, Any]:
    command = inputs["movement_command"]
    requested = min(
        float(command.get("target_speed_mps", 0)),
        float(instance.parameters["speed_mps"]),
    )
    motion = scene.advance_target(
        instance.target_ref,
        target_speed_mps=requested,
        max_acceleration_mps2=float(
            instance.parameters.get("max_acceleration_mps2", 1.2)
        ),
        max_deceleration_mps2=float(
            instance.parameters.get("max_acceleration_mps2", 1.2)
        ),
        delta_ms=scene.delta_ms(instance.task_key, invocation.virtual_time_ms),
    )
    return {"motion": motion}


def _path_follow(
    instance: RuntimeCapabilityInstance,
    inputs: Mapping[str, Any],
    invocation: CapabilityInvocation,
    scene: Any,
) -> Mapping[str, Any]:
    command = inputs["speed_command"]
    route = inputs["route"]
    scene.set_route(instance.target_ref, route)
    target = min(
        float(command.get("target_speed_mps", 0)),
        float(instance.parameters["max_speed_mps"]),
    )
    motion = scene.advance_target(
        instance.target_ref,
        target_speed_mps=target,
        max_acceleration_mps2=float(instance.parameters["max_acceleration_mps2"]),
        max_deceleration_mps2=min(
            float(instance.parameters["max_deceleration_mps2"]),
            float(command.get("deceleration_mps2", instance.parameters["max_deceleration_mps2"])),
        ),
        delta_ms=scene.delta_ms(instance.task_key, invocation.virtual_time_ms),
    )
    return {"motion": motion}


def _minimum_distance(
    instance: RuntimeCapabilityInstance,
    inputs: Mapping[str, Any],
    _invocation: CapabilityInvocation,
    scene: Any,
) -> Mapping[str, Any]:
    motions = inputs["motions"]
    if isinstance(motions, Mapping):
        motions = [motions]
    minimum = math.inf
    for index, first in enumerate(motions):
        for second in motions[index + 1 :]:
            distance = math.hypot(
                float(first["x_m"]) - float(second["x_m"]),
                float(first["y_m"]) - float(second["y_m"]),
            )
            clearance = max(
                0.0,
                distance
                - float(first.get("radius_m", 0))
                - float(second.get("radius_m", 0)),
            )
            minimum = min(minimum, clearance)
    if not math.isfinite(minimum):
        minimum = float(instance.state.get("minimum_distance_m", 0))
    previous = float(instance.state.get("minimum_distance_m", math.inf))
    instance.state["minimum_distance_m"] = min(previous, minimum)
    value = instance.state["minimum_distance_m"]
    scene.record_metric("minimum_distance", value, "m")
    return {"minimum_distance": {"value": value, "unit": "m"}}


def _timer(
    instance: RuntimeCapabilityInstance,
    inputs: Mapping[str, Any],
    invocation: CapabilityInvocation,
    scene: Any,
) -> Mapping[str, Any]:
    duration = int(instance.parameters["duration_ms"])
    state = instance.state
    state.setdefault("running", True)
    state.setdefault("remaining_ms", duration)
    control = inputs.get("control") or {}
    command = control.get("command")
    if command == "STOP":
        state["running"] = False
    elif command in {"START", "RESET"}:
        state["running"] = True
        state["remaining_ms"] = duration
    if not state["running"]:
        return {}
    state["remaining_ms"] = max(
        0,
        int(state["remaining_ms"])
        - scene.delta_ms(instance.task_key, invocation.virtual_time_ms),
    )
    if state["remaining_ms"]:
        return {}
    output = {"elapsed": {"timer": instance.task_key, "duration_ms": duration}}
    if instance.parameters.get("repeat", False):
        state["remaining_ms"] = duration
    else:
        state["running"] = False
    return output


def _state_machine(
    instance: RuntimeCapabilityInstance,
    inputs: Mapping[str, Any],
    invocation: CapabilityInvocation,
    scene: Any,
) -> Mapping[str, Any]:
    current = instance.state.setdefault("current", instance.parameters["initial_state"])
    requests = inputs.get("transition_request") or []
    if isinstance(requests, Mapping):
        requests = [requests]
    requests.extend(event.get("payload", {}) for event in invocation.events)
    selected = None
    for transition in instance.parameters.get("transitions", []):
        if transition.get("from") not in {None, "*", current}:
            continue
        requested = any(
            request.get("event") == transition.get("event")
            or request.get("to") == transition.get("to")
            for request in requests
        )
        if requested:
            selected = transition
            break
    if selected is None:
        return {"current_state": {"state": current}}
    next_state = selected["to"]
    instance.state["current"] = next_state
    scene.publish_state_change(
        instance.task_key, {"from": current, "to": next_state}
    )
    return {
        "state_changed": {"from": current, "to": next_state},
        "current_state": {"state": next_state},
    }


def _zone_presence(
    instance: RuntimeCapabilityInstance,
    _inputs: Mapping[str, Any],
    _invocation: CapabilityInvocation,
    scene: Any,
) -> Mapping[str, Any]:
    present = scene.entities_in_zone(
        instance.target_ref, instance.parameters.get("entity_types") or []
    )
    previous = set(instance.state.get("entity_refs", []))
    current = {item["entity_ref"] for item in present}
    instance.state["entity_refs"] = sorted(current)
    instance.state["count"] = len(current)
    return {
        "entered": [
            {"entity_ref": key, "zone_ref": instance.target_ref}
            for key in sorted(current - previous)
        ],
        "left": [
            {"entity_ref": key, "zone_ref": instance.target_ref}
            for key in sorted(previous - current)
        ],
        "presence": {
            "zone_ref": instance.target_ref,
            "count": len(current),
            "entity_refs": sorted(current),
        },
    }


BUILTIN_CAPABILITY_HANDLERS: dict[str, BuiltinHandler] = {
    "core.timer.v1": _timer,
    "core.state-machine.v1": _state_machine,
    "spatial.zone-presence.v1": _zone_presence,
    "spatial.relative-motion.v1": _relative_motion,
    "mobility.continuous-walk.v1": _continuous_walk,
    "mobility.path-follow.v1": _path_follow,
    "control.speed.v1": _speed_control,
    "metrics.minimum-distance.v1": _minimum_distance,
    "traffic.gap-acceptance.v1": _gap_acceptance,
    "traffic.passage-to-walk.v1": _passage_to_walk,
}


_PRIORITY_BY_KIND = {
    "SENSOR": 10,
    "DECISION": 20,
    "ADAPTER": 30,
    "CONTROLLER": 40,
    "ACTION": 50,
    "OBSERVER": 60,
}


class CapabilityRuntimeEngine:
    """Compile and execute all mounted bundle instances for one scene."""

    def __init__(self, snapshot: Mapping[str, Any], scene: Any) -> None:
        self.snapshot = snapshot
        self.scene = scene
        self.extension = ExperimentCapabilityExtension.model_validate(
            snapshot["experiment_extension"]
        )
        self.scheduler = MultiRateCapabilityScheduler(
            base_tick_ms=self.extension.clock.base_tick_ms
        )
        self.channels = CapabilityChannelStore()
        self.instances: dict[str, RuntimeCapabilityInstance] = {}
        self.traces: list[CapabilityTraceRecord] = []
        self._endpoint_latest: dict[str, Any] = {}
        self._endpoint_queues: dict[str, deque[Any]] = defaultdict(deque)
        self._endpoint_accumulated: dict[str, list[Any]] = defaultdict(list)
        self._bindings: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self._exposed_inputs: dict[str, list[str]] = defaultdict(list)
        self._exposed_outputs: dict[str, list[str]] = defaultdict(list)
        self._compile()

    def _compile(self) -> None:
        bundle_documents = self.snapshot["capability_bundles"]
        capability_documents = self.snapshot["capabilities"]
        actor_intervals = {
            actor.actor_key: actor.reasoning_interval_ms
            for actor in self.extension.actors
        }
        mounts = [
            RuntimeMountSpec(
                mount_key=mount.mount_key,
                capability_bundle_revision_id=mount.capability_bundle_revision_id,
                capability_revision_id=None,
                target_bindings=mount.target_bindings,
                parameters=mount.parameters,
                input_bindings=mount.input_bindings,
                output_bindings=mount.output_bindings,
            )
            for mount in self.extension.capability_mounts
            if mount.enabled
        ]
        mounts.extend(self._attachment_mounts())
        for mount in mounts:
            if mount.capability_bundle_revision_id:
                bundle_document = bundle_documents.get(
                    mount.capability_bundle_revision_id
                )
            else:
                capability_document = capability_documents.get(
                    mount.capability_revision_id or ""
                )
                bundle_document = (
                    self._single_capability_bundle(capability_document)
                    if capability_document is not None
                    else None
                )
            if bundle_document is None:
                raise CapabilityExecutionError(
                    "snapshot is missing attached capability or bundle "
                    + str(
                        mount.capability_bundle_revision_id
                        or mount.capability_revision_id
                    )
                )
            bundle = CapabilityBundleContract.model_validate(
                bundle_document["composition"]
            )
            for binding in bundle.bindings:
                source = self._endpoint(
                    mount.mount_key,
                    binding.source.instance_key,
                    binding.source.port_key,
                )
                target = self._endpoint(
                    mount.mount_key,
                    binding.target.instance_key,
                    binding.target.port_key,
                )
                self._bindings[source].append((target, binding.delivery))
            for exposure in bundle.exposed_inputs:
                channel_ref = mount.input_bindings.get(exposure.key)
                if channel_ref:
                    endpoint = self._endpoint(
                        mount.mount_key,
                        exposure.endpoint.instance_key,
                        exposure.endpoint.port_key,
                    )
                    self._exposed_inputs[endpoint].append(channel_ref)
            for exposure in bundle.exposed_outputs:
                channel_ref = mount.output_bindings.get(exposure.key)
                if channel_ref:
                    endpoint = self._endpoint(
                        mount.mount_key,
                        exposure.endpoint.instance_key,
                        exposure.endpoint.port_key,
                    )
                    self._exposed_outputs[endpoint].append(channel_ref)

            for definition in bundle.instances:
                if not definition.enabled:
                    continue
                capability_document = capability_documents.get(
                    definition.capability_revision_id
                )
                if capability_document is None:
                    raise CapabilityExecutionError(
                        "snapshot is missing capability "
                        + definition.capability_revision_id
                    )
                contract = CapabilityContract.model_validate(
                    capability_document["contract"]
                )
                target_ref = self._resolve_target(
                    definition.target_ref, mount.target_bindings
                )
                parameters = dict(definition.parameters)
                # Bundle-level exposed parameters are intentionally flat: only
                # names declared by an instance contract are copied into it.
                parameter_properties = contract.parameters_schema.get(
                    "properties", {}
                )
                for key, value in mount.parameters.items():
                    if key in parameter_properties:
                        parameters[key] = value
                task_key = f"{mount.mount_key}.{definition.instance_key}"
                runtime = RuntimeCapabilityInstance(
                    task_key=task_key,
                    mount_key=mount.mount_key,
                    definition=definition,
                    contract=contract,
                    target_ref=target_ref,
                    parameters=parameters,
                )
                self.instances[task_key] = runtime
                policy = definition.run_policy
                interval = policy.interval_ms
                if policy.trigger == "DECISION":
                    actor_key = target_ref.split(":", 1)[-1]
                    interval = actor_intervals.get(actor_key, 60_000)
                task = ScheduledCapabilityTask(
                    task_key=task_key,
                    trigger=policy.trigger,
                    callback=lambda invocation, key=task_key: self._execute(
                        key, invocation
                    ),
                    interval_ms=interval,
                    event_types=tuple(policy.event_types),
                    priority=_PRIORITY_BY_KIND[contract.kind],
                )
                self.scheduler.register(task)

    def _attachment_mounts(self) -> list[RuntimeMountSpec]:
        """Expand reusable map/tool attachments into ordinary runtime mounts."""

        mounts: list[RuntimeMountSpec] = []
        world = self.snapshot["map_revision"]["world"]
        scene = (world.get("definition") or {}).get("spatial_scene") or {}
        spatial_documents = self.snapshot.get("spatial_assets") or {}
        for placement_index, placement in enumerate(scene.get("placements") or []):
            revision_id = placement.get("spatial_asset_revision_id")
            contract = (spatial_documents.get(revision_id) or {}).get("contract") or {}
            kind = str(contract.get("kind") or "OBJECT")
            target_ref = (
                f"zone:{placement['instance_key']}"
                if kind == "ZONE"
                else f"map-object:{placement['instance_key']}"
            )
            parameter_overrides = placement.get("capability_parameter_overrides") or {}
            for attachment_index, attachment in enumerate(
                contract.get("capability_attachments") or []
            ):
                if not attachment.get("enabled", True):
                    continue
                parameters = dict(attachment.get("parameters") or {})
                parameters.update(
                    parameter_overrides.get(attachment["attachment_key"]) or {}
                )
                mounts.append(
                    self._attachment_mount(
                        prefix=f"spatial-{placement_index}-{attachment_index}",
                        attachment=attachment,
                        target_ref=target_ref,
                        parameters=parameters,
                    )
                )

        tool_documents = self.snapshot.get("tools") or {}
        for tool_index, tool in enumerate(self.extension.tool_instances):
            contract = (
                tool_documents.get(tool.tool_revision_id) or {}
            ).get("contract") or {}
            for attachment_index, attachment in enumerate(
                contract.get("capability_attachments") or []
            ):
                if not attachment.get("enabled", True):
                    continue
                parameters = dict(attachment.get("parameters") or {})
                parameters.update(tool.state_overrides.get("capability_parameters", {}).get(
                    attachment["attachment_key"], {}
                ))
                mounts.append(
                    self._attachment_mount(
                        prefix=f"tool-{tool_index}-{attachment_index}",
                        attachment=attachment,
                        target_ref=f"tool:{tool.instance_key}",
                        parameters=parameters,
                    )
                )
        return mounts

    @staticmethod
    def _attachment_mount(
        *,
        prefix: str,
        attachment: Mapping[str, Any],
        target_ref: str,
        parameters: Mapping[str, Any],
    ) -> RuntimeMountSpec:
        target_bindings = {
            "self": target_ref,
            **{
                key: CapabilityRuntimeEngine._resolve_attachment_ref(value, target_ref)
                for key, value in (attachment.get("target_bindings") or {}).items()
            },
        }
        return RuntimeMountSpec(
            mount_key=f"{prefix}-{attachment['attachment_key']}",
            capability_bundle_revision_id=attachment.get(
                "capability_bundle_revision_id"
            ),
            capability_revision_id=attachment.get("capability_revision_id"),
            target_bindings=target_bindings,
            parameters=parameters,
            input_bindings={
                key: CapabilityRuntimeEngine._resolve_attachment_ref(value, target_ref)
                for key, value in (attachment.get("input_bindings") or {}).items()
            },
            output_bindings={
                key: CapabilityRuntimeEngine._resolve_attachment_ref(value, target_ref)
                for key, value in (attachment.get("output_bindings") or {}).items()
            },
        )

    @staticmethod
    def _resolve_attachment_ref(reference: str, target_ref: str) -> str:
        """Resolve an asset-local reference without baking an instance id into it."""

        target_key = target_ref.split(":", 1)[-1]
        return str(reference).replace("${target}", target_ref).replace(
            "${target_key}", target_key
        )

    @staticmethod
    def _single_capability_bundle(
        capability_document: Mapping[str, Any],
    ) -> dict[str, Any]:
        contract = CapabilityContract.model_validate(capability_document["contract"])
        trigger = next(item for item in contract.triggers if item.default)
        return {
            "composition": {
                "name": f"Attached {contract.name}",
                "summary": "Runtime wrapper for an atomic asset attachment.",
                "targets": contract.targets,
                "instances": [
                    {
                        "instance_key": "attached",
                        "capability_revision_id": capability_document["revision_id"],
                        "target_ref": "self",
                        "parameters": {},
                        "run_policy": {
                            "trigger": trigger.mode,
                            "interval_ms": trigger.interval_ms,
                            "event_types": trigger.event_types,
                        },
                    }
                ],
                "exposed_inputs": [
                    {
                        "key": port.key,
                        "name": port.name,
                        "endpoint": {
                            "instance_key": "attached",
                            "port_key": port.key,
                        },
                        "required": port.required,
                    }
                    for port in contract.inputs
                ],
                "exposed_outputs": [
                    {
                        "key": port.key,
                        "name": port.name,
                        "endpoint": {
                            "instance_key": "attached",
                            "port_key": port.key,
                        },
                    }
                    for port in contract.outputs
                ],
                "exposed_parameters_schema": contract.parameters_schema,
            }
        }

    @staticmethod
    def _endpoint(mount_key: str, instance_key: str, port_key: str) -> str:
        return f"{mount_key}.{instance_key}.{port_key}"

    @staticmethod
    def _resolve_target(target_ref: str, bindings: Mapping[str, str]) -> str:
        alias = target_ref.split(":", 1)[-1]
        return bindings.get(target_ref, bindings.get(alias, target_ref))

    def _read_endpoint(self, endpoint: str, *, multiple: bool) -> Any:
        values: list[Any] = []
        if endpoint in self._endpoint_latest:
            values.append(self._endpoint_latest[endpoint])
        queue = self._endpoint_queues[endpoint]
        while queue:
            values.append(queue.popleft())
        if self._endpoint_accumulated[endpoint]:
            values.extend(self._endpoint_accumulated.pop(endpoint))
        for channel_ref in self._exposed_inputs.get(endpoint, ()):
            sample = self.channels.latest(channel_ref)
            value = sample.value if sample is not None else self.scene.read_channel(channel_ref)
            if value is not None:
                if multiple and isinstance(value, list):
                    values.extend(value)
                else:
                    values.append(value)
        if not values:
            return None
        return values if multiple else values[-1]

    def _publish_endpoint(
        self,
        runtime: RuntimeCapabilityInstance,
        port_key: str,
        value: Any,
        invocation: CapabilityInvocation,
    ) -> None:
        endpoint = self._endpoint(runtime.mount_key, runtime.definition.instance_key, port_key)
        port = next(item for item in runtime.contract.outputs if item.key == port_key)
        self._endpoint_latest[endpoint] = value
        for target, delivery in self._bindings.get(endpoint, ()):
            if delivery == "LATEST":
                self._endpoint_latest[target] = value
            elif delivery == "QUEUE":
                self._endpoint_queues[target].append(value)
            else:
                self._endpoint_accumulated[target].append(value)
        for channel_ref in self._exposed_outputs.get(endpoint, ()):
            self.channels.publish(
                channel_ref,
                value,
                data_type=port.data_type,
                virtual_time_ms=invocation.virtual_time_ms,
                producer=runtime.task_key,
            )
            self.scene.channel_published(channel_ref, value, port.data_type)
        if port.data_type.startswith("event/"):
            values = value if isinstance(value, list) else [value]
            for payload in values:
                self.scheduler.publish_event(
                    port.data_type,
                    payload if isinstance(payload, dict) else {"value": payload},
                )

    def _execute(self, task_key: str, invocation: CapabilityInvocation) -> None:
        runtime = self.instances[task_key]
        inputs: dict[str, Any] = {}
        missing: list[str] = []
        for port in runtime.contract.inputs:
            endpoint = self._endpoint(
                runtime.mount_key, runtime.definition.instance_key, port.key
            )
            value = self._read_endpoint(endpoint, multiple=port.multiple)
            if value is None:
                if port.required:
                    missing.append(port.key)
                continue
            inputs[port.key] = value
        if missing:
            self.traces.append(
                CapabilityTraceRecord(
                    task_key=task_key,
                    virtual_time_ms=invocation.virtual_time_ms,
                    trigger=invocation.trigger,
                    status="SKIPPED_MISSING_INPUT",
                    target_ref=runtime.target_ref,
                    inputs=inputs,
                    outputs={},
                    state=dict(runtime.state),
                    missing_inputs=tuple(missing),
                )
            )
            return
        try:
            outputs = dict(self._invoke_implementation(runtime, inputs, invocation))
        except Exception as exc:
            raise CapabilityExecutionError(
                f"{task_key} ({runtime.contract.implementation.kind}) failed: {exc}"
            ) from exc
        known_outputs = {port.key for port in runtime.contract.outputs}
        unknown = sorted(set(outputs) - known_outputs)
        if unknown:
            raise CapabilityExecutionError(
                f"{task_key} emitted undeclared outputs: {', '.join(unknown)}"
            )
        for key, value in outputs.items():
            self._publish_endpoint(runtime, key, value, invocation)
        self.traces.append(
            CapabilityTraceRecord(
                task_key=task_key,
                virtual_time_ms=invocation.virtual_time_ms,
                trigger=invocation.trigger,
                status="SUCCEEDED",
                target_ref=runtime.target_ref,
                inputs=inputs if runtime.contract.observability.record_inputs else {},
                outputs=outputs if runtime.contract.observability.record_outputs else {},
                state=(
                    dict(runtime.state)
                    if runtime.contract.observability.record_state
                    else {}
                ),
            )
        )

    def _invoke_implementation(
        self,
        runtime: RuntimeCapabilityInstance,
        inputs: Mapping[str, Any],
        invocation: CapabilityInvocation,
    ) -> Mapping[str, Any]:
        implementation = runtime.contract.implementation
        if implementation.kind == "BUILTIN":
            handler = BUILTIN_CAPABILITY_HANDLERS.get(implementation.entrypoint or "")
            if handler is None:
                raise ValueError(
                    f"unregistered built-in entrypoint {implementation.entrypoint}"
                )
            return handler(runtime, inputs, invocation, self.scene)
        if implementation.kind == "STATE_MACHINE":
            return _state_machine(runtime, inputs, invocation, self.scene)
        if implementation.kind == "PYTHON":
            context = {
                "parameters": runtime.parameters,
                "state": runtime.state,
                "target_ref": runtime.target_ref,
                "virtual_time_ms": invocation.virtual_time_ms,
            }
            result = invoke_inline_workflow_function(
                implementation.source or "", inputs, context
            )
            new_state = result.pop("state", None)
            if isinstance(new_state, Mapping):
                runtime.state.clear()
                runtime.state.update(new_state)
            return result.get("outputs", result)
        if implementation.kind == "RULES":
            return self._execute_rules(runtime, inputs)
        if implementation.kind == "WORKFLOW":
            return self.scene.execute_capability_workflow(
                implementation.entrypoint
                or implementation.config.get("workflow_key"),
                inputs,
                parameters=runtime.parameters,
                state=runtime.state,
                task_key=runtime.task_key,
            )
        if implementation.kind == "LLM":
            return self.scene.execute_llm_capability(
                runtime.contract,
                inputs,
                parameters=runtime.parameters,
                state=runtime.state,
                task_key=runtime.task_key,
            )
        raise ValueError(f"unsupported implementation kind {implementation.kind}")

    @staticmethod
    def _execute_rules(
        runtime: RuntimeCapabilityInstance, inputs: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        config = runtime.contract.implementation.config
        rules = config.get("rules") or []
        for rule in rules:
            path = str(rule.get("input", ""))
            value: Any = inputs
            for segment in path.split(".") if path else ():
                value = value.get(segment) if isinstance(value, Mapping) else None
            expected = rule.get("value")
            operator = rule.get("operator", "EQ")
            matches = {
                "EQ": value == expected,
                "NE": value != expected,
                "LT": value is not None and value < expected,
                "LTE": value is not None and value <= expected,
                "GT": value is not None and value > expected,
                "GTE": value is not None and value >= expected,
            }.get(operator, False)
            if matches:
                return dict(rule.get("outputs") or {})
        if "default_outputs" in config:
            return dict(config["default_outputs"])
        source = (runtime.contract.implementation.source or "").strip()
        if source == "return inputs":
            return dict(inputs)
        if source in {"return {}", ""}:
            return {}
        raise ValueError("RULES requires config.rules or a supported return expression")

    def run(self, duration_ms: int):
        return self.scheduler.run(duration_ms)

    def publish_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.scheduler.publish_event(event_type, payload)

    def publish_state_change(self, state_key: str, payload: dict[str, Any]) -> None:
        self.scheduler.publish_state_change(state_key, payload)

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "elapsed_ms": self.scheduler.virtual_time_ms,
            "tick_no": self.scheduler.tick_no,
            "channels": self.channels.snapshot(),
            "endpoint_latest": dict(self._endpoint_latest),
            "endpoint_queues": {
                key: list(values)
                for key, values in self._endpoint_queues.items()
                if values
            },
            "endpoint_accumulated": {
                key: list(values)
                for key, values in self._endpoint_accumulated.items()
                if values
            },
            "instances": {
                key: dict(value.state) for key, value in sorted(self.instances.items())
            },
        }

    def restore_state(self, document: Mapping[str, Any]) -> None:
        self.scheduler.restore_clock(
            int(document.get("elapsed_ms", 0)),
            int(document.get("tick_no", 0)),
        )
        self.channels.restore(document.get("channels") or {})
        self._endpoint_latest = dict(document.get("endpoint_latest") or {})
        self._endpoint_queues = defaultdict(
            deque,
            {
                key: deque(values)
                for key, values in (document.get("endpoint_queues") or {}).items()
            },
        )
        self._endpoint_accumulated = defaultdict(
            list,
            {
                key: list(values)
                for key, values in (
                    document.get("endpoint_accumulated") or {}
                ).items()
            },
        )
        for key, state in (document.get("instances") or {}).items():
            if key not in self.instances:
                raise ValueError(f"checkpoint contains unknown capability task {key}")
            self.instances[key].state = dict(state)


__all__ = [
    "BUILTIN_CAPABILITY_HANDLERS",
    "CapabilityChannelStore",
    "CapabilityExecutionError",
    "CapabilityRuntimeEngine",
    "CapabilityTraceRecord",
]
