"""Versioned contracts for reusable, composable simulation capabilities.

The contract is deliberately independent from ExperimentDefinition V1.  Existing
Stanford Town revisions must keep their normalized JSON and hashes while the V2
composition model is introduced alongside them.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .schema import StrictModel


StableKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=2,
        max_length=80,
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
    ),
]
PortKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
DataType = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=120,
        pattern=r"^(?:any|(?:event|state|command|metric|entity-ref|scalar)/[a-z][a-z0-9_-]*)$",
    ),
]

CapabilityKind = Literal[
    "SENSOR",
    "DECISION",
    "ACTION",
    "CONTROLLER",
    "OBSERVER",
    "ADAPTER",
]
CapabilityTarget = Literal[
    "AGENT",
    "BRAIN",
    "TOOL",
    "MAP_OBJECT",
    "ZONE",
    "INTERACTION",
    "WORLD",
]
TriggerMode = Literal[
    "FIXED_INTERVAL",
    "EVENT",
    "STATE_CHANGE",
    "DECISION",
    "MANUAL",
]
ImplementationKind = Literal[
    "BUILTIN",
    "STATE_MACHINE",
    "WORKFLOW",
    "RULES",
    "PYTHON",
    "LLM",
]


def _object_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


def _validate_object_schema(value: dict[str, Any], *, field_name: str) -> dict[str, Any]:
    if value.get("type") != "object":
        raise ValueError(f"{field_name} must declare a JSON object schema")
    properties = value.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError(f"{field_name}.properties must be an object")
    required = value.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise ValueError(f"{field_name}.required must be a string array")
    unknown = sorted(set(required) - set(properties))
    if unknown:
        raise ValueError(
            f"{field_name}.required references undefined properties: {', '.join(unknown)}"
        )
    return value


class CapabilityPort(StrictModel):
    key: PortKey
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    data_type: DataType
    description: Annotated[str, StringConstraints(max_length=2_000)] = ""
    required: bool = False
    multiple: bool = False


class CapabilityTrigger(StrictModel):
    mode: TriggerMode
    interval_ms: int | None = Field(default=None, ge=1, le=86_400_000)
    event_types: list[DataType] = Field(default_factory=list, max_length=100)
    default: bool = False

    @model_validator(mode="after")
    def validate_trigger_shape(self) -> "CapabilityTrigger":
        if self.mode == "FIXED_INTERVAL" and self.interval_ms is None:
            raise ValueError("FIXED_INTERVAL trigger requires interval_ms")
        if self.mode != "FIXED_INTERVAL" and self.interval_ms is not None:
            raise ValueError("interval_ms is only valid for FIXED_INTERVAL triggers")
        if self.mode == "EVENT":
            if not self.event_types:
                raise ValueError("EVENT trigger requires at least one event type")
            if any(not item.startswith("event/") for item in self.event_types):
                raise ValueError("EVENT trigger event_types must use event/* types")
        elif self.event_types:
            raise ValueError("event_types is only valid for EVENT triggers")
        return self


class CapabilityImplementation(StrictModel):
    kind: ImplementationKind
    entrypoint: Annotated[str, StringConstraints(max_length=500)] | None = None
    source: Annotated[str, StringConstraints(max_length=100_000)] | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    deterministic: bool = True

    @model_validator(mode="after")
    def validate_implementation(self) -> "CapabilityImplementation":
        if self.kind == "BUILTIN" and not (self.entrypoint or "").strip():
            raise ValueError("BUILTIN implementation requires entrypoint")
        if self.kind == "PYTHON" and not (self.source or "").strip():
            raise ValueError("PYTHON implementation requires source")
        if self.kind not in {"PYTHON", "RULES"} and self.source is not None:
            raise ValueError("source is only valid for PYTHON or RULES implementations")
        return self


class CapabilityDependency(StrictModel):
    capability_revision_id: str | None = Field(default=None, min_length=1, max_length=36)
    interface: StableKey | None = None
    optional: bool = False

    @model_validator(mode="after")
    def require_one_identity(self) -> "CapabilityDependency":
        if bool(self.capability_revision_id) == bool(self.interface):
            raise ValueError(
                "dependency requires exactly one of capability_revision_id or interface"
            )
        return self


class CapabilityObservability(StrictModel):
    record_inputs: bool = False
    record_outputs: bool = True
    record_state: bool = False
    metric_outputs: list[PortKey] = Field(default_factory=list, max_length=100)
    sensitive_inputs: list[PortKey] = Field(default_factory=list, max_length=100)


class CapabilityContract(StrictModel):
    schema_version: Literal["ga-capability/v1"] = "ga-capability/v1"
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    summary: Annotated[str, StringConstraints(max_length=2_000)] = ""
    kind: CapabilityKind
    targets: list[CapabilityTarget] = Field(min_length=1, max_length=20)
    interfaces: list[StableKey] = Field(default_factory=list, max_length=50)
    parameters_schema: dict[str, Any] = Field(default_factory=_object_schema)
    inputs: list[CapabilityPort] = Field(default_factory=list, max_length=200)
    outputs: list[CapabilityPort] = Field(default_factory=list, max_length=200)
    state_schema: dict[str, Any] = Field(default_factory=_object_schema)
    triggers: list[CapabilityTrigger] = Field(min_length=1, max_length=20)
    implementation: CapabilityImplementation
    dependencies: list[CapabilityDependency] = Field(default_factory=list, max_length=100)
    permissions: list[StableKey] = Field(default_factory=list, max_length=100)
    observability: CapabilityObservability = Field(default_factory=CapabilityObservability)

    @field_validator("parameters_schema")
    @classmethod
    def validate_parameters_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_object_schema(value, field_name="parameters_schema")

    @field_validator("state_schema")
    @classmethod
    def validate_state_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_object_schema(value, field_name="state_schema")

    @model_validator(mode="after")
    def validate_contract_relations(self) -> "CapabilityContract":
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("targets must be unique")
        if len(self.interfaces) != len(set(self.interfaces)):
            raise ValueError("interfaces must be unique")
        input_keys = [port.key for port in self.inputs]
        output_keys = [port.key for port in self.outputs]
        if len(input_keys) != len(set(input_keys)):
            raise ValueError("input port keys must be unique")
        if len(output_keys) != len(set(output_keys)):
            raise ValueError("output port keys must be unique")
        overlap = sorted(set(input_keys) & set(output_keys))
        if overlap:
            raise ValueError(
                "input and output port keys share the same namespace: "
                + ", ".join(overlap)
            )
        defaults = sum(trigger.default for trigger in self.triggers)
        if defaults != 1:
            raise ValueError("exactly one trigger must be marked as default")
        if self.observability.metric_outputs:
            output_by_key = {port.key: port for port in self.outputs}
            missing = sorted(set(self.observability.metric_outputs) - set(output_by_key))
            if missing:
                raise ValueError(
                    "metric_outputs reference undefined output ports: " + ", ".join(missing)
                )
            invalid = [
                key
                for key in self.observability.metric_outputs
                if not output_by_key[key].data_type.startswith("metric/")
            ]
            if invalid:
                raise ValueError(
                    "metric_outputs must reference metric/* ports: " + ", ".join(invalid)
                )
        missing_sensitive = sorted(
            set(self.observability.sensitive_inputs) - set(input_keys)
        )
        if missing_sensitive:
            raise ValueError(
                "sensitive_inputs reference undefined input ports: "
                + ", ".join(missing_sensitive)
            )
        if self.implementation.kind == "PYTHON" and "execute-python" not in self.permissions:
            raise ValueError("PYTHON implementation requires execute-python permission")
        return self


class CapabilityRunPolicy(StrictModel):
    trigger: TriggerMode
    interval_ms: int | None = Field(default=None, ge=1, le=86_400_000)
    event_types: list[DataType] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_policy(self) -> "CapabilityRunPolicy":
        probe = CapabilityTrigger(
            mode=self.trigger,
            interval_ms=self.interval_ms,
            event_types=self.event_types,
            default=True,
        )
        del probe
        return self


class CapabilityInstanceDefinition(StrictModel):
    instance_key: PortKey
    capability_revision_id: str = Field(min_length=1, max_length=36)
    target_ref: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
    parameters: dict[str, Any] = Field(default_factory=dict)
    run_policy: CapabilityRunPolicy
    enabled: bool = True


class CapabilityEndpoint(StrictModel):
    instance_key: PortKey
    port_key: PortKey


class CapabilityBindingDefinition(StrictModel):
    binding_key: PortKey
    source: CapabilityEndpoint
    target: CapabilityEndpoint
    delivery: Literal["LATEST", "QUEUE", "ACCUMULATE"] = "LATEST"

    @model_validator(mode="after")
    def prevent_self_loop(self) -> "CapabilityBindingDefinition":
        if self.source == self.target:
            raise ValueError("a binding cannot connect an endpoint to itself")
        return self


class CapabilityBundlePortExposure(StrictModel):
    key: PortKey
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    endpoint: CapabilityEndpoint
    required: bool = False


class CapabilityBundleContract(StrictModel):
    schema_version: Literal["ga-capability-bundle/v1"] = "ga-capability-bundle/v1"
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    summary: Annotated[str, StringConstraints(max_length=2_000)] = ""
    targets: list[CapabilityTarget] = Field(min_length=1, max_length=20)
    instances: list[CapabilityInstanceDefinition] = Field(min_length=1, max_length=500)
    bindings: list[CapabilityBindingDefinition] = Field(default_factory=list, max_length=2_000)
    exposed_inputs: list[CapabilityBundlePortExposure] = Field(
        default_factory=list, max_length=500
    )
    exposed_outputs: list[CapabilityBundlePortExposure] = Field(
        default_factory=list, max_length=500
    )
    exposed_parameters_schema: dict[str, Any] = Field(default_factory=_object_schema)

    @field_validator("exposed_parameters_schema")
    @classmethod
    def validate_exposed_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_object_schema(value, field_name="exposed_parameters_schema")

    @model_validator(mode="after")
    def validate_bundle_structure(self) -> "CapabilityBundleContract":
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("bundle targets must be unique")
        instance_keys = [item.instance_key for item in self.instances]
        if len(instance_keys) != len(set(instance_keys)):
            raise ValueError("bundle instance keys must be unique")
        binding_keys = [item.binding_key for item in self.bindings]
        if len(binding_keys) != len(set(binding_keys)):
            raise ValueError("bundle binding keys must be unique")
        known = set(instance_keys)
        for binding in self.bindings:
            if binding.source.instance_key not in known:
                raise ValueError(
                    f"binding {binding.binding_key} references unknown source instance"
                )
            if binding.target.instance_key not in known:
                raise ValueError(
                    f"binding {binding.binding_key} references unknown target instance"
                )
        exposure_keys = [
            item.key for item in (*self.exposed_inputs, *self.exposed_outputs)
        ]
        if len(exposure_keys) != len(set(exposure_keys)):
            raise ValueError("bundle exposed port keys must be unique")
        for exposure in (*self.exposed_inputs, *self.exposed_outputs):
            if exposure.endpoint.instance_key not in known:
                raise ValueError(
                    f"exposed port {exposure.key} references unknown instance"
                )
        return self


def normalize_contract_key(value: str) -> str:
    """Normalize user-facing names only for generated keys, never stored identities."""

    candidate = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return candidate[:64].rstrip("-") or "capability"


__all__ = [
    "CapabilityBindingDefinition",
    "CapabilityBundleContract",
    "CapabilityBundlePortExposure",
    "CapabilityContract",
    "CapabilityEndpoint",
    "CapabilityImplementation",
    "CapabilityInstanceDefinition",
    "CapabilityObservability",
    "CapabilityPort",
    "CapabilityRunPolicy",
    "CapabilityTrigger",
    "normalize_contract_key",
]
