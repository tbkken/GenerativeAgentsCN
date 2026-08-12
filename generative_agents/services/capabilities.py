"""Lifecycle, validation, and composition for reusable capability assets."""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from math import ceil
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from generative_agents.config.capabilities import (
    CapabilityBundleContract,
    CapabilityContract,
    normalize_contract_key,
)
from generative_agents.config.hashing import canonical_json_bytes
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    CapabilityBundle,
    CapabilityBundleRevision,
    CapabilityDefinition,
    CapabilityRevision,
)
from generative_agents.runtime.json_schema import validate_json_schema

from .errors import ServiceError, not_found


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _status_counts(session: Session, model) -> dict[str, int]:
    counts = {"DRAFT": 0, "PUBLISHED": 0}
    for status, count in session.execute(select(model.status, func.count()).group_by(model.status)):
        counts[status] = int(count)
    counts["ALL"] = sum(counts.values())
    return counts


def _generated_key(name: str, suffix: str) -> str:
    base = normalize_contract_key(name)[:48].rstrip("-")
    return f"{base or suffix}-{uuid4().hex[:8]}"


def _builtin_contracts() -> dict[str, CapabilityContract]:
    object_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    return {
        "core-timer": CapabilityContract.model_validate(
            {
                "name": "计时器",
                "summary": "按照虚拟时间产生可复现的计时事件。",
                "kind": "CONTROLLER",
                "targets": ["WORLD", "MAP_OBJECT", "TOOL", "INTERACTION", "BRAIN"],
                "interfaces": ["timer"],
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "duration_ms": {"type": "integer", "minimum": 1},
                        "repeat": {"type": "boolean"},
                    },
                    "required": ["duration_ms"],
                    "additionalProperties": False,
                },
                "inputs": [
                    {
                        "key": "control",
                        "name": "计时控制",
                        "data_type": "command/timer_control",
                    }
                ],
                "outputs": [
                    {
                        "key": "elapsed",
                        "name": "计时完成",
                        "data_type": "event/timer_elapsed",
                    }
                ],
                "state_schema": {
                    "type": "object",
                    "properties": {
                        "running": {"type": "boolean"},
                        "remaining_ms": {"type": "integer", "minimum": 0},
                    },
                    "required": ["running", "remaining_ms"],
                    "additionalProperties": False,
                },
                "triggers": [
                    {"mode": "FIXED_INTERVAL", "interval_ms": 200, "default": True},
                    {
                        "mode": "EVENT",
                        "event_types": ["event/timer_control"],
                        "default": False,
                    },
                ],
                "implementation": {
                    "kind": "BUILTIN",
                    "entrypoint": "core.timer.v1",
                    "deterministic": True,
                },
                "permissions": ["read-virtual-time"],
                "observability": {"record_outputs": True, "record_state": True},
            }
        ),
        "core-state-machine": CapabilityContract.model_validate(
            {
                "name": "状态机",
                "summary": "依据配置的状态、条件和事件执行确定性转换。",
                "kind": "CONTROLLER",
                "targets": ["WORLD", "MAP_OBJECT", "TOOL", "AGENT", "INTERACTION"],
                "interfaces": ["state-machine"],
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "initial_state": {"type": "string", "minLength": 1},
                        "states": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                        "transitions": {"type": "array"},
                    },
                    "required": ["initial_state", "states", "transitions"],
                    "additionalProperties": False,
                },
                "inputs": [
                    {
                        "key": "transition_request",
                        "name": "转换请求",
                        "data_type": "event/state_transition_request",
                        "multiple": True,
                    }
                ],
                "outputs": [
                    {
                        "key": "state_changed",
                        "name": "状态变化",
                        "data_type": "event/state_changed",
                    },
                    {
                        "key": "current_state",
                        "name": "当前状态",
                        "data_type": "state/machine_state",
                    },
                ],
                "state_schema": {
                    "type": "object",
                    "properties": {"current": {"type": "string"}},
                    "required": ["current"],
                    "additionalProperties": False,
                },
                "triggers": [
                    {
                        "mode": "EVENT",
                        "event_types": ["event/state_transition_request"],
                        "default": True,
                    }
                ],
                "implementation": {
                    "kind": "BUILTIN",
                    "entrypoint": "core.state-machine.v1",
                },
                "observability": {"record_outputs": True, "record_state": True},
            }
        ),
        "spatial-zone-presence": CapabilityContract.model_validate(
            {
                "name": "区域存在感知",
                "summary": "感知实体进入、离开以及区域内数量变化。",
                "kind": "SENSOR",
                "targets": ["ZONE", "MAP_OBJECT", "WORLD"],
                "interfaces": ["zone-presence-sensor"],
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "entity_types": {"type": "array", "items": {"type": "string"}},
                        "debounce_ms": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                "inputs": [],
                "outputs": [
                    {
                        "key": "entered",
                        "name": "实体进入",
                        "data_type": "event/zone_entered",
                        "multiple": True,
                    },
                    {
                        "key": "left",
                        "name": "实体离开",
                        "data_type": "event/zone_left",
                        "multiple": True,
                    },
                    {
                        "key": "presence",
                        "name": "区域存在状态",
                        "data_type": "state/zone_presence",
                    },
                ],
                "state_schema": {
                    "type": "object",
                    "properties": {"count": {"type": "integer", "minimum": 0}},
                    "required": ["count"],
                    "additionalProperties": False,
                },
                "triggers": [
                    {
                        "mode": "EVENT",
                        "event_types": ["event/spatial_position_changed"],
                        "default": True,
                    }
                ],
                "implementation": {
                    "kind": "BUILTIN",
                    "entrypoint": "spatial.zone-presence.v1",
                },
                "permissions": ["read-world-position"],
                "observability": {"record_outputs": True, "record_state": True},
            }
        ),
        "spatial-relative-motion": CapabilityContract.model_validate(
            {
                "name": "相对运动感知",
                "summary": "计算两个实体的距离、相对速度和预计到达时间。",
                "kind": "SENSOR",
                "targets": ["AGENT", "TOOL", "INTERACTION"],
                "interfaces": ["relative-motion-sensor"],
                "parameters_schema": object_schema,
                "inputs": [
                    {
                        "key": "subject_motion",
                        "name": "主体运动状态",
                        "data_type": "state/motion",
                        "required": True,
                    },
                    {
                        "key": "object_motion",
                        "name": "对象运动状态",
                        "data_type": "state/motion",
                        "required": True,
                    },
                ],
                "outputs": [
                    {
                        "key": "relative_motion",
                        "name": "相对运动",
                        "data_type": "state/relative_motion",
                    }
                ],
                "state_schema": object_schema,
                "triggers": [
                    {"mode": "FIXED_INTERVAL", "interval_ms": 200, "default": True}
                ],
                "implementation": {
                    "kind": "BUILTIN",
                    "entrypoint": "spatial.relative-motion.v1",
                },
                "permissions": ["read-world-position"],
                "observability": {"record_outputs": True},
            }
        ),
        "mobility-continuous-walk": CapabilityContract.model_validate(
            {
                "name": "连续步行",
                "summary": "按米制连续坐标和路径约束推进人类身体。",
                "kind": "ACTION",
                "targets": ["AGENT"],
                "interfaces": ["continuous-mobility"],
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "speed_mps": {"type": "number", "minimum": 0},
                        "max_acceleration_mps2": {"type": "number", "minimum": 0},
                    },
                    "required": ["speed_mps"],
                    "additionalProperties": False,
                },
                "inputs": [
                    {
                        "key": "movement_command",
                        "name": "步行动作",
                        "data_type": "command/walk",
                        "required": True,
                    }
                ],
                "outputs": [
                    {
                        "key": "motion",
                        "name": "运动状态",
                        "data_type": "state/motion",
                    }
                ],
                "state_schema": object_schema,
                "triggers": [
                    {"mode": "FIXED_INTERVAL", "interval_ms": 200, "default": True}
                ],
                "implementation": {
                    "kind": "BUILTIN",
                    "entrypoint": "mobility.continuous-walk.v1",
                },
                "permissions": ["control-self-position"],
                "observability": {"record_outputs": True},
            }
        ),
        "mobility-path-follow": CapabilityContract.model_validate(
            {
                "name": "沿路径运动",
                "summary": "让工具实体沿指定网络路径连续运动。",
                "kind": "ACTION",
                "targets": ["TOOL"],
                "interfaces": ["path-mobility"],
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "max_speed_mps": {"type": "number", "minimum": 0},
                        "max_acceleration_mps2": {"type": "number", "minimum": 0},
                        "max_deceleration_mps2": {"type": "number", "minimum": 0},
                    },
                    "required": [
                        "max_speed_mps",
                        "max_acceleration_mps2",
                        "max_deceleration_mps2",
                    ],
                    "additionalProperties": False,
                },
                "inputs": [
                    {
                        "key": "speed_command",
                        "name": "速度控制",
                        "data_type": "command/speed",
                        "required": True,
                    },
                    {
                        "key": "route",
                        "name": "路径",
                        "data_type": "state/route",
                        "required": True,
                    },
                ],
                "outputs": [
                    {
                        "key": "motion",
                        "name": "运动状态",
                        "data_type": "state/motion",
                    }
                ],
                "state_schema": object_schema,
                "triggers": [
                    {"mode": "FIXED_INTERVAL", "interval_ms": 200, "default": True}
                ],
                "implementation": {
                    "kind": "BUILTIN",
                    "entrypoint": "mobility.path-follow.v1",
                },
                "permissions": ["control-tool-position"],
                "observability": {"record_outputs": True},
            }
        ),
        "control-speed": CapabilityContract.model_validate(
            {
                "name": "速度控制",
                "summary": "把高层通行动作转换为目标速度和制动命令。",
                "kind": "CONTROLLER",
                "targets": ["AGENT", "TOOL", "INTERACTION"],
                "interfaces": ["speed-controller"],
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "comfortable_deceleration_mps2": {"type": "number", "minimum": 0},
                        "emergency_deceleration_mps2": {"type": "number", "minimum": 0},
                    },
                    "required": [
                        "comfortable_deceleration_mps2",
                        "emergency_deceleration_mps2",
                    ],
                    "additionalProperties": False,
                },
                "inputs": [
                    {
                        "key": "passage_action",
                        "name": "通行动作",
                        "data_type": "command/passage_action",
                        "required": True,
                    },
                    {
                        "key": "motion",
                        "name": "当前运动",
                        "data_type": "state/motion",
                        "required": True,
                    },
                ],
                "outputs": [
                    {
                        "key": "speed_command",
                        "name": "速度命令",
                        "data_type": "command/speed",
                    }
                ],
                "state_schema": object_schema,
                "triggers": [
                    {"mode": "FIXED_INTERVAL", "interval_ms": 200, "default": True}
                ],
                "implementation": {
                    "kind": "BUILTIN",
                    "entrypoint": "control.speed.v1",
                },
                "observability": {"record_inputs": True, "record_outputs": True},
            }
        ),
        "metrics-minimum-distance": CapabilityContract.model_validate(
            {
                "name": "最近距离观察器",
                "summary": "记录两个运动实体在一次互动中的最小欧氏距离。",
                "kind": "OBSERVER",
                "targets": ["INTERACTION", "ZONE", "WORLD"],
                "interfaces": ["safety-observer"],
                "parameters_schema": object_schema,
                "inputs": [
                    {
                        "key": "motions",
                        "name": "运动状态集合",
                        "data_type": "state/motion",
                        "required": True,
                        "multiple": True,
                    }
                ],
                "outputs": [
                    {
                        "key": "minimum_distance",
                        "name": "最近距离",
                        "data_type": "metric/minimum_distance",
                    }
                ],
                "state_schema": {
                    "type": "object",
                    "properties": {
                        "minimum_distance_m": {"type": "number", "minimum": 0}
                    },
                    "required": ["minimum_distance_m"],
                    "additionalProperties": False,
                },
                "triggers": [
                    {"mode": "FIXED_INTERVAL", "interval_ms": 200, "default": True}
                ],
                "implementation": {
                    "kind": "BUILTIN",
                    "entrypoint": "metrics.minimum-distance.v1",
                },
                "permissions": ["read-world-position"],
                "observability": {
                    "record_outputs": True,
                    "record_state": True,
                    "metric_outputs": ["minimum_distance"],
                },
            }
        ),
        "decision-gap-acceptance": CapabilityContract.model_validate(
            {
                "name": "通行间隙决策",
                "summary": "根据相对运动、信号状态和安全间隙输出继续、让行或制动动作。",
                "kind": "DECISION",
                "targets": ["AGENT", "BRAIN", "INTERACTION"],
                "interfaces": ["gap-acceptance-decision"],
                "parameters_schema": {
                    "type": "object",
                    "properties": {
                        "safe_gap_s": {"type": "number", "minimum": 0},
                        "collision_distance_m": {"type": "number", "minimum": 0},
                        "desired_speed_mps": {"type": "number", "minimum": 0},
                    },
                    "required": [
                        "safe_gap_s",
                        "collision_distance_m",
                        "desired_speed_mps",
                    ],
                    "additionalProperties": False,
                },
                "inputs": [
                    {
                        "key": "relative_motion",
                        "name": "相对运动",
                        "data_type": "state/relative_motion",
                        "required": True,
                    },
                    {
                        "key": "signal_state",
                        "name": "交通信号状态",
                        "data_type": "state/signal",
                    },
                ],
                "outputs": [
                    {
                        "key": "passage_action",
                        "name": "通行动作",
                        "data_type": "command/passage_action",
                    }
                ],
                "state_schema": object_schema,
                "triggers": [{"mode": "DECISION", "default": True}],
                "implementation": {
                    "kind": "BUILTIN",
                    "entrypoint": "traffic.gap-acceptance.v1",
                },
                "permissions": ["read-world-position"],
                "observability": {
                    "record_inputs": True,
                    "record_outputs": True,
                },
            }
        ),
        "adapter-passage-to-walk": CapabilityContract.model_validate(
            {
                "name": "通行动作转步行动作",
                "summary": "把统一的通行决策转换为行人连续运动命令。",
                "kind": "ADAPTER",
                "targets": ["AGENT", "INTERACTION"],
                "interfaces": ["passage-to-walk-adapter"],
                "parameters_schema": object_schema,
                "inputs": [
                    {
                        "key": "passage_action",
                        "name": "通行动作",
                        "data_type": "command/passage_action",
                        "required": True,
                    }
                ],
                "outputs": [
                    {
                        "key": "movement_command",
                        "name": "步行动作",
                        "data_type": "command/walk",
                    }
                ],
                "state_schema": object_schema,
                "triggers": [
                    {"mode": "FIXED_INTERVAL", "interval_ms": 200, "default": True}
                ],
                "implementation": {
                    "kind": "BUILTIN",
                    "entrypoint": "traffic.passage-to-walk.v1",
                },
                "observability": {
                    "record_inputs": True,
                    "record_outputs": True,
                },
            }
        ),
    }


class CapabilityService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_builtin_capabilities(self) -> None:
        """Create immutable baseline contracts without rewriting existing versions."""

        with self.database.session_factory.begin() as session:
            for capability_key, contract in _builtin_contracts().items():
                existing = session.scalar(
                    select(CapabilityDefinition).where(
                        CapabilityDefinition.capability_key == capability_key
                    )
                )
                if existing is not None:
                    continue
                now = _utc_now()
                document = contract.model_dump(mode="json", exclude_none=False)
                capability = CapabilityDefinition(
                    id=str(uuid4()),
                    capability_key=capability_key,
                    name=contract.name,
                    description=contract.summary,
                    status="PUBLISHED",
                    is_builtin=True,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(capability)
                session.flush()
                revision = CapabilityRevision(
                    id=str(uuid4()),
                    capability_id=capability.id,
                    revision_no=1,
                    state="PUBLISHED",
                    schema_version=contract.schema_version,
                    contract_json=document,
                    contract_hash=_digest(document),
                    validation_json={"valid": True, "errors": [], "warnings": []},
                    lock_version=1,
                    created_at=now,
                    updated_at=now,
                    published_at=now,
                )
                session.add(revision)
                session.flush()
                capability.current_published_revision_id = revision.id

    def ensure_builtin_bundles(self) -> None:
        """Seed reusable traffic behavior packages from published atomic abilities."""

        required_keys = {
            "spatial-relative-motion",
            "decision-gap-acceptance",
            "adapter-passage-to-walk",
            "mobility-continuous-walk",
            "control-speed",
            "mobility-path-follow",
            "metrics-minimum-distance",
        }
        with self.database.session_factory.begin() as session:
            definitions = list(
                session.scalars(
                    select(CapabilityDefinition).where(
                        CapabilityDefinition.capability_key.in_(required_keys)
                    )
                )
            )
            revisions = {
                item.capability_key: session.get(
                    CapabilityRevision, item.current_published_revision_id
                )
                for item in definitions
                if item.current_published_revision_id
            }
            if set(revisions) != required_keys or any(
                item is None for item in revisions.values()
            ):
                raise RuntimeError("traffic capability bundle dependencies are unavailable")

            def instance(
                key: str,
                capability_key: str,
                target_ref: str,
                parameters: dict[str, Any],
                *,
                trigger: str,
                interval_ms: int | None = None,
            ) -> dict[str, Any]:
                return {
                    "instance_key": key,
                    "capability_revision_id": revisions[capability_key].id,
                    "target_ref": target_ref,
                    "parameters": parameters,
                    "run_policy": {
                        "trigger": trigger,
                        "interval_ms": interval_ms,
                        "event_types": [],
                    },
                    "enabled": True,
                }

            bundles: dict[str, CapabilityBundleContract] = {
                "relative-motion-perception": CapabilityBundleContract.model_validate(
                    {
                        "name": "相对运动感知",
                        "summary": "把两个实体的运动状态转换为距离、闭合速度和预计碰撞时间。",
                        "targets": ["AGENT", "TOOL", "INTERACTION"],
                        "instances": [
                            instance(
                                "relative_motion",
                                "spatial-relative-motion",
                                "interaction:crossing",
                                {},
                                trigger="FIXED_INTERVAL",
                                interval_ms=200,
                            )
                        ],
                        "bindings": [],
                        "exposed_inputs": [
                            {
                                "key": "subject_motion",
                                "name": "主体运动状态",
                                "endpoint": {
                                    "instance_key": "relative_motion",
                                    "port_key": "subject_motion",
                                },
                                "required": True,
                            },
                            {
                                "key": "object_motion",
                                "name": "对象运动状态",
                                "endpoint": {
                                    "instance_key": "relative_motion",
                                    "port_key": "object_motion",
                                },
                                "required": True,
                            },
                        ],
                        "exposed_outputs": [
                            {
                                "key": "relative_motion",
                                "name": "相对运动",
                                "endpoint": {
                                    "instance_key": "relative_motion",
                                    "port_key": "relative_motion",
                                },
                            }
                        ],
                    }
                ),
                "pedestrian-crossing-behavior": CapabilityBundleContract.model_validate(
                    {
                        "name": "行人过街行为",
                        "summary": "可复用的安全间隙判断、通行动作适配与连续步行组合。",
                        "targets": ["AGENT", "BRAIN", "INTERACTION"],
                        "instances": [
                            instance(
                                "gap_decision",
                                "decision-gap-acceptance",
                                "agent:subject",
                                {
                                    "safe_gap_s": 3.0,
                                    "collision_distance_m": 0.5,
                                    "desired_speed_mps": 1.4,
                                },
                                trigger="DECISION",
                            ),
                            instance(
                                "walk_adapter",
                                "adapter-passage-to-walk",
                                "agent:subject",
                                {},
                                trigger="FIXED_INTERVAL",
                                interval_ms=200,
                            ),
                            instance(
                                "walking",
                                "mobility-continuous-walk",
                                "agent:subject",
                                {"speed_mps": 1.4, "max_acceleration_mps2": 1.2},
                                trigger="FIXED_INTERVAL",
                                interval_ms=200,
                            ),
                        ],
                        "bindings": [
                            {
                                "binding_key": "decision_to_adapter",
                                "source": {
                                    "instance_key": "gap_decision",
                                    "port_key": "passage_action",
                                },
                                "target": {
                                    "instance_key": "walk_adapter",
                                    "port_key": "passage_action",
                                },
                            },
                            {
                                "binding_key": "adapter_to_walk",
                                "source": {
                                    "instance_key": "walk_adapter",
                                    "port_key": "movement_command",
                                },
                                "target": {
                                    "instance_key": "walking",
                                    "port_key": "movement_command",
                                },
                            },
                        ],
                        "exposed_inputs": [
                            {
                                "key": "relative_motion",
                                "name": "与来车的相对运动",
                                "endpoint": {
                                    "instance_key": "gap_decision",
                                    "port_key": "relative_motion",
                                },
                                "required": True,
                            }
                        ],
                        "exposed_outputs": [
                            {
                                "key": "motion",
                                "name": "行人运动状态",
                                "endpoint": {
                                    "instance_key": "walking",
                                    "port_key": "motion",
                                },
                            }
                        ],
                    }
                ),
                "vehicle-yield-behavior": CapabilityBundleContract.model_validate(
                    {
                        "name": "车辆让行行为",
                        "summary": "可复用的驾驶员安全间隙判断、速度控制和车辆路径运动组合。",
                        "targets": ["AGENT", "BRAIN", "TOOL", "INTERACTION"],
                        "instances": [
                            instance(
                                "gap_decision",
                                "decision-gap-acceptance",
                                "agent:subject",
                                {
                                    "safe_gap_s": 4.0,
                                    "collision_distance_m": 0.5,
                                    "desired_speed_mps": 8.0,
                                },
                                trigger="DECISION",
                            ),
                            instance(
                                "speed_control",
                                "control-speed",
                                "tool:vehicle",
                                {
                                    "comfortable_deceleration_mps2": 3.0,
                                    "emergency_deceleration_mps2": 7.0,
                                },
                                trigger="FIXED_INTERVAL",
                                interval_ms=200,
                            ),
                            instance(
                                "path_follow",
                                "mobility-path-follow",
                                "tool:vehicle",
                                {
                                    "max_speed_mps": 13.9,
                                    "max_acceleration_mps2": 2.5,
                                    "max_deceleration_mps2": 7.0,
                                },
                                trigger="FIXED_INTERVAL",
                                interval_ms=200,
                            ),
                        ],
                        "bindings": [
                            {
                                "binding_key": "decision_to_speed",
                                "source": {
                                    "instance_key": "gap_decision",
                                    "port_key": "passage_action",
                                },
                                "target": {
                                    "instance_key": "speed_control",
                                    "port_key": "passage_action",
                                },
                            },
                            {
                                "binding_key": "speed_to_path",
                                "source": {
                                    "instance_key": "speed_control",
                                    "port_key": "speed_command",
                                },
                                "target": {
                                    "instance_key": "path_follow",
                                    "port_key": "speed_command",
                                },
                            },
                        ],
                        "exposed_inputs": [
                            {
                                "key": "relative_motion",
                                "name": "与行人的相对运动",
                                "endpoint": {
                                    "instance_key": "gap_decision",
                                    "port_key": "relative_motion",
                                },
                                "required": True,
                            },
                            {
                                "key": "current_motion",
                                "name": "车辆当前运动",
                                "endpoint": {
                                    "instance_key": "speed_control",
                                    "port_key": "motion",
                                },
                                "required": True,
                            },
                            {
                                "key": "route",
                                "name": "车辆路线",
                                "endpoint": {
                                    "instance_key": "path_follow",
                                    "port_key": "route",
                                },
                                "required": True,
                            },
                        ],
                        "exposed_outputs": [
                            {
                                "key": "motion",
                                "name": "车辆运动状态",
                                "endpoint": {
                                    "instance_key": "path_follow",
                                    "port_key": "motion",
                                },
                            }
                        ],
                    }
                ),
                "crossing-safety-observation": CapabilityBundleContract.model_validate(
                    {
                        "name": "过街安全观测",
                        "summary": "从多个运动实体持续计算最近距离，供停止条件和结果报告复用。",
                        "targets": ["INTERACTION", "ZONE", "WORLD"],
                        "instances": [
                            instance(
                                "minimum_distance",
                                "metrics-minimum-distance",
                                "interaction:crossing",
                                {},
                                trigger="FIXED_INTERVAL",
                                interval_ms=200,
                            )
                        ],
                        "bindings": [],
                        "exposed_inputs": [
                            {
                                "key": "motions",
                                "name": "参与者运动状态",
                                "endpoint": {
                                    "instance_key": "minimum_distance",
                                    "port_key": "motions",
                                },
                                "required": True,
                            }
                        ],
                        "exposed_outputs": [
                            {
                                "key": "minimum_distance",
                                "name": "最近距离",
                                "endpoint": {
                                    "instance_key": "minimum_distance",
                                    "port_key": "minimum_distance",
                                },
                            }
                        ],
                    }
                ),
            }
            for bundle_key, composition in bundles.items():
                if session.scalar(
                    select(CapabilityBundle.id).where(
                        CapabilityBundle.bundle_key == bundle_key
                    )
                ):
                    continue
                errors = self._bundle_publish_errors(session, composition)
                if errors:
                    raise RuntimeError(
                        f"invalid built-in capability bundle {bundle_key}: {errors}"
                    )
                now = _utc_now()
                document = composition.model_dump(mode="json", exclude_none=False)
                bundle = CapabilityBundle(
                    id=str(uuid4()),
                    bundle_key=bundle_key,
                    name=composition.name,
                    description=composition.summary,
                    status="PUBLISHED",
                    is_builtin=True,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(bundle)
                session.flush()
                revision = CapabilityBundleRevision(
                    id=str(uuid4()),
                    bundle_id=bundle.id,
                    revision_no=1,
                    state="PUBLISHED",
                    schema_version=composition.schema_version,
                    composition_json=document,
                    composition_hash=_digest(document),
                    validation_json={"valid": True, "errors": [], "warnings": []},
                    lock_version=1,
                    created_at=now,
                    updated_at=now,
                    published_at=now,
                )
                session.add(revision)
                session.flush()
                bundle.current_published_revision_id = revision.id

    def create_capability(
        self,
        *,
        name: str,
        description: str = "",
        capability_key: str | None = None,
        source_revision_id: str | None = None,
        contract: CapabilityContract | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ServiceError(
                "INVALID_CAPABILITY_NAME", "能力名称不能为空", status_code=422
            )
        stable_key = capability_key or _generated_key(name, "capability")
        with self.database.session_factory.begin() as session:
            if session.scalar(
                select(CapabilityDefinition.id).where(
                    CapabilityDefinition.capability_key == stable_key
                )
            ):
                raise ServiceError(
                    "CAPABILITY_KEY_CONFLICT", "能力稳定键已被使用", status_code=409
                )
            source: CapabilityRevision | None = None
            if source_revision_id:
                source = session.get(CapabilityRevision, source_revision_id)
                if source is None or source.state != "PUBLISHED":
                    raise not_found("capability_revision", source_revision_id)
                contract_model = CapabilityContract.model_validate(source.contract_json)
            elif contract is not None:
                contract_model = CapabilityContract.model_validate(contract)
            else:
                contract_model = CapabilityContract.model_validate(
                    {
                        "name": name,
                        "summary": description,
                        "kind": "DECISION",
                        "targets": ["BRAIN"],
                        "parameters_schema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        "inputs": [],
                        "outputs": [],
                        "state_schema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        "triggers": [{"mode": "DECISION", "default": True}],
                        "implementation": {
                            "kind": "WORKFLOW",
                            "config": {},
                            "deterministic": True,
                        },
                    }
                )
            document = contract_model.model_dump(mode="json", exclude_none=False)
            now = _utc_now()
            capability = CapabilityDefinition(
                id=str(uuid4()),
                capability_key=stable_key,
                name=name,
                description=description.strip()[:10_000],
                status="DRAFT",
                is_builtin=False,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(capability)
            session.flush()
            revision = CapabilityRevision(
                id=str(uuid4()),
                capability_id=capability.id,
                revision_no=1,
                state="DRAFT",
                base_revision_id=source.id if source else None,
                schema_version=contract_model.schema_version,
                contract_json=document,
                contract_hash=_digest(document),
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(revision)
            session.flush()
            capability.current_draft_revision_id = revision.id
            return self._capability_detail(session, capability)

    def list_capabilities(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        target: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ServiceError(
                "INVALID_PAGINATION", "能力分页参数无效", status_code=422
            )
        normalized_status = status.upper() if status else None
        if normalized_status not in {None, "DRAFT", "PUBLISHED"}:
            raise ServiceError(
                "INVALID_CAPABILITY_STATUS", "能力状态筛选无效", status_code=422
            )
        with self.database.session_factory() as session:
            statement = select(CapabilityDefinition)
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                statement = statement.where(
                    or_(
                        CapabilityDefinition.name.ilike(pattern),
                        CapabilityDefinition.capability_key.ilike(pattern),
                    )
                )
            if normalized_status:
                statement = statement.where(
                    CapabilityDefinition.status == normalized_status
                )
            rows = list(
                session.scalars(
                    statement.order_by(
                        CapabilityDefinition.is_builtin.desc(),
                        CapabilityDefinition.updated_at.desc(),
                        CapabilityDefinition.id.desc(),
                    )
                )
            )
            items = [self._capability_detail(session, item) for item in rows]
            if kind:
                normalized_kind = kind.upper()
                items = [
                    item
                    for item in items
                    if (item.get("active_contract") or {}).get("kind") == normalized_kind
                ]
            if target:
                normalized_target = target.upper()
                items = [
                    item
                    for item in items
                    if normalized_target
                    in ((item.get("active_contract") or {}).get("targets") or [])
                ]
            total = len(items)
            start = (page - 1) * page_size
            return {
                "items": items[start : start + page_size],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, ceil(total / page_size)),
                "status_counts": _status_counts(session, CapabilityDefinition),
            }

    def get_capability(self, capability_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            capability = session.get(CapabilityDefinition, capability_id)
            if capability is None:
                raise not_found("capability", capability_id)
            return self._capability_detail(session, capability)

    def get_capability_draft(self, capability_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            capability, revision = self._require_capability_draft(
                session, capability_id
            )
            return self._capability_revision_detail(revision, capability)

    def update_capability_draft(
        self,
        capability_id: str,
        *,
        expected_lock_version: int,
        contract: CapabilityContract | dict[str, Any],
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        contract_model = CapabilityContract.model_validate(contract)
        document = contract_model.model_dump(mode="json", exclude_none=False)
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            capability, revision = self._require_capability_draft(
                session, capability_id
            )
            result = session.execute(
                update(CapabilityRevision)
                .where(
                    CapabilityRevision.id == revision.id,
                    CapabilityRevision.state == "DRAFT",
                    CapabilityRevision.lock_version == expected_lock_version,
                )
                .values(
                    schema_version=contract_model.schema_version,
                    contract_json=document,
                    contract_hash=_digest(document),
                    validation_json=None,
                    lock_version=CapabilityRevision.lock_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                actual = session.scalar(
                    select(CapabilityRevision.lock_version).where(
                        CapabilityRevision.id == revision.id
                    )
                )
                raise ServiceError(
                    "CAPABILITY_REVISION_CONFLICT",
                    "能力草稿已被其他请求修改，请重新载入",
                    status_code=409,
                    details={
                        "expected_lock_version": expected_lock_version,
                        "actual_lock_version": actual,
                    },
                )
            if name is not None:
                normalized_name = name.strip()
                if not normalized_name:
                    raise ServiceError(
                        "INVALID_CAPABILITY_NAME", "能力名称不能为空", status_code=422
                    )
                capability.name = normalized_name
            if description is not None:
                capability.description = description.strip()[:10_000]
            capability.row_version += 1
            capability.updated_at = now
            session.flush()
            refreshed = session.get(CapabilityRevision, revision.id)
            return self._capability_revision_detail(refreshed, capability)

    def publish_capability_draft(
        self,
        capability_id: str,
        *,
        draft_revision_id: str,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            capability, revision = self._require_capability_draft(
                session, capability_id
            )
            if capability.is_builtin:
                raise ServiceError(
                    "BUILTIN_CAPABILITY_IMMUTABLE",
                    "系统内置能力不可修改",
                    status_code=409,
                )
            if (
                revision.id != draft_revision_id
                or revision.lock_version != expected_lock_version
            ):
                raise ServiceError(
                    "CAPABILITY_REVISION_CONFLICT",
                    "能力草稿版本已经变化",
                    status_code=409,
                )
            contract = CapabilityContract.model_validate(revision.contract_json)
            errors = self._capability_publish_errors(session, contract)
            if errors:
                revision.validation_json = {
                    "valid": False,
                    "errors": errors,
                    "warnings": [],
                }
                raise ServiceError(
                    "CAPABILITY_VALIDATION_FAILED",
                    "能力没有通过发布校验",
                    status_code=422,
                    details=revision.validation_json,
                )
            now = _utc_now()
            revision.contract_json = contract.model_dump(
                mode="json", exclude_none=False
            )
            revision.contract_hash = _digest(revision.contract_json)
            revision.validation_json = {
                "valid": True,
                "errors": [],
                "warnings": [],
            }
            revision.state = "PUBLISHED"
            revision.published_at = now
            revision.updated_at = now
            capability.current_draft_revision_id = None
            capability.current_published_revision_id = revision.id
            capability.status = "PUBLISHED"
            capability.row_version += 1
            capability.updated_at = now
            session.flush()
            return self._capability_revision_detail(revision, capability)

    def list_capability_revisions(self, capability_id: str) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            capability = session.get(CapabilityDefinition, capability_id)
            if capability is None:
                raise not_found("capability", capability_id)
            revisions = list(
                session.scalars(
                    select(CapabilityRevision)
                    .where(CapabilityRevision.capability_id == capability_id)
                    .order_by(CapabilityRevision.revision_no.desc())
                )
            )
            return [
                self._capability_revision_detail(item, capability, include_contract=False)
                for item in revisions
            ]

    def get_capability_revision(
        self, capability_id: str, revision_id: str
    ) -> dict[str, Any]:
        with self.database.session_factory() as session:
            capability = session.get(CapabilityDefinition, capability_id)
            revision = session.get(CapabilityRevision, revision_id)
            if capability is None:
                raise not_found("capability", capability_id)
            if revision is None or revision.capability_id != capability_id:
                raise not_found("capability_revision", revision_id)
            return self._capability_revision_detail(revision, capability)

    def fork_capability_revision(
        self, capability_id: str, revision_id: str
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            capability = session.get(CapabilityDefinition, capability_id)
            if capability is None:
                raise not_found("capability", capability_id)
            if capability.is_builtin:
                raise ServiceError(
                    "BUILTIN_CAPABILITY_IMMUTABLE",
                    "请从内置能力创建一个新的自定义能力",
                    status_code=409,
                )
            if capability.current_draft_revision_id:
                raise ServiceError(
                    "CAPABILITY_DRAFT_EXISTS",
                    "该能力已有编辑中的草稿",
                    status_code=409,
                )
            source = session.get(CapabilityRevision, revision_id)
            if (
                source is None
                or source.capability_id != capability_id
                or source.state != "PUBLISHED"
            ):
                raise not_found("capability_revision", revision_id)
            number = int(
                session.scalar(
                    select(func.max(CapabilityRevision.revision_no)).where(
                        CapabilityRevision.capability_id == capability_id
                    )
                )
                or 0
            ) + 1
            now = _utc_now()
            draft = CapabilityRevision(
                id=str(uuid4()),
                capability_id=capability_id,
                revision_no=number,
                state="DRAFT",
                base_revision_id=source.id,
                schema_version=source.schema_version,
                contract_json=copy.deepcopy(source.contract_json),
                contract_hash=source.contract_hash,
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            capability.current_draft_revision_id = draft.id
            capability.status = "DRAFT"
            capability.row_version += 1
            capability.updated_at = now
            return self._capability_revision_detail(draft, capability)

    def create_bundle(
        self,
        *,
        name: str,
        description: str = "",
        bundle_key: str | None = None,
        source_revision_id: str | None = None,
        composition: CapabilityBundleContract | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ServiceError("INVALID_BUNDLE_NAME", "能力包名称不能为空", status_code=422)
        stable_key = bundle_key or _generated_key(name, "bundle")
        with self.database.session_factory.begin() as session:
            if session.scalar(
                select(CapabilityBundle.id).where(
                    CapabilityBundle.bundle_key == stable_key
                )
            ):
                raise ServiceError(
                    "CAPABILITY_BUNDLE_KEY_CONFLICT",
                    "能力包稳定键已被使用",
                    status_code=409,
                )
            source: CapabilityBundleRevision | None = None
            if source_revision_id:
                source = session.get(CapabilityBundleRevision, source_revision_id)
                if source is None or source.state != "PUBLISHED":
                    raise not_found("capability_bundle_revision", source_revision_id)
                contract = CapabilityBundleContract.model_validate(source.composition_json)
            elif composition is not None:
                contract = CapabilityBundleContract.model_validate(composition)
            else:
                raise ServiceError(
                    "CAPABILITY_BUNDLE_COMPOSITION_REQUIRED",
                    "创建能力包必须至少装配一个能力",
                    status_code=422,
                )
            document = contract.model_dump(mode="json", exclude_none=False)
            now = _utc_now()
            bundle = CapabilityBundle(
                id=str(uuid4()),
                bundle_key=stable_key,
                name=name,
                description=description.strip()[:10_000],
                status="DRAFT",
                is_builtin=False,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(bundle)
            session.flush()
            revision = CapabilityBundleRevision(
                id=str(uuid4()),
                bundle_id=bundle.id,
                revision_no=1,
                state="DRAFT",
                base_revision_id=source.id if source else None,
                schema_version=contract.schema_version,
                composition_json=document,
                composition_hash=_digest(document),
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(revision)
            session.flush()
            bundle.current_draft_revision_id = revision.id
            return self._bundle_detail(session, bundle)

    def list_bundles(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ServiceError("INVALID_PAGINATION", "能力包分页参数无效", status_code=422)
        normalized_status = status.upper() if status else None
        if normalized_status not in {None, "DRAFT", "PUBLISHED"}:
            raise ServiceError(
                "INVALID_CAPABILITY_BUNDLE_STATUS", "能力包状态筛选无效", status_code=422
            )
        with self.database.session_factory() as session:
            statement = select(CapabilityBundle)
            count_statement = select(func.count()).select_from(CapabilityBundle)
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                predicate = or_(
                    CapabilityBundle.name.ilike(pattern),
                    CapabilityBundle.bundle_key.ilike(pattern),
                )
                statement = statement.where(predicate)
                count_statement = count_statement.where(predicate)
            if normalized_status:
                statement = statement.where(CapabilityBundle.status == normalized_status)
                count_statement = count_statement.where(
                    CapabilityBundle.status == normalized_status
                )
            total = int(session.scalar(count_statement) or 0)
            rows = list(
                session.scalars(
                    statement.order_by(
                        CapabilityBundle.updated_at.desc(), CapabilityBundle.id.desc()
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return {
                "items": [self._bundle_detail(session, item) for item in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, ceil(total / page_size)),
                "status_counts": _status_counts(session, CapabilityBundle),
            }

    def get_bundle(self, bundle_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            bundle = session.get(CapabilityBundle, bundle_id)
            if bundle is None:
                raise not_found("capability_bundle", bundle_id)
            return self._bundle_detail(session, bundle)

    def get_bundle_draft(self, bundle_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            bundle, revision = self._require_bundle_draft(session, bundle_id)
            return self._bundle_revision_detail(revision, bundle)

    def update_bundle_draft(
        self,
        bundle_id: str,
        *,
        expected_lock_version: int,
        composition: CapabilityBundleContract | dict[str, Any],
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        contract = CapabilityBundleContract.model_validate(composition)
        document = contract.model_dump(mode="json", exclude_none=False)
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            bundle, revision = self._require_bundle_draft(session, bundle_id)
            result = session.execute(
                update(CapabilityBundleRevision)
                .where(
                    CapabilityBundleRevision.id == revision.id,
                    CapabilityBundleRevision.state == "DRAFT",
                    CapabilityBundleRevision.lock_version == expected_lock_version,
                )
                .values(
                    schema_version=contract.schema_version,
                    composition_json=document,
                    composition_hash=_digest(document),
                    validation_json=None,
                    lock_version=CapabilityBundleRevision.lock_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                actual = session.scalar(
                    select(CapabilityBundleRevision.lock_version).where(
                        CapabilityBundleRevision.id == revision.id
                    )
                )
                raise ServiceError(
                    "CAPABILITY_BUNDLE_REVISION_CONFLICT",
                    "能力包草稿已被其他请求修改，请重新载入",
                    status_code=409,
                    details={
                        "expected_lock_version": expected_lock_version,
                        "actual_lock_version": actual,
                    },
                )
            if name is not None:
                normalized_name = name.strip()
                if not normalized_name:
                    raise ServiceError(
                        "INVALID_BUNDLE_NAME", "能力包名称不能为空", status_code=422
                    )
                bundle.name = normalized_name
            if description is not None:
                bundle.description = description.strip()[:10_000]
            bundle.row_version += 1
            bundle.updated_at = now
            session.flush()
            return self._bundle_revision_detail(
                session.get(CapabilityBundleRevision, revision.id), bundle
            )

    def publish_bundle_draft(
        self,
        bundle_id: str,
        *,
        draft_revision_id: str,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            bundle, revision = self._require_bundle_draft(session, bundle_id)
            if (
                revision.id != draft_revision_id
                or revision.lock_version != expected_lock_version
            ):
                raise ServiceError(
                    "CAPABILITY_BUNDLE_REVISION_CONFLICT",
                    "能力包草稿版本已经变化",
                    status_code=409,
                )
            composition = CapabilityBundleContract.model_validate(
                revision.composition_json
            )
            errors = self._bundle_publish_errors(session, composition)
            if errors:
                revision.validation_json = {
                    "valid": False,
                    "errors": errors,
                    "warnings": [],
                }
                raise ServiceError(
                    "CAPABILITY_BUNDLE_VALIDATION_FAILED",
                    "能力包没有通过发布校验",
                    status_code=422,
                    details=revision.validation_json,
                )
            now = _utc_now()
            revision.composition_json = composition.model_dump(
                mode="json", exclude_none=False
            )
            revision.composition_hash = _digest(revision.composition_json)
            revision.validation_json = {
                "valid": True,
                "errors": [],
                "warnings": [],
            }
            revision.state = "PUBLISHED"
            revision.published_at = now
            revision.updated_at = now
            bundle.current_draft_revision_id = None
            bundle.current_published_revision_id = revision.id
            bundle.status = "PUBLISHED"
            bundle.row_version += 1
            bundle.updated_at = now
            session.flush()
            return self._bundle_revision_detail(revision, bundle)

    def list_bundle_revisions(self, bundle_id: str) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            bundle = session.get(CapabilityBundle, bundle_id)
            if bundle is None:
                raise not_found("capability_bundle", bundle_id)
            revisions = list(
                session.scalars(
                    select(CapabilityBundleRevision)
                    .where(CapabilityBundleRevision.bundle_id == bundle_id)
                    .order_by(CapabilityBundleRevision.revision_no.desc())
                )
            )
            return [
                self._bundle_revision_detail(item, bundle, include_composition=False)
                for item in revisions
            ]

    def get_bundle_revision(
        self, bundle_id: str, revision_id: str
    ) -> dict[str, Any]:
        with self.database.session_factory() as session:
            bundle = session.get(CapabilityBundle, bundle_id)
            revision = session.get(CapabilityBundleRevision, revision_id)
            if bundle is None:
                raise not_found("capability_bundle", bundle_id)
            if revision is None or revision.bundle_id != bundle_id:
                raise not_found("capability_bundle_revision", revision_id)
            return self._bundle_revision_detail(revision, bundle)

    def fork_bundle_revision(self, bundle_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            bundle = session.get(CapabilityBundle, bundle_id)
            if bundle is None:
                raise not_found("capability_bundle", bundle_id)
            if bundle.current_draft_revision_id:
                raise ServiceError(
                    "CAPABILITY_BUNDLE_DRAFT_EXISTS",
                    "该能力包已有编辑中的草稿",
                    status_code=409,
                )
            source = session.get(CapabilityBundleRevision, revision_id)
            if (
                source is None
                or source.bundle_id != bundle_id
                or source.state != "PUBLISHED"
            ):
                raise not_found("capability_bundle_revision", revision_id)
            number = int(
                session.scalar(
                    select(func.max(CapabilityBundleRevision.revision_no)).where(
                        CapabilityBundleRevision.bundle_id == bundle_id
                    )
                )
                or 0
            ) + 1
            now = _utc_now()
            draft = CapabilityBundleRevision(
                id=str(uuid4()),
                bundle_id=bundle_id,
                revision_no=number,
                state="DRAFT",
                base_revision_id=source.id,
                schema_version=source.schema_version,
                composition_json=copy.deepcopy(source.composition_json),
                composition_hash=source.composition_hash,
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            bundle.current_draft_revision_id = draft.id
            bundle.status = "DRAFT"
            bundle.row_version += 1
            bundle.updated_at = now
            return self._bundle_revision_detail(draft, bundle)

    @staticmethod
    def _capability_publish_errors(
        session: Session, contract: CapabilityContract
    ) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        implementation = contract.implementation
        if implementation.kind == "BUILTIN":
            from generative_agents.runtime.capability_engine import (
                BUILTIN_CAPABILITY_HANDLERS,
            )

            if implementation.entrypoint not in BUILTIN_CAPABILITY_HANDLERS:
                errors.append(
                    {
                        "code": "CAPABILITY_BUILTIN_UNREGISTERED",
                        "path": "implementation.entrypoint",
                        "message": "内置能力入口没有对应的运行时实现",
                    }
                )
        elif implementation.kind == "PYTHON":
            from generative_agents.runtime.workflow_functions import (
                validate_inline_workflow_function,
            )

            try:
                validate_inline_workflow_function(implementation.source or "")
            except ValueError as exc:
                errors.append(
                    {
                        "code": "CAPABILITY_PYTHON_INVALID",
                        "path": "implementation.source",
                        "message": str(exc),
                    }
                )
        elif implementation.kind == "RULES":
            rules = implementation.config.get("rules") or []
            source = (implementation.source or "").strip()
            if not rules and source not in {"", "return {}", "return inputs"}:
                errors.append(
                    {
                        "code": "CAPABILITY_RULES_UNSUPPORTED",
                        "path": "implementation",
                        "message": "规则能力必须使用结构化 rules，或受支持的 return 表达式",
                    }
                )
        elif implementation.kind == "WORKFLOW" and not (
            implementation.entrypoint
            or implementation.config.get("workflow_key")
        ):
            errors.append(
                {
                    "code": "CAPABILITY_WORKFLOW_ENTRYPOINT_REQUIRED",
                    "path": "implementation.entrypoint",
                    "message": "工作流能力必须声明可执行工作流入口",
                }
            )
        elif implementation.kind == "LLM" and not contract.outputs:
            errors.append(
                {
                    "code": "CAPABILITY_LLM_OUTPUT_REQUIRED",
                    "path": "outputs",
                    "message": "LLM 能力必须声明结构化输出端口",
                }
            )
        for index, dependency in enumerate(contract.dependencies):
            if not dependency.capability_revision_id:
                continue
            revision = session.get(
                CapabilityRevision, dependency.capability_revision_id
            )
            if revision is None or revision.state != "PUBLISHED":
                errors.append(
                    {
                        "code": "CAPABILITY_DEPENDENCY_UNAVAILABLE",
                        "path": f"dependencies.{index}.capability_revision_id",
                        "message": "依赖必须引用已发布的能力版本",
                    }
                )
        return errors

    @staticmethod
    def _bundle_publish_errors(
        session: Session, composition: CapabilityBundleContract
    ) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        contracts: dict[str, CapabilityContract] = {}
        for index, instance in enumerate(composition.instances):
            revision = session.get(
                CapabilityRevision, instance.capability_revision_id
            )
            if revision is None or revision.state != "PUBLISHED":
                errors.append(
                    {
                        "code": "CAPABILITY_REVISION_UNAVAILABLE",
                        "path": f"instances.{index}.capability_revision_id",
                        "message": "能力实例必须引用已发布的能力版本",
                    }
                )
                continue
            contract = CapabilityContract.model_validate(revision.contract_json)
            contracts[instance.instance_key] = contract
            try:
                validate_json_schema(
                    instance.parameters,
                    contract.parameters_schema,
                    f"$.instances[{index}].parameters",
                )
            except ValueError as exc:
                errors.append(
                    {
                        "code": "CAPABILITY_PARAMETERS_INVALID",
                        "path": f"instances.{index}.parameters",
                        "message": str(exc),
                    }
                )
            supported = {trigger.mode for trigger in contract.triggers}
            if instance.run_policy.trigger not in supported:
                errors.append(
                    {
                        "code": "CAPABILITY_TRIGGER_UNSUPPORTED",
                        "path": f"instances.{index}.run_policy.trigger",
                        "message": "实例运行策略不在能力支持的触发方式中",
                    }
                )

        incoming: dict[tuple[str, str], int] = {}
        graph: dict[str, set[str]] = {}
        for index, binding in enumerate(composition.bindings):
            source = contracts.get(binding.source.instance_key)
            target = contracts.get(binding.target.instance_key)
            if source is None or target is None:
                continue
            output = next(
                (item for item in source.outputs if item.key == binding.source.port_key),
                None,
            )
            input_port = next(
                (item for item in target.inputs if item.key == binding.target.port_key),
                None,
            )
            if output is None:
                errors.append(
                    {
                        "code": "CAPABILITY_OUTPUT_NOT_FOUND",
                        "path": f"bindings.{index}.source.port_key",
                        "message": "连接引用了不存在的输出端口",
                    }
                )
            if input_port is None:
                errors.append(
                    {
                        "code": "CAPABILITY_INPUT_NOT_FOUND",
                        "path": f"bindings.{index}.target.port_key",
                        "message": "连接引用了不存在的输入端口",
                    }
                )
            if output is None or input_port is None:
                continue
            if (
                output.data_type != input_port.data_type
                and output.data_type != "any"
                and input_port.data_type != "any"
            ):
                errors.append(
                    {
                        "code": "CAPABILITY_PORT_TYPE_MISMATCH",
                        "path": f"bindings.{index}",
                        "message": (
                            f"输出 {output.data_type} 不能连接到输入 "
                            f"{input_port.data_type}"
                        ),
                    }
                )
            key = (binding.target.instance_key, binding.target.port_key)
            incoming[key] = incoming.get(key, 0) + 1
            if incoming[key] > 1 and not input_port.multiple:
                errors.append(
                    {
                        "code": "CAPABILITY_INPUT_MULTIPLICITY_EXCEEDED",
                        "path": f"bindings.{index}.target",
                        "message": "该输入端口不允许多个来源",
                    }
                )
            if binding.delivery == "LATEST":
                graph.setdefault(binding.source.instance_key, set()).add(
                    binding.target.instance_key
                )

        exposed_input_targets: set[tuple[str, str]] = set()
        for index, exposure in enumerate(composition.exposed_inputs):
            contract = contracts.get(exposure.endpoint.instance_key)
            input_port = next(
                (
                    item
                    for item in (contract.inputs if contract else [])
                    if item.key == exposure.endpoint.port_key
                ),
                None,
            )
            if input_port is None:
                errors.append(
                    {
                        "code": "CAPABILITY_EXPOSED_INPUT_NOT_FOUND",
                        "path": f"exposed_inputs.{index}.endpoint",
                        "message": "公开输入引用了不存在的能力输入端口",
                    }
                )
                continue
            key = (exposure.endpoint.instance_key, exposure.endpoint.port_key)
            if key in exposed_input_targets:
                errors.append(
                    {
                        "code": "CAPABILITY_EXPOSED_INPUT_DUPLICATE",
                        "path": f"exposed_inputs.{index}.endpoint",
                        "message": "同一个能力输入端口不能被重复公开",
                    }
                )
            exposed_input_targets.add(key)
            if incoming.get(key, 0) and not input_port.multiple:
                errors.append(
                    {
                        "code": "CAPABILITY_EXPOSED_INPUT_ALREADY_BOUND",
                        "path": f"exposed_inputs.{index}.endpoint",
                        "message": "非多值输入不能同时接受内部连线和外部输入",
                    }
                )

        exposed_output_sources: set[tuple[str, str]] = set()
        for index, exposure in enumerate(composition.exposed_outputs):
            contract = contracts.get(exposure.endpoint.instance_key)
            output_port = next(
                (
                    item
                    for item in (contract.outputs if contract else [])
                    if item.key == exposure.endpoint.port_key
                ),
                None,
            )
            if output_port is None:
                errors.append(
                    {
                        "code": "CAPABILITY_EXPOSED_OUTPUT_NOT_FOUND",
                        "path": f"exposed_outputs.{index}.endpoint",
                        "message": "公开输出引用了不存在的能力输出端口",
                    }
                )
                continue
            key = (exposure.endpoint.instance_key, exposure.endpoint.port_key)
            if key in exposed_output_sources:
                errors.append(
                    {
                        "code": "CAPABILITY_EXPOSED_OUTPUT_DUPLICATE",
                        "path": f"exposed_outputs.{index}.endpoint",
                        "message": "同一个能力输出端口不能被重复公开",
                    }
                )
            exposed_output_sources.add(key)

        for instance_key, contract in contracts.items():
            for port in contract.inputs:
                key = (instance_key, port.key)
                if (
                    port.required
                    and incoming.get(key, 0) == 0
                    and key not in exposed_input_targets
                ):
                    errors.append(
                        {
                            "code": "CAPABILITY_REQUIRED_INPUT_UNBOUND",
                            "path": f"instances.{instance_key}.inputs.{port.key}",
                            "message": "必需输入端口尚未连接",
                        }
                    )

        if CapabilityService._has_cycle(graph):
            errors.append(
                {
                    "code": "CAPABILITY_SYNCHRONOUS_BINDING_CYCLE",
                    "path": "bindings",
                    "message": "LATEST 同步连接存在环；请改用事件队列或增加状态延迟",
                }
            )
        return errors

    @staticmethod
    def _has_cycle(graph: dict[str, set[str]]) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            if any(visit(child) for child in graph.get(node, ())):
                return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in graph)

    @staticmethod
    def _require_capability_draft(
        session: Session, capability_id: str
    ) -> tuple[CapabilityDefinition, CapabilityRevision]:
        capability = session.get(CapabilityDefinition, capability_id)
        if capability is None:
            raise not_found("capability", capability_id)
        revision = (
            session.get(CapabilityRevision, capability.current_draft_revision_id)
            if capability.current_draft_revision_id
            else None
        )
        if (
            revision is None
            or revision.capability_id != capability_id
            or revision.state != "DRAFT"
        ):
            raise ServiceError(
                "CAPABILITY_DRAFT_UNAVAILABLE",
                "能力没有可编辑草稿",
                status_code=409,
            )
        return capability, revision

    @staticmethod
    def _require_bundle_draft(
        session: Session, bundle_id: str
    ) -> tuple[CapabilityBundle, CapabilityBundleRevision]:
        bundle = session.get(CapabilityBundle, bundle_id)
        if bundle is None:
            raise not_found("capability_bundle", bundle_id)
        revision = (
            session.get(CapabilityBundleRevision, bundle.current_draft_revision_id)
            if bundle.current_draft_revision_id
            else None
        )
        if (
            revision is None
            or revision.bundle_id != bundle_id
            or revision.state != "DRAFT"
        ):
            raise ServiceError(
                "CAPABILITY_BUNDLE_DRAFT_UNAVAILABLE",
                "能力包没有可编辑草稿",
                status_code=409,
            )
        return bundle, revision

    @staticmethod
    def _capability_revision_summary(
        revision: CapabilityRevision | None,
    ) -> dict[str, Any] | None:
        if revision is None:
            return None
        return {
            "id": revision.id,
            "revision_no": revision.revision_no,
            "state": revision.state,
            "schema_version": revision.schema_version,
            "contract_hash": revision.contract_hash,
            "lock_version": revision.lock_version,
            "updated_at": revision.updated_at.isoformat(),
            "published_at": (
                revision.published_at.isoformat() if revision.published_at else None
            ),
        }

    def _capability_detail(
        self, session: Session, capability: CapabilityDefinition
    ) -> dict[str, Any]:
        draft = (
            session.get(CapabilityRevision, capability.current_draft_revision_id)
            if capability.current_draft_revision_id
            else None
        )
        published = (
            session.get(
                CapabilityRevision, capability.current_published_revision_id
            )
            if capability.current_published_revision_id
            else None
        )
        active = draft or published
        return {
            "id": capability.id,
            "capability_key": capability.capability_key,
            "name": capability.name,
            "description": capability.description,
            "status": capability.status,
            "is_builtin": capability.is_builtin,
            "row_version": capability.row_version,
            "current_draft": self._capability_revision_summary(draft),
            "current_published": self._capability_revision_summary(published),
            "active_contract": copy.deepcopy(active.contract_json) if active else None,
            "created_at": capability.created_at.isoformat(),
            "updated_at": capability.updated_at.isoformat(),
        }

    def _capability_revision_detail(
        self,
        revision: CapabilityRevision,
        capability: CapabilityDefinition,
        *,
        include_contract: bool = True,
    ) -> dict[str, Any]:
        result = self._capability_revision_summary(revision) or {}
        result.update(
            {
                "capability_id": capability.id,
                "capability_key": capability.capability_key,
                "capability_name": capability.name,
                "base_revision_id": revision.base_revision_id,
                "validation": revision.validation_json,
                "readonly": revision.state == "PUBLISHED",
            }
        )
        if include_contract:
            result["contract"] = copy.deepcopy(revision.contract_json)
        return result

    @staticmethod
    def _bundle_revision_summary(
        revision: CapabilityBundleRevision | None,
    ) -> dict[str, Any] | None:
        if revision is None:
            return None
        return {
            "id": revision.id,
            "revision_no": revision.revision_no,
            "state": revision.state,
            "schema_version": revision.schema_version,
            "composition_hash": revision.composition_hash,
            "lock_version": revision.lock_version,
            "updated_at": revision.updated_at.isoformat(),
            "published_at": (
                revision.published_at.isoformat() if revision.published_at else None
            ),
        }

    def _bundle_detail(
        self, session: Session, bundle: CapabilityBundle
    ) -> dict[str, Any]:
        draft = (
            session.get(
                CapabilityBundleRevision, bundle.current_draft_revision_id
            )
            if bundle.current_draft_revision_id
            else None
        )
        published = (
            session.get(
                CapabilityBundleRevision, bundle.current_published_revision_id
            )
            if bundle.current_published_revision_id
            else None
        )
        active = draft or published
        return {
            "id": bundle.id,
            "bundle_key": bundle.bundle_key,
            "name": bundle.name,
            "description": bundle.description,
            "status": bundle.status,
            "is_builtin": bundle.is_builtin,
            "row_version": bundle.row_version,
            "current_draft": self._bundle_revision_summary(draft),
            "current_published": self._bundle_revision_summary(published),
            "instance_count": (
                len((active.composition_json or {}).get("instances", [])) if active else 0
            ),
            "binding_count": (
                len((active.composition_json or {}).get("bindings", [])) if active else 0
            ),
            "targets": (
                list((active.composition_json or {}).get("targets", [])) if active else []
            ),
            "active_composition": (
                copy.deepcopy(active.composition_json) if active else None
            ),
            "created_at": bundle.created_at.isoformat(),
            "updated_at": bundle.updated_at.isoformat(),
        }

    def _bundle_revision_detail(
        self,
        revision: CapabilityBundleRevision,
        bundle: CapabilityBundle,
        *,
        include_composition: bool = True,
    ) -> dict[str, Any]:
        result = self._bundle_revision_summary(revision) or {}
        result.update(
            {
                "bundle_id": bundle.id,
                "bundle_key": bundle.bundle_key,
                "bundle_name": bundle.name,
                "base_revision_id": revision.base_revision_id,
                "validation": revision.validation_json,
                "readonly": revision.state == "PUBLISHED",
            }
        )
        if include_composition:
            result["composition"] = copy.deepcopy(revision.composition_json)
        return result


__all__ = ["CapabilityService"]
