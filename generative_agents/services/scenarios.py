"""Experiment-level capability assembly, validation, and schedule compilation."""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from math import ceil
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from generative_agents.config import ExperimentDefinition
from generative_agents.config.capabilities import (
    CapabilityBundleContract,
    CapabilityContract,
)
from generative_agents.config.hashing import canonical_json_bytes
from generative_agents.config.scenarios import ExperimentCapabilityExtension
from generative_agents.config.spatial_assets import SpatialAssetContract
from generative_agents.config.tools import ToolContract
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    AgentTemplateRevision,
    CapabilityBundleRevision,
    CapabilityRevision,
    Experiment,
    ExperimentRevision,
    ExperimentRevisionCapability,
    SpatialAssetRevision,
    ToolRevision,
    WorldMapRevision,
)
from generative_agents.runtime.json_schema import validate_json_schema

from .errors import ServiceError, not_found


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def validate_experiment_capability_in_session(
    session: Session,
    revision: ExperimentRevision | None,
    extension: ExperimentCapabilityExtension,
) -> list[dict[str, Any]]:
    if extension.mode == "LEGACY_TOWN":
        return []
    errors: list[dict[str, Any]] = []
    channel_producers: dict[str, list[tuple[str, str]]] = {}
    channel_consumers: dict[str, list[tuple[str, str, bool]]] = {}
    map_revision = session.get(WorldMapRevision, extension.map_revision_id)
    if map_revision is None or map_revision.state != "PUBLISHED":
        errors.append(
            {
                "code": "SCENARIO_MAP_REVISION_UNAVAILABLE",
                "path": "map_revision_id",
                "message": "能力场景必须引用已发布地图版本",
            }
        )
    placement_keys = {
        item.get("instance_key")
        for item in (
            ((map_revision.world_json.get("definition") or {}).get("spatial_scene") or {}).get(
                "placements", []
            )
            if map_revision is not None
            else []
        )
        if item.get("instance_key")
    }
    definition = (
        ExperimentDefinition.model_validate(revision.definition_json)
        if revision is not None
        else None
    )
    experiment_agents = {item.agent_key for item in definition.agents} if definition else set()
    for index, actor in enumerate(extension.actors):
        if definition is not None and actor.experiment_agent_key not in experiment_agents:
            errors.append(
                {
                    "code": "SCENARIO_EXPERIMENT_AGENT_NOT_FOUND",
                    "path": f"actors.{index}.experiment_agent_key",
                    "message": "场景角色必须引用当前实验中的 Agent",
                }
            )
        if actor.agent_revision_id:
            agent_revision = session.get(
                AgentTemplateRevision, actor.agent_revision_id
            )
            if agent_revision is None or agent_revision.state != "PUBLISHED":
                errors.append(
                    {
                        "code": "SCENARIO_AGENT_REVISION_UNAVAILABLE",
                        "path": f"actors.{index}.agent_revision_id",
                        "message": "角色来源必须是已发布 Agent 版本",
                    }
                )

    tool_by_key: dict[str, ToolContract] = {}
    for index, instance in enumerate(extension.tool_instances):
        tool_revision = session.get(ToolRevision, instance.tool_revision_id)
        if tool_revision is None or tool_revision.state != "PUBLISHED":
            errors.append(
                {
                    "code": "SCENARIO_TOOL_REVISION_UNAVAILABLE",
                    "path": f"tool_instances.{index}.tool_revision_id",
                    "message": "工具实例必须引用已发布工具版本",
                }
            )
            continue
        tool_by_key[instance.instance_key] = ToolContract.model_validate(
            tool_revision.contract_json
        )
    for index, actor in enumerate(extension.actors):
        if actor.active_tool_instance_key:
            tool = tool_by_key.get(actor.active_tool_instance_key)
            if tool is not None and tool.mobility.mode == "NONE":
                errors.append(
                    {
                        "code": "SCENARIO_ACTIVE_TOOL_NOT_MOBILE",
                        "path": f"actors.{index}.active_tool_instance_key",
                        "message": "角色的活动交通工具必须具有移动能力",
                    }
                )

    def register_attachment_channels(attachment, path: str, target_ref: str) -> None:
        if not attachment.enabled:
            return
        input_ports: dict[str, Any] = {}
        output_ports: dict[str, Any] = {}
        intervals: list[int] = []
        if attachment.capability_revision_id:
            capability_revision = session.get(
                CapabilityRevision, attachment.capability_revision_id
            )
            if capability_revision is None or capability_revision.state != "PUBLISHED":
                return
            capability = CapabilityContract.model_validate(
                capability_revision.contract_json
            )
            input_ports = {item.key: item for item in capability.inputs}
            output_ports = {item.key: item for item in capability.outputs}
            trigger = next(item for item in capability.triggers if item.default)
            if trigger.mode == "FIXED_INTERVAL" and trigger.interval_ms:
                intervals.append(trigger.interval_ms)
        else:
            bundle_revision = session.get(
                CapabilityBundleRevision, attachment.capability_bundle_revision_id
            )
            if bundle_revision is None or bundle_revision.state != "PUBLISHED":
                return
            bundle = CapabilityBundleContract.model_validate(
                bundle_revision.composition_json
            )
            contracts: dict[str, CapabilityContract] = {}
            for instance in bundle.instances:
                capability_revision = session.get(
                    CapabilityRevision, instance.capability_revision_id
                )
                if capability_revision is None or capability_revision.state != "PUBLISHED":
                    continue
                contracts[instance.instance_key] = CapabilityContract.model_validate(
                    capability_revision.contract_json
                )
                if (
                    instance.enabled
                    and instance.run_policy.trigger == "FIXED_INTERVAL"
                    and instance.run_policy.interval_ms
                ):
                    intervals.append(instance.run_policy.interval_ms)
            for exposure in bundle.exposed_inputs:
                contract = contracts.get(exposure.endpoint.instance_key)
                port = next(
                    (
                        item
                        for item in (contract.inputs if contract else [])
                        if item.key == exposure.endpoint.port_key
                    ),
                    None,
                )
                if port:
                    input_ports[exposure.key] = port
            for exposure in bundle.exposed_outputs:
                contract = contracts.get(exposure.endpoint.instance_key)
                port = next(
                    (
                        item
                        for item in (contract.outputs if contract else [])
                        if item.key == exposure.endpoint.port_key
                    ),
                    None,
                )
                if port:
                    output_ports[exposure.key] = port
        for key, channel_ref in attachment.input_bindings.items():
            channel_ref = channel_ref.replace("${target}", target_ref).replace(
                "${target_key}", target_ref.split(":", 1)[-1]
            )
            port = input_ports.get(key)
            if port:
                channel_consumers.setdefault(channel_ref, []).append(
                    (port.data_type, f"{path}.input_bindings.{key}", port.multiple)
                )
        for key, channel_ref in attachment.output_bindings.items():
            channel_ref = channel_ref.replace("${target}", target_ref).replace(
                "${target_key}", target_ref.split(":", 1)[-1]
            )
            port = output_ports.get(key)
            if port:
                channel_producers.setdefault(channel_ref, []).append(
                    (port.data_type, f"{path}.output_bindings.{key}")
                )
        if any(interval % extension.clock.base_tick_ms for interval in intervals):
            errors.append(
                {
                    "code": "SCENARIO_ATTACHMENT_INTERVAL_NOT_ALIGNED",
                    "path": path,
                    "message": "资产附件能力的执行间隔必须是实验基础 tick 的整数倍",
                }
            )

    if map_revision is not None and map_revision.state == "PUBLISHED":
        placements = (
            ((map_revision.world_json.get("definition") or {}).get("spatial_scene") or {}).get(
                "placements", []
            )
        )
        for placement_index, placement in enumerate(placements):
            asset_revision = session.get(
                SpatialAssetRevision, placement.get("spatial_asset_revision_id")
            )
            if asset_revision is None or asset_revision.state != "PUBLISHED":
                continue
            contract = SpatialAssetContract.model_validate(asset_revision.contract_json)
            target_ref = (
                f"zone:{placement['instance_key']}"
                if contract.kind == "ZONE"
                else f"map-object:{placement['instance_key']}"
            )
            for attachment_index, attachment in enumerate(
                contract.capability_attachments
            ):
                register_attachment_channels(
                    attachment,
                    f"map.placements.{placement_index}.attachments.{attachment_index}",
                    target_ref,
                )
    for tool_index, instance in enumerate(extension.tool_instances):
        contract = tool_by_key.get(instance.instance_key)
        if contract is None:
            continue
        for attachment_index, attachment in enumerate(contract.capability_attachments):
            register_attachment_channels(
                attachment,
                f"tool_instances.{tool_index}.attachments.{attachment_index}",
                f"tool:{instance.instance_key}",
            )

    for index, mount in enumerate(extension.capability_mounts):
        bundle_revision = session.get(
            CapabilityBundleRevision, mount.capability_bundle_revision_id
        )
        if bundle_revision is None or bundle_revision.state != "PUBLISHED":
            errors.append(
                {
                    "code": "SCENARIO_CAPABILITY_BUNDLE_UNAVAILABLE",
                    "path": f"capability_mounts.{index}.capability_bundle_revision_id",
                    "message": "场景挂载必须引用已发布能力包版本",
                }
            )
            continue
        bundle = CapabilityBundleContract.model_validate(
            bundle_revision.composition_json
        )
        contracts_by_instance: dict[str, CapabilityContract] = {}
        for instance in bundle.instances:
            capability_revision = session.get(
                CapabilityRevision, instance.capability_revision_id
            )
            if capability_revision is not None and capability_revision.state == "PUBLISHED":
                contracts_by_instance[instance.instance_key] = CapabilityContract.model_validate(
                    capability_revision.contract_json
                )
            alias = instance.target_ref.split(":", 1)[-1]
            resolved_target = mount.target_bindings.get(
                instance.target_ref,
                mount.target_bindings.get(alias, instance.target_ref),
            )
            target_kind, _, target_key = resolved_target.partition(":")
            target_valid = (
                target_kind in {"interaction", "world", "brain"}
                or target_kind in {"actor", "agent"}
                and target_key in {item.actor_key for item in extension.actors}
                or target_kind == "tool"
                and target_key in {item.instance_key for item in extension.tool_instances}
                or target_kind in {"map-object", "zone"}
                and target_key in placement_keys
            )
            if not target_valid:
                errors.append(
                    {
                        "code": "SCENARIO_CAPABILITY_TARGET_UNRESOLVED",
                        "path": f"capability_mounts.{index}.target_bindings",
                        "message": f"能力实例 {instance.instance_key} 的目标 {resolved_target} 无法解析",
                    }
                )
        exposed_inputs = {item.key: item for item in bundle.exposed_inputs}
        exposed_outputs = {item.key: item for item in bundle.exposed_outputs}
        unknown_inputs = sorted(set(mount.input_bindings) - set(exposed_inputs))
        unknown_outputs = sorted(set(mount.output_bindings) - set(exposed_outputs))
        if unknown_inputs:
            errors.append(
                {
                    "code": "SCENARIO_BUNDLE_INPUT_UNKNOWN",
                    "path": f"capability_mounts.{index}.input_bindings",
                    "message": f"能力包没有公开输入：{', '.join(unknown_inputs)}",
                }
            )
        if unknown_outputs:
            errors.append(
                {
                    "code": "SCENARIO_BUNDLE_OUTPUT_UNKNOWN",
                    "path": f"capability_mounts.{index}.output_bindings",
                    "message": f"能力包没有公开输出：{', '.join(unknown_outputs)}",
                }
            )
        missing_inputs = sorted(
            item.key
            for item in bundle.exposed_inputs
            if item.required and item.key not in mount.input_bindings
        )
        if missing_inputs:
            errors.append(
                {
                    "code": "SCENARIO_BUNDLE_INPUT_REQUIRED",
                    "path": f"capability_mounts.{index}.input_bindings",
                    "message": f"能力包缺少场景输入：{', '.join(missing_inputs)}",
                }
            )
        for exposure in bundle.exposed_inputs:
            channel_ref = mount.input_bindings.get(exposure.key)
            contract = contracts_by_instance.get(exposure.endpoint.instance_key)
            port = next(
                (
                    item
                    for item in (contract.inputs if contract else [])
                    if item.key == exposure.endpoint.port_key
                ),
                None,
            )
            if channel_ref and port:
                channel_consumers.setdefault(channel_ref, []).append(
                    (
                        port.data_type,
                        f"capability_mounts.{index}.input_bindings.{exposure.key}",
                        port.multiple,
                    )
                )
        for exposure in bundle.exposed_outputs:
            channel_ref = mount.output_bindings.get(exposure.key)
            contract = contracts_by_instance.get(exposure.endpoint.instance_key)
            port = next(
                (
                    item
                    for item in (contract.outputs if contract else [])
                    if item.key == exposure.endpoint.port_key
                ),
                None,
            )
            if channel_ref and port:
                channel_producers.setdefault(channel_ref, []).append(
                    (
                        port.data_type,
                        f"capability_mounts.{index}.output_bindings.{exposure.key}",
                    )
                )
        try:
            validate_json_schema(
                mount.parameters,
                bundle.exposed_parameters_schema,
                f"$.capability_mounts[{index}].parameters",
            )
        except ValueError as exc:
            errors.append(
                {
                    "code": "SCENARIO_BUNDLE_PARAMETERS_INVALID",
                    "path": f"capability_mounts.{index}.parameters",
                    "message": str(exc),
                }
            )
        for instance in bundle.instances:
            if (
                instance.enabled
                and instance.run_policy.trigger == "FIXED_INTERVAL"
                and instance.run_policy.interval_ms % extension.clock.base_tick_ms
            ):
                errors.append(
                    {
                        "code": "SCENARIO_INTERVAL_NOT_ALIGNED",
                        "path": f"capability_mounts.{index}.{instance.instance_key}.run_policy",
                        "message": "能力执行间隔必须是实验基础 tick 的整数倍",
                    }
                )

    def state_channel_type(channel_ref: str) -> str | None:
        if not channel_ref.startswith("state:"):
            return None
        suffix = channel_ref.rsplit(":", 1)[-1]
        return {
            "motion": "state/motion",
            "motions": "state/motion",
            "route": "state/route",
            "signal": "state/signal",
            "state": "state/signal",
        }.get(suffix)

    for channel_ref, producers in channel_producers.items():
        if len(producers) > 1:
            errors.append(
                {
                    "code": "SCENARIO_CHANNEL_MULTIPLE_PRODUCERS",
                    "path": producers[1][1],
                    "message": f"场景通道 {channel_ref} 只能有一个生产者",
                }
            )
    for channel_ref, consumers in channel_consumers.items():
        producers = channel_producers.get(channel_ref, [])
        source_type = producers[0][0] if producers else state_channel_type(channel_ref)
        if source_type is None:
            errors.append(
                {
                    "code": "SCENARIO_CHANNEL_PRODUCER_MISSING",
                    "path": consumers[0][1],
                    "message": f"场景输入通道 {channel_ref} 没有生产者，也不是系统状态通道",
                }
            )
            continue
        for target_type, path, _multiple in consumers:
            if source_type not in {"any", target_type} and target_type != "any":
                errors.append(
                    {
                        "code": "SCENARIO_CHANNEL_TYPE_MISMATCH",
                        "path": path,
                        "message": f"通道 {channel_ref} 的 {source_type} 不能连接到 {target_type}",
                    }
                )
    return errors


def composed_scenario_requires_models_in_session(
    session: Session, revision: ExperimentRevision
) -> bool:
    """Return whether the active composed graph can invoke LLM-backed code.

    Legacy experiments always require their Chat/Embedding services.  A fully
    deterministic composed graph must not be blocked by, billed for, or shown
    model probes that it will never execute.
    """

    from generative_agents.runtime.capability_snapshot import (
        build_capability_runtime_snapshot,
    )

    try:
        snapshot = build_capability_runtime_snapshot(session, revision)
    except (RuntimeError, ValueError):
        # Validation owns the detailed error for a broken assembly.  Model
        # probing remains conservative until the graph can be snapshotted.
        return True
    if snapshot is None:
        return True
    return any(
        (document.get("contract") or {}).get("implementation", {}).get("kind")
        in {"LLM", "WORKFLOW"}
        for document in (snapshot.get("capabilities") or {}).values()
    )


class ScenarioAssemblyService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def default_extension() -> ExperimentCapabilityExtension:
        return ExperimentCapabilityExtension()

    def get_draft(self, experiment_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            experiment, revision = self._require_draft(session, experiment_id)
            return self._detail(session, experiment, revision)

    def get_revision(
        self, experiment_id: str, revision_id: str
    ) -> dict[str, Any]:
        with self.database.session_factory() as session:
            experiment = session.get(Experiment, experiment_id)
            revision = session.get(ExperimentRevision, revision_id)
            if experiment is None:
                raise not_found("experiment", experiment_id)
            if revision is None or revision.experiment_id != experiment.id:
                raise not_found("revision", revision_id)
            return self._detail(session, experiment, revision)

    def update_draft(
        self,
        experiment_id: str,
        *,
        expected_lock_version: int,
        extension: ExperimentCapabilityExtension | dict[str, Any],
    ) -> dict[str, Any]:
        model = ExperimentCapabilityExtension.model_validate(extension)
        document = model.model_dump(mode="json", exclude_none=False)
        now = _now()
        with self.database.session_factory.begin() as session:
            experiment, revision = self._require_draft(session, experiment_id)
            result = session.execute(
                update(ExperimentRevision)
                .where(
                    ExperimentRevision.id == revision.id,
                    ExperimentRevision.lock_version == expected_lock_version,
                    ExperimentRevision.state == "DRAFT",
                )
                .values(
                    lock_version=ExperimentRevision.lock_version + 1,
                    validation_json=None,
                    validated_hash=None,
                    snapshot_complete=False,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise ServiceError(
                    "REVISION_CONFLICT",
                    "实验草稿已被其他请求修改，请重新载入",
                    status_code=409,
                )
            row = session.get(ExperimentRevisionCapability, revision.id)
            if row is None:
                row = ExperimentRevisionCapability(
                    revision_id=revision.id,
                    experiment_id=experiment.id,
                    schema_version=model.schema_version,
                    extension_json=document,
                    extension_hash=_digest(document),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.schema_version = model.schema_version
                row.extension_json = document
                row.extension_hash = _digest(document)
                row.updated_at = now
            experiment.updated_at = now
            experiment.row_version += 1
            session.flush()
            refreshed = session.get(ExperimentRevision, revision.id)
            return self._detail(session, experiment, refreshed)

    def validate_draft(self, experiment_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            experiment, revision = self._require_draft(session, experiment_id)
            row = session.get(ExperimentRevisionCapability, revision.id)
            extension = ExperimentCapabilityExtension.model_validate(
                row.extension_json if row else self.default_extension()
            )
            errors = validate_experiment_capability_in_session(
                session, revision, extension
            )
            return {
                "valid": not errors,
                "errors": errors,
                "warnings": [],
                "schedule": self._compile_schedule(session, extension),
            }

    def copy_extension(
        self,
        session: Session,
        *,
        source_revision_id: str,
        target_revision_id: str,
        experiment_id: str,
        now: datetime,
    ) -> None:
        source = session.get(ExperimentRevisionCapability, source_revision_id)
        if source is None:
            return
        session.add(
            ExperimentRevisionCapability(
                revision_id=target_revision_id,
                experiment_id=experiment_id,
                schema_version=source.schema_version,
                extension_json=copy.deepcopy(source.extension_json),
                extension_hash=source.extension_hash,
                created_at=now,
                updated_at=now,
            )
        )

    @staticmethod
    def validate_for_publish(
        session: Session, revision: ExperimentRevision
    ) -> list[dict[str, Any]]:
        row = session.get(ExperimentRevisionCapability, revision.id)
        if row is None:
            return []
        extension = ExperimentCapabilityExtension.model_validate(row.extension_json)
        return validate_experiment_capability_in_session(session, revision, extension)

    def _compile_schedule(
        self, session: Session, extension: ExperimentCapabilityExtension
    ) -> dict[str, Any]:
        if extension.mode == "LEGACY_TOWN":
            return {"mode": "LEGACY_TOWN", "tasks": [], "total_executions": 0}
        tasks: list[dict[str, Any]] = []
        actor_intervals = {
            item.actor_key: item.reasoning_interval_ms for item in extension.actors
        }

        def append_capability_task(
            *,
            task_key: str,
            capability_revision_id: str,
            trigger: str,
            interval_ms: int | None,
            source_kind: str,
            source_ref: str,
            target_ref: str,
            input_bindings: dict[str, str] | None = None,
            output_bindings: dict[str, str] | None = None,
        ) -> None:
            capability_revision = session.get(
                CapabilityRevision, capability_revision_id
            )
            if capability_revision is None:
                return
            capability = CapabilityContract.model_validate(
                capability_revision.contract_json
            )
            executions = (
                ceil(extension.clock.duration_ms / interval_ms)
                if interval_ms
                else 0
            )
            tasks.append(
                {
                    "task_key": task_key,
                    "capability_revision_id": capability_revision_id,
                    "capability_name": capability.name,
                    "capability_kind": capability.kind,
                    "implementation_kind": capability.implementation.kind,
                    "trigger": trigger,
                    "interval_ms": interval_ms,
                    "estimated_executions": executions,
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                    "target_ref": target_ref,
                    "input_bindings": input_bindings or {},
                    "output_bindings": output_bindings or {},
                }
            )

        def append_attachment_tasks(
            attachment,
            *,
            task_prefix: str,
            source_kind: str,
            source_ref: str,
            target_ref: str,
        ) -> None:
            if not attachment.enabled:
                return
            inputs = {
                key: value.replace("${target}", target_ref).replace(
                    "${target_key}", target_ref.split(":", 1)[-1]
                )
                for key, value in attachment.input_bindings.items()
            }
            outputs = {
                key: value.replace("${target}", target_ref).replace(
                    "${target_key}", target_ref.split(":", 1)[-1]
                )
                for key, value in attachment.output_bindings.items()
            }
            if attachment.capability_revision_id:
                revision = session.get(
                    CapabilityRevision, attachment.capability_revision_id
                )
                if revision is None:
                    return
                contract = CapabilityContract.model_validate(revision.contract_json)
                trigger = next(item for item in contract.triggers if item.default)
                append_capability_task(
                    task_key=f"{task_prefix}.attached",
                    capability_revision_id=revision.id,
                    trigger=trigger.mode,
                    interval_ms=trigger.interval_ms,
                    source_kind=source_kind,
                    source_ref=source_ref,
                    target_ref=target_ref,
                    input_bindings=inputs,
                    output_bindings=outputs,
                )
                return
            revision = session.get(
                CapabilityBundleRevision, attachment.capability_bundle_revision_id
            )
            if revision is None:
                return
            bundle = CapabilityBundleContract.model_validate(revision.composition_json)
            for instance in bundle.instances:
                if not instance.enabled:
                    continue
                append_capability_task(
                    task_key=f"{task_prefix}.{instance.instance_key}",
                    capability_revision_id=instance.capability_revision_id,
                    trigger=instance.run_policy.trigger,
                    interval_ms=instance.run_policy.interval_ms,
                    source_kind=source_kind,
                    source_ref=source_ref,
                    target_ref=target_ref,
                    input_bindings=inputs,
                    output_bindings=outputs,
                )

        for mount in extension.capability_mounts:
            if not mount.enabled:
                continue
            revision = session.get(
                CapabilityBundleRevision, mount.capability_bundle_revision_id
            )
            if revision is None:
                continue
            bundle = CapabilityBundleContract.model_validate(revision.composition_json)
            for instance in bundle.instances:
                if not instance.enabled:
                    continue
                interval = instance.run_policy.interval_ms
                if instance.run_policy.trigger == "DECISION":
                    target_alias = instance.target_ref.split(":", 1)[-1]
                    resolved_target = mount.target_bindings.get(
                        target_alias, instance.target_ref
                    )
                    actor_key = resolved_target.split(":", 1)[-1]
                    interval = actor_intervals.get(actor_key, 60_000)
                append_capability_task(
                    task_key=f"{mount.mount_key}.{instance.instance_key}",
                    capability_revision_id=instance.capability_revision_id,
                    trigger=instance.run_policy.trigger,
                    interval_ms=interval,
                    source_kind="SCENARIO_MOUNT",
                    source_ref=mount.mount_key,
                    target_ref=instance.target_ref,
                    input_bindings=dict(mount.input_bindings),
                    output_bindings=dict(mount.output_bindings),
                )
        map_revision = session.get(WorldMapRevision, extension.map_revision_id)
        scene = (
            ((map_revision.world_json.get("definition") or {}).get("spatial_scene") or {})
            if map_revision is not None
            else {}
        )
        for placement in scene.get("placements") or []:
            revision = session.get(
                SpatialAssetRevision, placement.get("spatial_asset_revision_id")
            )
            if revision is None:
                continue
            contract = SpatialAssetContract.model_validate(revision.contract_json)
            target_ref = (
                f"zone:{placement['instance_key']}"
                if contract.kind == "ZONE"
                else f"map-object:{placement['instance_key']}"
            )
            for attachment in contract.capability_attachments:
                append_attachment_tasks(
                    attachment,
                    task_prefix=(
                        f"asset-{placement['instance_key']}-{attachment.attachment_key}"
                    ),
                    source_kind="SPATIAL_ASSET_ATTACHMENT",
                    source_ref=placement["instance_key"],
                    target_ref=target_ref,
                )
        for tool in extension.tool_instances:
            revision = session.get(ToolRevision, tool.tool_revision_id)
            if revision is None:
                continue
            contract = ToolContract.model_validate(revision.contract_json)
            for attachment in contract.capability_attachments:
                append_attachment_tasks(
                    attachment,
                    task_prefix=f"tool-{tool.instance_key}-{attachment.attachment_key}",
                    source_kind="TOOL_ATTACHMENT",
                    source_ref=tool.instance_key,
                    target_ref=f"tool:{tool.instance_key}",
                )
        return {
            "mode": extension.mode,
            "base_tick_ms": extension.clock.base_tick_ms,
            "duration_ms": extension.clock.duration_ms,
            "tasks": tasks,
            "total_executions": sum(item["estimated_executions"] for item in tasks),
            "estimated_llm_decisions": sum(
                item["estimated_executions"]
                for item in tasks
                if item["implementation_kind"] == "LLM"
            ),
        }

    @staticmethod
    def _require_draft(
        session: Session, experiment_id: str
    ) -> tuple[Experiment, ExperimentRevision]:
        experiment = session.get(Experiment, experiment_id)
        if experiment is None:
            raise not_found("experiment", experiment_id)
        revision = (
            session.get(ExperimentRevision, experiment.current_draft_revision_id)
            if experiment.current_draft_revision_id
            else None
        )
        if revision is None or revision.state != "DRAFT":
            raise ServiceError(
                "DRAFT_BASE_UNAVAILABLE", "当前实验没有可编辑草稿", status_code=409
            )
        return experiment, revision

    def _detail(
        self,
        session: Session,
        experiment: Experiment,
        revision: ExperimentRevision,
    ) -> dict[str, Any]:
        row = session.get(ExperimentRevisionCapability, revision.id)
        extension = ExperimentCapabilityExtension.model_validate(
            row.extension_json if row else self.default_extension()
        )
        document = extension.model_dump(mode="json", exclude_none=False)
        return {
            "experiment_id": experiment.id,
            "revision_id": revision.id,
            "revision_state": revision.state,
            "lock_version": revision.lock_version,
            "schema_version": extension.schema_version,
            "extension": document,
            "extension_hash": row.extension_hash if row else _digest(document),
            "readonly": revision.state == "PUBLISHED",
            "is_default": row is None,
            "schedule": self._compile_schedule(session, extension),
        }


__all__ = [
    "ScenarioAssemblyService",
    "composed_scenario_requires_models_in_session",
    "validate_experiment_capability_in_session",
]
