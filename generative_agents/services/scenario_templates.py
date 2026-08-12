"""Versioned scenario blueprints and experiment-slot instantiation."""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import func, select, update

from generative_agents.config import ExperimentDefinition
from generative_agents.config.hashing import canonical_json_bytes
from generative_agents.config.capabilities import normalize_contract_key
from generative_agents.config.scenario_templates import ScenarioTemplateContract
from generative_agents.config.scenarios import ExperimentCapabilityExtension
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    CapabilityBundle,
    Experiment,
    ExperimentRevision,
    ExperimentRevisionCapability,
    ScenarioTemplate,
    ScenarioTemplateRevision,
    ToolDefinition,
    WorldMap,
)

from .errors import ServiceError, not_found


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


class ScenarioTemplateService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_builtin_templates(self) -> None:
        with self.database.session_factory.begin() as session:
            existing = session.scalar(
                select(ScenarioTemplate.id).where(
                    ScenarioTemplate.template_key == "one-car-one-pedestrian"
                )
            )
            template = session.get(ScenarioTemplate, existing) if existing else None
            world_map = session.scalar(
                select(WorldMap).where(
                    WorldMap.map_key == "standard-3lane-intersection"
                )
            )
            car = session.scalar(
                select(ToolDefinition).where(ToolDefinition.tool_key == "generic-car")
            )
            bundle_keys = {
                "relative-motion-perception",
                "pedestrian-crossing-behavior",
                "vehicle-yield-behavior",
                "crossing-safety-observation",
            }
            bundles = list(
                session.scalars(
                    select(CapabilityBundle).where(
                        CapabilityBundle.bundle_key.in_(bundle_keys)
                    )
                )
            )
            bundle_revisions = {
                item.bundle_key: item.current_published_revision_id for item in bundles
            }
            if (
                world_map is None
                or not world_map.current_published_revision_id
                or car is None
                or not car.current_published_revision_id
                or set(bundle_revisions) != bundle_keys
                or not all(bundle_revisions.values())
            ):
                raise RuntimeError("one-car-one-pedestrian template dependencies unavailable")

            def mount(
                key: str,
                bundle_key: str,
                *,
                targets: dict[str, str],
                inputs: dict[str, str],
                outputs: dict[str, str],
            ) -> dict[str, Any]:
                return {
                    "mount_key": key,
                    "capability_bundle_revision_id": bundle_revisions[bundle_key],
                    "target_bindings": targets,
                    "parameters": {},
                    "input_bindings": inputs,
                    "output_bindings": outputs,
                }

            contract = ScenarioTemplateContract.model_validate(
                {
                    "name": "一辆车 × 一名行人：无信号过街博弈",
                    "summary": (
                        "在标准四向三车道路口中，用相对运动感知、安全间隙决策、"
                        "连续运动和最小距离观测研究人车让行博弈。"
                    ),
                    "tags": ["traffic", "pedestrian-safety", "game"],
                    "actor_slots": [
                        {
                            "slot_key": "pedestrian",
                            "name": "过街行人",
                            "role": "PEDESTRIAN",
                            "description": "从南侧人行道向北穿越冲突区。",
                        },
                        {
                            "slot_key": "driver",
                            "name": "车辆驾驶员",
                            "role": "DRIVER",
                            "description": "驾驶汽车从西向东接近冲突区。",
                        },
                    ],
                    "blueprint": {
                        "mode": "CAPABILITY_COMPOSED",
                        "map_revision_id": world_map.current_published_revision_id,
                        "clock": {
                            "base_tick_ms": 100,
                            "duration_ms": 20_000,
                            "snapshot_interval_ms": 1_000,
                        },
                        "actors": [
                            {
                                "actor_key": "pedestrian",
                                "experiment_agent_key": "pedestrian",
                                "role": "PEDESTRIAN",
                                "initial_pose": {
                                    "x_m": 24,
                                    "y_m": 18,
                                    "heading_degrees": 90,
                                },
                                "route": [
                                    {
                                        "x_m": 24,
                                        "y_m": 36,
                                        "heading_degrees": 90,
                                    }
                                ],
                                "reasoning_interval_ms": 200,
                            },
                            {
                                "actor_key": "driver",
                                "experiment_agent_key": "driver",
                                "role": "DRIVER",
                                "initial_pose": {
                                    "x_m": 8,
                                    "y_m": 24,
                                    "heading_degrees": 0,
                                },
                                "reasoning_interval_ms": 200,
                                "active_tool_instance_key": "car-one",
                            },
                        ],
                        "tool_instances": [
                            {
                                "instance_key": "car-one",
                                "tool_revision_id": car.current_published_revision_id,
                                "owner_actor_key": "driver",
                                "operator_actor_key": "driver",
                                "initial_pose": {
                                    "x_m": 8,
                                    "y_m": 24,
                                    "heading_degrees": 0,
                                },
                                "route": [
                                    {
                                        "x_m": 40,
                                        "y_m": 24,
                                        "heading_degrees": 0,
                                    }
                                ],
                                "state_overrides": {"speed_mps": 4},
                            }
                        ],
                        "capability_mounts": [
                            mount(
                                "pedestrian-perception",
                                "relative-motion-perception",
                                targets={"crossing": "interaction:crossing"},
                                inputs={
                                    "subject_motion": "state:actor:pedestrian:motion",
                                    "object_motion": "state:tool:car-one:motion",
                                },
                                outputs={
                                    "relative_motion": "channel:pedestrian-relative-motion"
                                },
                            ),
                            mount(
                                "vehicle-perception",
                                "relative-motion-perception",
                                targets={"crossing": "interaction:crossing"},
                                inputs={
                                    "subject_motion": "state:tool:car-one:motion",
                                    "object_motion": "state:actor:pedestrian:motion",
                                },
                                outputs={
                                    "relative_motion": "channel:vehicle-relative-motion"
                                },
                            ),
                            mount(
                                "pedestrian-behavior",
                                "pedestrian-crossing-behavior",
                                targets={"subject": "actor:pedestrian"},
                                inputs={
                                    "relative_motion": "channel:pedestrian-relative-motion"
                                },
                                outputs={"motion": "channel:pedestrian-motion"},
                            ),
                            mount(
                                "vehicle-behavior",
                                "vehicle-yield-behavior",
                                targets={
                                    "subject": "actor:driver",
                                    "vehicle": "tool:car-one",
                                },
                                inputs={
                                    "relative_motion": "channel:vehicle-relative-motion",
                                    "current_motion": "state:tool:car-one:motion",
                                    "route": "state:tool:car-one:route",
                                },
                                outputs={"motion": "channel:vehicle-motion"},
                            ),
                            mount(
                                "safety-observation",
                                "crossing-safety-observation",
                                targets={"crossing": "interaction:crossing"},
                                inputs={
                                    "motions": "state:interaction:crossing:motions"
                                },
                                outputs={
                                    "minimum_distance": "channel:minimum-distance"
                                },
                            ),
                        ],
                        "metrics": [
                            {
                                "metric_key": "minimum-distance",
                                "kind": "MINIMUM_DISTANCE",
                                "source_channel": "channel:minimum-distance",
                                "unit": "m",
                            },
                            {
                                "metric_key": "collision-count",
                                "kind": "COLLISION",
                                "unit": "count",
                            },
                        ],
                        "stop_conditions": [
                            {
                                "condition_key": "stop-on-collision",
                                "metric_key": "collision-count",
                                "operator": "GTE",
                                "threshold": 1,
                            }
                        ],
                    },
                }
            )
            document = contract.model_dump(mode="json", exclude_none=False)
            contract_hash = _digest(document)
            now = _now()
            if template is not None:
                current = session.get(
                    ScenarioTemplateRevision, template.current_published_revision_id
                )
                if current is not None and current.contract_hash == contract_hash:
                    return
                revision_no = int(
                    session.scalar(
                        select(func.max(ScenarioTemplateRevision.revision_no)).where(
                            ScenarioTemplateRevision.template_id == template.id
                        )
                    )
                    or 0
                ) + 1
            else:
                template = ScenarioTemplate(
                    id=str(uuid4()),
                    template_key="one-car-one-pedestrian",
                    name=contract.name,
                    description=contract.summary,
                    status="PUBLISHED",
                    is_builtin=True,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(template)
                session.flush()
                current = None
                revision_no = 1
            revision = ScenarioTemplateRevision(
                id=str(uuid4()),
                template_id=template.id,
                revision_no=revision_no,
                state="PUBLISHED",
                base_revision_id=current.id if current else None,
                schema_version=contract.schema_version,
                contract_json=document,
                contract_hash=contract_hash,
                validation_json={"valid": True, "errors": [], "warnings": []},
                lock_version=1,
                created_at=now,
                updated_at=now,
                published_at=now,
            )
            session.add(revision)
            session.flush()
            template.current_published_revision_id = revision.id
            template.name = contract.name
            template.description = contract.summary
            if current is not None:
                template.row_version += 1
                template.updated_at = now

    def list_templates(self) -> dict[str, Any]:
        with self.database.session_factory() as session:
            templates = list(
                session.scalars(
                    select(ScenarioTemplate).order_by(
                        ScenarioTemplate.is_builtin.desc(), ScenarioTemplate.updated_at.desc()
                    )
                )
            )
            return {
                "items": [self._detail(session, item) for item in templates],
                "total": len(templates),
            }

    def create_from_experiment(
        self,
        *,
        experiment_id: str,
        name: str,
        description: str = "",
        template_key: str | None = None,
    ) -> dict[str, Any]:
        normalized_name = name.strip()
        if not normalized_name:
            raise ServiceError(
                "INVALID_SCENARIO_TEMPLATE_NAME", "场景模板名称不能为空", status_code=422
            )
        with self.database.session_factory.begin() as session:
            experiment = session.get(Experiment, experiment_id)
            revision = session.get(
                ExperimentRevision,
                experiment.current_draft_revision_id if experiment else None,
            )
            if experiment is None:
                raise not_found("experiment", experiment_id)
            if revision is None or revision.state != "DRAFT":
                raise ServiceError(
                    "DRAFT_BASE_UNAVAILABLE", "当前实验没有可保存的草稿", status_code=409
                )
            extension_row = session.get(ExperimentRevisionCapability, revision.id)
            if extension_row is None:
                raise ServiceError(
                    "SCENARIO_ASSEMBLY_UNAVAILABLE",
                    "当前实验尚未配置能力场景",
                    status_code=422,
                )
            extension = ExperimentCapabilityExtension.model_validate(
                extension_row.extension_json
            )
            if extension.mode != "CAPABILITY_COMPOSED":
                raise ServiceError(
                    "SCENARIO_TEMPLATE_REQUIRES_COMPOSED_MODE",
                    "只有能力组合场景可以保存为模板",
                    status_code=422,
                )
            definition = ExperimentDefinition.model_validate(revision.definition_json)
            agent_names = {item.agent_key: item.name for item in definition.agents}
            blueprint = extension.model_dump(mode="json", exclude_none=False)
            slots = []
            for actor in blueprint["actors"]:
                source_agent_key = actor["experiment_agent_key"]
                actor["experiment_agent_key"] = actor["actor_key"]
                slots.append(
                    {
                        "slot_key": actor["actor_key"],
                        "name": agent_names.get(source_agent_key, actor["actor_key"]),
                        "role": actor["role"],
                        "description": f"源自实验角色 {source_agent_key}",
                    }
                )
            contract = ScenarioTemplateContract.model_validate(
                {
                    "name": normalized_name,
                    "summary": description.strip(),
                    "tags": [],
                    "actor_slots": slots,
                    "blueprint": blueprint,
                }
            )
            errors = self._validation_errors(session, contract)
            if errors:
                raise ServiceError(
                    "SCENARIO_TEMPLATE_VALIDATION_FAILED",
                    "当前场景尚未通过模板校验",
                    status_code=422,
                    details={"valid": False, "errors": errors, "warnings": []},
                )
            stable_key = template_key or (
                normalize_contract_key(normalized_name)[:48].rstrip("-")
                + f"-{uuid4().hex[:8]}"
            )
            if session.scalar(
                select(ScenarioTemplate.id).where(
                    ScenarioTemplate.template_key == stable_key
                )
            ):
                raise ServiceError(
                    "SCENARIO_TEMPLATE_KEY_CONFLICT",
                    "场景模板稳定键已存在",
                    status_code=409,
                )
            now = _now()
            template = ScenarioTemplate(
                id=str(uuid4()),
                template_key=stable_key,
                name=contract.name,
                description=contract.summary,
                status="DRAFT",
                is_builtin=False,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(template)
            session.flush()
            document = contract.model_dump(mode="json", exclude_none=False)
            draft = ScenarioTemplateRevision(
                id=str(uuid4()),
                template_id=template.id,
                revision_no=1,
                state="DRAFT",
                base_revision_id=None,
                schema_version=contract.schema_version,
                contract_json=document,
                contract_hash=_digest(document),
                validation_json=None,
                lock_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(draft)
            session.flush()
            template.current_draft_revision_id = draft.id
            return self._detail(session, template)

    def get_draft(self, template_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            template, draft = self._require_draft(session, template_id)
            contract = ScenarioTemplateContract.model_validate(draft.contract_json)
            return self._revision_detail(draft, contract)

    def update_draft(
        self,
        template_id: str,
        *,
        expected_lock_version: int,
        contract: ScenarioTemplateContract | dict[str, Any],
    ) -> dict[str, Any]:
        model = ScenarioTemplateContract.model_validate(contract)
        document = model.model_dump(mode="json", exclude_none=False)
        with self.database.session_factory.begin() as session:
            template, draft = self._require_draft(session, template_id)
            now = _now()
            result = session.execute(
                update(ScenarioTemplateRevision)
                .where(
                    ScenarioTemplateRevision.id == draft.id,
                    ScenarioTemplateRevision.lock_version == expected_lock_version,
                    ScenarioTemplateRevision.state == "DRAFT",
                )
                .values(
                    contract_json=document,
                    contract_hash=_digest(document),
                    validation_json=None,
                    lock_version=ScenarioTemplateRevision.lock_version + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise ServiceError(
                    "SCENARIO_TEMPLATE_REVISION_CONFLICT",
                    "场景模板草稿已被其他请求修改",
                    status_code=409,
                )
            template.name = model.name
            template.description = model.summary
            template.row_version += 1
            template.updated_at = now
            refreshed = session.get(ScenarioTemplateRevision, draft.id)
            return self._revision_detail(refreshed, model)

    def publish_draft(
        self, template_id: str, *, draft_revision_id: str, expected_lock_version: int
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            template, draft = self._require_draft(session, template_id)
            if draft.id != draft_revision_id or draft.lock_version != expected_lock_version:
                raise ServiceError(
                    "SCENARIO_TEMPLATE_REVISION_CONFLICT",
                    "场景模板草稿版本已变化",
                    status_code=409,
                )
            contract = ScenarioTemplateContract.model_validate(draft.contract_json)
            errors = self._validation_errors(session, contract)
            report = {"valid": not errors, "errors": errors, "warnings": []}
            if errors:
                draft.validation_json = report
                raise ServiceError(
                    "SCENARIO_TEMPLATE_VALIDATION_FAILED",
                    "场景模板没有通过发布校验",
                    status_code=422,
                    details=report,
                )
            now = _now()
            draft.state = "PUBLISHED"
            draft.validation_json = report
            draft.published_at = now
            draft.updated_at = now
            draft.lock_version += 1
            template.current_draft_revision_id = None
            template.current_published_revision_id = draft.id
            template.status = "PUBLISHED"
            template.row_version += 1
            template.updated_at = now
            return self._revision_detail(draft, contract)

    def fork_revision(self, template_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            template = session.get(ScenarioTemplate, template_id)
            source = session.get(ScenarioTemplateRevision, revision_id)
            if template is None:
                raise not_found("scenario_template", template_id)
            if template.is_builtin:
                raise ServiceError(
                    "BUILTIN_SCENARIO_TEMPLATE_IMMUTABLE",
                    "内置模板不可原地修改；请先保存为新的用户模板",
                    status_code=409,
                )
            if source is None or source.template_id != template.id or source.state != "PUBLISHED":
                raise not_found("scenario_template_revision", revision_id)
            if template.current_draft_revision_id:
                raise ServiceError(
                    "SCENARIO_TEMPLATE_DRAFT_EXISTS",
                    "该模板已经有编辑中的草稿",
                    status_code=409,
                )
            revision_no = int(
                session.scalar(
                    select(func.max(ScenarioTemplateRevision.revision_no)).where(
                        ScenarioTemplateRevision.template_id == template.id
                    )
                )
                or 0
            ) + 1
            now = _now()
            draft = ScenarioTemplateRevision(
                id=str(uuid4()),
                template_id=template.id,
                revision_no=revision_no,
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
            template.current_draft_revision_id = draft.id
            template.status = "DRAFT"
            template.row_version += 1
            template.updated_at = now
            contract = ScenarioTemplateContract.model_validate(draft.contract_json)
            return self._revision_detail(draft, contract)

    def list_revisions(self, template_id: str) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            template = session.get(ScenarioTemplate, template_id)
            if template is None:
                raise not_found("scenario_template", template_id)
            revisions = list(
                session.scalars(
                    select(ScenarioTemplateRevision)
                    .where(ScenarioTemplateRevision.template_id == template.id)
                    .order_by(ScenarioTemplateRevision.revision_no.desc())
                )
            )
            return [
                self._revision_detail(
                    revision,
                    ScenarioTemplateContract.model_validate(revision.contract_json),
                )
                for revision in revisions
            ]

    def get_template(self, template_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            template = session.get(ScenarioTemplate, template_id)
            if template is None:
                raise not_found("scenario_template", template_id)
            return self._detail(session, template)

    def get_revision(self, template_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            template = session.get(ScenarioTemplate, template_id)
            revision = session.get(ScenarioTemplateRevision, revision_id)
            if template is None:
                raise not_found("scenario_template", template_id)
            if revision is None or revision.template_id != template_id:
                raise not_found("scenario_template_revision", revision_id)
            contract = ScenarioTemplateContract.model_validate(revision.contract_json)
            return self._revision_detail(revision, contract)

    def instantiate(
        self,
        *,
        experiment_id: str,
        template_revision_id: str,
        actor_bindings: Mapping[str, str],
        clock_overrides: Mapping[str, int] | None = None,
        mount_parameter_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> ExperimentCapabilityExtension:
        with self.database.session_factory() as session:
            experiment = session.get(Experiment, experiment_id)
            revision = session.get(
                ExperimentRevision,
                experiment.current_draft_revision_id if experiment else None,
            )
            if experiment is None:
                raise not_found("experiment", experiment_id)
            if revision is None or revision.state != "DRAFT":
                raise ServiceError(
                    "DRAFT_BASE_UNAVAILABLE", "当前实验没有可应用模板的草稿", status_code=409
                )
            template_revision = session.get(
                ScenarioTemplateRevision, template_revision_id
            )
            if template_revision is None or template_revision.state != "PUBLISHED":
                raise not_found("scenario_template_revision", template_revision_id)
            contract = ScenarioTemplateContract.model_validate(
                template_revision.contract_json
            )
            expected_slots = {slot.slot_key for slot in contract.actor_slots}
            if set(actor_bindings) != expected_slots:
                raise ServiceError(
                    "SCENARIO_TEMPLATE_ACTOR_BINDINGS_INVALID",
                    "角色绑定必须完整覆盖模板角色槽位",
                    status_code=422,
                    details={"required": sorted(expected_slots)},
                )
            if len(set(actor_bindings.values())) != len(actor_bindings):
                raise ServiceError(
                    "SCENARIO_TEMPLATE_ACTORS_NOT_UNIQUE",
                    "同一个实验 Agent 不能同时占用多个物理角色",
                    status_code=422,
                )
            definition = ExperimentDefinition.model_validate(revision.definition_json)
            known_agents = {item.agent_key for item in definition.agents}
            unknown = sorted(set(actor_bindings.values()) - known_agents)
            if unknown:
                raise ServiceError(
                    "SCENARIO_TEMPLATE_AGENT_NOT_FOUND",
                    "角色绑定引用了实验中不存在的 Agent",
                    status_code=422,
                    details={"agent_keys": unknown},
                )
            document = contract.blueprint.model_dump(mode="json", exclude_none=False)
            for actor in document["actors"]:
                actor["experiment_agent_key"] = actor_bindings[actor["actor_key"]]
            for key, value in (clock_overrides or {}).items():
                if key not in {"base_tick_ms", "duration_ms", "snapshot_interval_ms"}:
                    raise ServiceError(
                        "SCENARIO_TEMPLATE_CLOCK_OVERRIDE_UNKNOWN",
                        f"不支持的时间参数：{key}",
                        status_code=422,
                    )
                document["clock"][key] = value
            overrides = mount_parameter_overrides or {}
            known_mounts = {item["mount_key"] for item in document["capability_mounts"]}
            unknown_mounts = sorted(set(overrides) - known_mounts)
            if unknown_mounts:
                raise ServiceError(
                    "SCENARIO_TEMPLATE_MOUNT_OVERRIDE_UNKNOWN",
                    "能力参数覆盖引用了不存在的挂载",
                    status_code=422,
                    details={"mount_keys": unknown_mounts},
                )
            for mount in document["capability_mounts"]:
                mount["parameters"].update(overrides.get(mount["mount_key"], {}))
            return ExperimentCapabilityExtension.model_validate(document)

    def apply_to_draft(
        self,
        *,
        experiment_id: str,
        template_revision_id: str,
        expected_lock_version: int,
        actor_bindings: Mapping[str, str],
        clock_overrides: Mapping[str, int] | None = None,
        mount_parameter_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        extension = self.instantiate(
            experiment_id=experiment_id,
            template_revision_id=template_revision_id,
            actor_bindings=actor_bindings,
            clock_overrides=clock_overrides,
            mount_parameter_overrides=mount_parameter_overrides,
        )
        from .scenarios import ScenarioAssemblyService

        return ScenarioAssemblyService(self.database).update_draft(
            experiment_id,
            expected_lock_version=expected_lock_version,
            extension=extension,
        )

    @staticmethod
    def _validation_errors(session, contract: ScenarioTemplateContract) -> list[dict[str, Any]]:
        from .scenarios import validate_experiment_capability_in_session

        return validate_experiment_capability_in_session(
            session, None, contract.blueprint
        )

    @staticmethod
    def _require_draft(session, template_id: str):
        template = session.get(ScenarioTemplate, template_id)
        if template is None:
            raise not_found("scenario_template", template_id)
        if not template.current_draft_revision_id:
            raise ServiceError(
                "SCENARIO_TEMPLATE_DRAFT_UNAVAILABLE",
                "场景模板没有可编辑草稿",
                status_code=409,
            )
        draft = session.get(
            ScenarioTemplateRevision, template.current_draft_revision_id
        )
        if draft is None or draft.state != "DRAFT":
            raise ServiceError(
                "SCENARIO_TEMPLATE_DRAFT_UNAVAILABLE",
                "场景模板草稿状态无效",
                status_code=409,
            )
        return template, draft

    def _detail(self, session, template: ScenarioTemplate) -> dict[str, Any]:
        revision = (
            session.get(ScenarioTemplateRevision, template.current_published_revision_id)
            if template.current_published_revision_id
            else None
        )
        draft = (
            session.get(ScenarioTemplateRevision, template.current_draft_revision_id)
            if template.current_draft_revision_id
            else None
        )
        contract = (
            ScenarioTemplateContract.model_validate(revision.contract_json)
            if revision
            else None
        )
        return {
            "id": template.id,
            "template_key": template.template_key,
            "name": template.name,
            "description": template.description,
            "status": template.status,
            "is_builtin": template.is_builtin,
            "row_version": template.row_version,
            "current_published": (
                self._revision_detail(revision, contract) if revision and contract else None
            ),
            "current_draft": (
                self._revision_detail(
                    draft,
                    ScenarioTemplateContract.model_validate(draft.contract_json),
                )
                if draft
                else None
            ),
        }

    @staticmethod
    def _revision_detail(
        revision: ScenarioTemplateRevision,
        contract: ScenarioTemplateContract,
    ) -> dict[str, Any]:
        return {
            "id": revision.id,
            "revision_no": revision.revision_no,
            "state": revision.state,
            "schema_version": revision.schema_version,
            "contract_hash": revision.contract_hash,
            "base_revision_id": revision.base_revision_id,
            "lock_version": revision.lock_version,
            "validation": revision.validation_json,
            "contract": contract.model_dump(mode="json", exclude_none=False),
            "readonly": revision.state == "PUBLISHED",
            "published_at": revision.published_at.isoformat()
            if revision.published_at
            else None,
        }


__all__ = ["ScenarioTemplateService"]
