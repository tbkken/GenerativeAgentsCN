"""Reusable Agent-orchestration brains and immutable template revisions."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from math import ceil
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from generative_agents.config import (
    DEFAULT_WORKFLOW_KEYS,
    ExperimentDefinition,
    WorkflowDefinition,
    definition_hash,
    ensure_llm_context_inputs,
    make_builtin_definition,
    make_default_workflows,
    workflow_hash,
)
from generative_agents.config.brain_capabilities import BrainCapabilityExtension
from generative_agents.config.capabilities import CapabilityBundleContract
from generative_agents.config.hashing import canonical_json_bytes
from generative_agents.config.prompt_variables import canonicalize_prompt_payload
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    BrainRevision,
    BrainRevisionExtension,
    BrainTemplate,
    BrainWorkflow,
    CapabilityBundleRevision,
    Experiment,
    ExperimentRevision,
    ExperimentWorkflow,
    WorkflowFunctionRecord,
)
from generative_agents.runtime.json_schema import validate_json_schema
from generative_agents.runtime.workflow_functions import (
    get_workflow_function,
    validate_inline_workflow_function,
)

from .errors import ServiceError, not_found
from .workflows import (
    _invalid_prompt_variables,
    execute_workflow_trial,
    workflow_execution_issues,
    workflow_function_sources_in_session,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _make_key(name: str) -> str:
    ascii_key = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return f"{(ascii_key[:48].strip('-') or 'brain')}-{uuid4().hex[:8]}"


def _bundle_digest(
    workflows: dict[str, WorkflowDefinition], prompts: dict[str, Any]
) -> str:
    payload = {
        "workflows": {
            key: workflow.model_dump(mode="json", exclude_none=False)
            for key, workflow in sorted(workflows.items())
        },
        "prompts": prompts,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _extension_digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _prompt_keys(workflow: WorkflowDefinition) -> set[str]:
    return {
        node.prompt_key
        for node in workflow.nodes
        if node.kind == "llm" and node.prompt_key
    }


class BrainService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_builtin_brain(self) -> dict[str, Any]:
        """Register the code-owned Stanford Town orchestration as an immutable base."""

        with self.database.session_factory.begin() as session:
            existing = session.scalar(
                select(BrainTemplate).where(BrainTemplate.brain_key == "stanford-town")
            )
            if existing is not None:
                return self._brain_detail(session, existing)
            now = _utc_now()
            definition = make_builtin_definition(
                key="stanford-town-brain", name="斯坦福小镇"
            )
            workflows = make_default_workflows()
            prompt_keys = set().union(*(_prompt_keys(item) for item in workflows.values()))
            prompts = {
                key: definition.prompts[key].model_dump(mode="json", exclude_none=False)
                for key in sorted(prompt_keys)
                if key in definition.prompts
            }
            brain = BrainTemplate(
                id=str(uuid4()),
                brain_key="stanford-town",
                name="斯坦福小镇",
                description="系统内置的 Generative Agents 基准大脑，包含日程、记忆、行动、社交与反思流程。",
                status="PUBLISHED",
                is_builtin=True,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(brain)
            session.flush()
            revision = BrainRevision(
                id=str(uuid4()),
                brain_id=brain.id,
                revision_no=1,
                state="PUBLISHED",
                schema_version=1,
                prompts_json=prompts,
                bundle_hash=_bundle_digest(workflows, prompts),
                validation_json={"valid": True, "errors": [], "warnings": []},
                lock_version=1,
                created_at=now,
                updated_at=now,
                published_at=now,
            )
            session.add(revision)
            session.flush()
            self._write_workflows(session, brain.id, revision.id, workflows, now)
            brain.current_published_revision_id = revision.id
            return self._brain_detail(session, brain)

    def default_revision_id(self) -> str:
        with self.database.session_factory() as session:
            brain = session.scalar(
                select(BrainTemplate).where(BrainTemplate.brain_key == "stanford-town")
            )
            if brain is None or not brain.current_published_revision_id:
                raise ServiceError(
                    "DEFAULT_BRAIN_UNAVAILABLE",
                    "斯坦福小镇基准大脑尚未初始化",
                    status_code=503,
                )
            return brain.current_published_revision_id

    def create_from_experiment(
        self,
        experiment_id: str,
        *,
        revision_id: str | None,
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Save one experiment-owned orchestration snapshot as a new brain Draft."""

        name = name.strip()
        if not name:
            raise ServiceError("INVALID_BRAIN_NAME", "大脑名称不能为空", status_code=422)
        with self.database.session_factory.begin() as session:
            experiment = session.get(Experiment, experiment_id)
            if experiment is None:
                raise not_found("experiment", experiment_id)
            selected_id = (
                revision_id
                or experiment.current_draft_revision_id
                or experiment.current_published_revision_id
            )
            source = session.get(ExperimentRevision, selected_id) if selected_id else None
            if source is None or source.experiment_id != experiment_id:
                raise not_found("revision", selected_id or "")
            rows = list(session.scalars(select(ExperimentWorkflow).where(
                ExperimentWorkflow.revision_id == source.id
            ).order_by(ExperimentWorkflow.workflow_key)))
            workflows = {
                row.workflow_key: WorkflowDefinition.model_validate(row.definition_json)
                for row in rows
            }
            self._require_complete(workflows, source.id)
            definition = ExperimentDefinition.model_validate(source.definition_json)
            prompt_keys = set().union(*(_prompt_keys(item) for item in workflows.values()))
            prompt_payload = definition.model_dump(mode="json", exclude_none=False)["prompts"]
            prompts = {
                key: prompt_payload[key]
                for key in sorted(prompt_keys)
                if key in prompt_payload
            }
            stable_key = _make_key(name)
            now = _utc_now()
            brain = BrainTemplate(
                id=str(uuid4()), brain_key=stable_key, name=name,
                description=description.strip()[:10_000], status="DRAFT",
                is_builtin=False, row_version=1, created_at=now, updated_at=now,
            )
            session.add(brain)
            session.flush()
            revision = BrainRevision(
                id=str(uuid4()), brain_id=brain.id, revision_no=1, state="DRAFT",
                base_revision_id=None, schema_version=1, prompts_json=prompts,
                bundle_hash=_bundle_digest(workflows, prompts), validation_json=None,
                lock_version=1, created_at=now, updated_at=now,
            )
            session.add(revision)
            session.flush()
            self._write_workflows(session, brain.id, revision.id, workflows, now)
            brain.current_draft_revision_id = revision.id
            return self._brain_detail(session, brain)

    def create_brain(
        self,
        *,
        name: str,
        description: str = "",
        source_revision_id: str | None = None,
        brain_key: str | None = None,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ServiceError("INVALID_BRAIN_NAME", "大脑名称不能为空", status_code=422)
        stable_key = brain_key.strip() if brain_key else _make_key(name)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", stable_key):
            raise ServiceError(
                "INVALID_BRAIN_KEY", "大脑稳定键必须由小写字母、数字和连字符组成", status_code=422
            )
        with self.database.session_factory.begin() as session:
            if session.scalar(select(BrainTemplate.id).where(BrainTemplate.brain_key == stable_key)):
                raise ServiceError("BRAIN_KEY_CONFLICT", "大脑稳定键已被使用", status_code=409)
            source: BrainRevision | None = None
            if source_revision_id:
                source = session.get(BrainRevision, source_revision_id)
                if source is None or source.state != "PUBLISHED":
                    raise not_found("brain_revision", source_revision_id)
                workflows = self._load_bundle(session, source.id)
                prompts = copy.deepcopy(source.prompts_json)
            else:
                workflows = make_default_workflows()
                builtin = make_builtin_definition(key=stable_key, name=name)
                prompt_keys = set().union(*(_prompt_keys(item) for item in workflows.values()))
                prompts = {
                    key: builtin.prompts[key].model_dump(mode="json", exclude_none=False)
                    for key in sorted(prompt_keys)
                    if key in builtin.prompts
                }
            now = _utc_now()
            brain = BrainTemplate(
                id=str(uuid4()), brain_key=stable_key, name=name,
                description=description.strip()[:10_000], status="DRAFT",
                is_builtin=False, row_version=1, created_at=now, updated_at=now,
            )
            session.add(brain)
            session.flush()
            revision = BrainRevision(
                id=str(uuid4()), brain_id=brain.id, revision_no=1, state="DRAFT",
                base_revision_id=source.id if source else None, schema_version=1,
                prompts_json=prompts, bundle_hash=_bundle_digest(workflows, prompts),
                validation_json=None, lock_version=1, created_at=now, updated_at=now,
            )
            session.add(revision)
            session.flush()
            self._write_workflows(session, brain.id, revision.id, workflows, now)
            if source is not None:
                self._copy_capability_extension(
                    session,
                    source_revision_id=source.id,
                    target_revision_id=revision.id,
                    brain_id=brain.id,
                    now=now,
                )
            brain.current_draft_revision_id = revision.id
            return self._brain_detail(session, brain)

    def list_brains(
        self, *, query: str | None = None, status: str | None = None,
        page: int = 1, page_size: int = 5,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ServiceError("INVALID_PAGINATION", "大脑分页参数无效", status_code=422)
        normalized_status = status.upper() if status else None
        if normalized_status not in {None, "DRAFT", "PUBLISHED"}:
            raise ServiceError("INVALID_BRAIN_STATUS", "大脑状态筛选无效", status_code=422)
        with self.database.session_factory() as session:
            statement = select(BrainTemplate)
            count = select(func.count()).select_from(BrainTemplate)
            status_count = select(BrainTemplate.status, func.count()).group_by(BrainTemplate.status)
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                predicate = or_(BrainTemplate.name.ilike(pattern), BrainTemplate.brain_key.ilike(pattern))
                statement, count, status_count = statement.where(predicate), count.where(predicate), status_count.where(predicate)
            counts = {"DRAFT": 0, "PUBLISHED": 0}
            for item_status, item_count in session.execute(status_count):
                counts[item_status] = int(item_count)
            counts["ALL"] = sum(counts.values())
            if normalized_status:
                statement = statement.where(BrainTemplate.status == normalized_status)
                count = count.where(BrainTemplate.status == normalized_status)
            total = int(session.scalar(count) or 0)
            rows = list(session.scalars(
                statement.order_by(BrainTemplate.is_builtin.desc(), BrainTemplate.updated_at.desc(), BrainTemplate.id.desc())
                .offset((page - 1) * page_size).limit(page_size)
            ))
            return {
                "items": [self._brain_detail(session, item) for item in rows],
                "page": page, "page_size": page_size, "total": total,
                "total_pages": max(1, ceil(total / page_size)), "status_counts": counts,
            }

    def get_brain(self, brain_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            brain = session.get(BrainTemplate, brain_id)
            if brain is None:
                raise not_found("brain", brain_id)
            return self._brain_detail(session, brain)

    def get_draft(self, brain_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            brain, revision = self._require_draft(session, brain_id)
            return self._revision_detail(session, brain, revision)

    def get_revision(self, brain_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            brain, revision = self._require_revision(session, brain_id, revision_id)
            return self._revision_detail(session, brain, revision)

    @staticmethod
    def default_capability_extension() -> BrainCapabilityExtension:
        return BrainCapabilityExtension()

    def get_capability_extension(
        self, brain_id: str, revision_id: str
    ) -> dict[str, Any]:
        with self.database.session_factory() as session:
            brain, revision = self._require_revision(session, brain_id, revision_id)
            row = session.get(BrainRevisionExtension, revision.id)
            extension = BrainCapabilityExtension.model_validate(
                row.extension_json if row else self.default_capability_extension()
            )
            return self._capability_extension_detail(brain, revision, extension, row)

    def get_draft_capability_extension(self, brain_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            brain, revision = self._require_draft(session, brain_id)
            row = session.get(BrainRevisionExtension, revision.id)
            extension = BrainCapabilityExtension.model_validate(
                row.extension_json if row else self.default_capability_extension()
            )
            return self._capability_extension_detail(brain, revision, extension, row)

    def update_draft_capability_extension(
        self,
        brain_id: str,
        *,
        expected_lock_version: int,
        extension: BrainCapabilityExtension | dict[str, Any],
    ) -> dict[str, Any]:
        model = BrainCapabilityExtension.model_validate(extension)
        document = model.model_dump(mode="json", exclude_none=False)
        now = _utc_now()
        with self.database.session_factory.begin() as session:
            brain, revision = self._require_draft(session, brain_id)
            result = session.execute(
                update(BrainRevision)
                .where(
                    BrainRevision.id == revision.id,
                    BrainRevision.lock_version == expected_lock_version,
                    BrainRevision.state == "DRAFT",
                )
                .values(
                    lock_version=BrainRevision.lock_version + 1,
                    validation_json=None,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise ServiceError(
                    "BRAIN_REVISION_CONFLICT",
                    "大脑草稿已被其他请求修改，请重新载入",
                    status_code=409,
                )
            row = session.get(BrainRevisionExtension, revision.id)
            if row is None:
                row = BrainRevisionExtension(
                    revision_id=revision.id,
                    brain_id=brain.id,
                    schema_version=model.schema_version,
                    extension_json=document,
                    extension_hash=_extension_digest(document),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.schema_version = model.schema_version
                row.extension_json = document
                row.extension_hash = _extension_digest(document)
                row.updated_at = now
            brain.updated_at = now
            session.flush()
            refreshed = session.get(BrainRevision, revision.id)
            return self._capability_extension_detail(brain, refreshed, model, row)

    def list_workflows(self, brain_id: str, revision_id: str | None = None) -> dict[str, Any]:
        with self.database.session_factory() as session:
            if revision_id:
                brain, revision = self._require_revision(session, brain_id, revision_id)
            else:
                brain, revision = self._require_draft(session, brain_id)
            rows = self._workflow_rows(session, revision.id)
            by_key = {row.workflow_key: row for row in rows}
            self._require_complete(by_key, revision.id)
            return {
                "brain_id": brain.id, "revision_id": revision.id,
                "revision_no": revision.revision_no, "lock_version": revision.lock_version,
                "readonly": revision.state != "DRAFT",
                "items": [self._summary(by_key[key]) for key in DEFAULT_WORKFLOW_KEYS],
            }

    def get_workflow(
        self, brain_id: str, workflow_key: str, revision_id: str | None = None
    ) -> dict[str, Any]:
        self._validate_key(workflow_key)
        with self.database.session_factory() as session:
            if revision_id:
                brain, revision = self._require_revision(session, brain_id, revision_id)
            else:
                brain, revision = self._require_draft(session, brain_id)
            row = session.scalar(select(BrainWorkflow).where(
                BrainWorkflow.revision_id == revision.id,
                BrainWorkflow.workflow_key == workflow_key,
            ))
            if row is None:
                raise not_found("brain_workflow", workflow_key)
            return self._workflow_detail(brain, revision, row)

    def save_workflow(
        self, brain_id: str, workflow_key: str, *, expected_lock_version: int,
        workflow: WorkflowDefinition, prompt_contents: dict[str, str],
    ) -> dict[str, Any]:
        self._validate_key(workflow_key)
        workflow = ensure_llm_context_inputs(workflow)
        if workflow.workflow_key != workflow_key:
            raise ServiceError("WORKFLOW_KEY_MISMATCH", "流程 key 与 URL 不一致", status_code=422)
        with self.database.session_factory.begin() as session:
            brain, revision = self._require_draft(session, brain_id)
            if revision.lock_version != expected_lock_version:
                raise ServiceError(
                    "BRAIN_REVISION_CONFLICT", "大脑草稿已被其他请求修改，请重新载入",
                    status_code=409,
                    details={"expected_lock_version": expected_lock_version, "actual_lock_version": revision.lock_version},
                )
            custom_keys = set(
                session.scalars(select(WorkflowFunctionRecord.function_key)).all()
            )
            for node in workflow.nodes:
                if node.kind not in {"code", "script"}:
                    continue
                if (
                    node.script_mode == "shared"
                    and get_workflow_function(node.operation or "") is None
                    and node.operation not in custom_keys
                ):
                    raise ServiceError(
                        "WORKFLOW_SCRIPT_NOT_REGISTERED",
                        f"公共 Function {node.operation} 未注册",
                        status_code=422,
                    )
                if node.script_mode == "inline":
                    try:
                        validate_inline_workflow_function(node.script_source or "")
                    except ValueError as exc:
                        raise ServiceError(
                            "WORKFLOW_INLINE_SCRIPT_INVALID", str(exc), status_code=422
                        ) from exc
            referenced = _prompt_keys(workflow)
            if set(prompt_contents) - referenced:
                raise ServiceError("WORKFLOW_PROMPT_NOT_OWNED", "只能更新当前流程引用的 Prompt", status_code=422)
            prompts = canonicalize_prompt_payload(copy.deepcopy(revision.prompts_json))
            for node in workflow.nodes:
                if node.kind != "llm" or not node.prompt_key:
                    continue
                content = prompt_contents.get(node.prompt_key, prompts.get(node.prompt_key, {}).get("content", ""))
                invalid = _invalid_prompt_variables(node, content)
                if invalid:
                    raise ServiceError(
                        "WORKFLOW_PROMPT_VARIABLE_AMBIGUOUS", "Prompt 变量必须以当前节点输入变量开头",
                        status_code=422, details={"prompt_key": node.prompt_key, "invalid_variables": invalid},
                    )
                prompts[node.prompt_key] = canonicalize_prompt_payload({node.prompt_key: {"content": content}})[node.prompt_key]
            row = session.scalar(select(BrainWorkflow).where(
                BrainWorkflow.revision_id == revision.id,
                BrainWorkflow.workflow_key == workflow_key,
            ))
            if row is None:
                raise not_found("brain_workflow", workflow_key)
            now = _utc_now()
            row.definition_json = workflow.model_dump(mode="json", exclude_none=False)
            row.workflow_hash = workflow_hash(workflow)
            row.updated_at = now
            revision.prompts_json = prompts
            revision.lock_version += 1
            revision.validation_json = None
            revision.updated_at = now
            brain.updated_at = now
            workflows = self._load_bundle(session, revision.id)
            workflows[workflow_key] = workflow
            revision.bundle_hash = _bundle_digest(workflows, prompts)
            session.flush()
            return self._workflow_detail(brain, revision, row)

    def migrate_to_prompt_router(
        self,
        brain_id: str,
        workflow_key: str,
        *,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        """Replace a legacy brain graph with the executable router default."""

        return self.save_workflow(
            brain_id,
            workflow_key,
            expected_lock_version=expected_lock_version,
            workflow=make_default_workflows()[workflow_key],
            prompt_contents={},
        )

    def test_run_workflow(
        self,
        brain_id: str,
        workflow_key: str,
        *,
        workflow: WorkflowDefinition,
        inputs: dict[str, Any],
        llm_outputs: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_key(workflow_key)
        workflow = ensure_llm_context_inputs(workflow)
        if workflow.workflow_key != workflow_key:
            raise ServiceError(
                "WORKFLOW_KEY_MISMATCH", "流程 key 与 URL 不一致", status_code=422
            )
        with self.database.session_factory() as session:
            _brain, revision = self._require_draft(session, brain_id)
            workflows = self._load_bundle(session, revision.id)
            workflows[workflow_key] = workflow
            operation_keys = {
                node.operation
                for item in workflows.values()
                for node in item.nodes
                if node.kind in {"code", "script"}
                and node.script_mode == "shared"
                and get_workflow_function(node.operation or "") is None
                and node.operation
            }
            custom_sources = {
                row.function_key: row.source
                for row in session.scalars(
                    select(WorkflowFunctionRecord).where(
                        WorkflowFunctionRecord.function_key.in_(operation_keys)
                    )
                ).all()
            }
            missing = operation_keys - custom_sources.keys()
            if missing:
                raise ServiceError(
                    "WORKFLOW_SCRIPT_NOT_REGISTERED",
                    "流程引用了不存在的公共 Function",
                    status_code=422,
                    details={"operations": sorted(missing)},
                )
        return execute_workflow_trial(
            workflows,
            workflow_key,
            inputs=inputs,
            llm_outputs=llm_outputs,
            function_sources=custom_sources,
        )

    def publish_draft(
        self, brain_id: str, *, draft_revision_id: str, expected_lock_version: int
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            brain, revision = self._require_draft(session, brain_id)
            if revision.id != draft_revision_id or revision.lock_version != expected_lock_version:
                raise ServiceError("BRAIN_REVISION_CONFLICT", "大脑草稿版本已变化，请重新载入", status_code=409)
            workflows = self._load_bundle(session, revision.id)
            self._require_complete(workflows, revision.id)
            errors = self._validate_bundle(session, workflows, revision.prompts_json)
            errors.extend(self._validate_capability_extension(session, revision))
            if errors:
                revision.validation_json = {"valid": False, "errors": errors, "warnings": []}
                raise ServiceError("BRAIN_VALIDATION_FAILED", "大脑未通过发布校验", status_code=422, details=revision.validation_json)
            now = _utc_now()
            revision.bundle_hash = _bundle_digest(workflows, revision.prompts_json)
            revision.validation_json = {"valid": True, "errors": [], "warnings": []}
            revision.state = "PUBLISHED"
            revision.published_at = now
            revision.updated_at = now
            brain.current_draft_revision_id = None
            brain.current_published_revision_id = revision.id
            brain.status = "PUBLISHED"
            brain.row_version += 1
            brain.updated_at = now
            session.flush()
            return self._revision_detail(session, brain, revision)

    def fork_revision(self, brain_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            brain, source = self._require_revision(session, brain_id, revision_id)
            if source.state != "PUBLISHED":
                raise not_found("brain_revision", revision_id)
            if brain.is_builtin:
                raise ServiceError(
                    "BUILTIN_BRAIN_IMMUTABLE", "系统基准大脑不能直接修改，请基于它新建一个大脑", status_code=409
                )
            if brain.current_draft_revision_id:
                raise ServiceError("BRAIN_DRAFT_EXISTS", "该大脑已有编辑中的草稿", status_code=409)
            number = int(session.scalar(select(func.max(BrainRevision.revision_no)).where(BrainRevision.brain_id == brain_id)) or 0) + 1
            now = _utc_now()
            draft = BrainRevision(
                id=str(uuid4()), brain_id=brain.id, revision_no=number, state="DRAFT",
                base_revision_id=source.id, schema_version=source.schema_version,
                prompts_json=copy.deepcopy(source.prompts_json), bundle_hash=source.bundle_hash,
                validation_json=None, lock_version=1, created_at=now, updated_at=now,
            )
            session.add(draft)
            session.flush()
            self._write_workflows(session, brain.id, draft.id, self._load_bundle(session, source.id), now)
            self._copy_capability_extension(
                session,
                source_revision_id=source.id,
                target_revision_id=draft.id,
                brain_id=brain.id,
                now=now,
            )
            brain.current_draft_revision_id = draft.id
            brain.status = "DRAFT"
            brain.row_version += 1
            brain.updated_at = now
            return self._revision_detail(session, brain, draft)

    def list_revisions(self, brain_id: str) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            brain = session.get(BrainTemplate, brain_id)
            if brain is None:
                raise not_found("brain", brain_id)
            revisions = list(session.scalars(select(BrainRevision).where(
                BrainRevision.brain_id == brain_id
            ).order_by(BrainRevision.revision_no.desc())))
            return [self._revision_summary(item) for item in revisions]

    def apply_to_experiment(
        self, experiment_id: str, *, expected_lock_version: int, brain_revision_id: str
    ) -> dict[str, Any]:
        """Replace a Draft's complete workflow and Prompt bundle from one brain Revision."""

        with self.database.session_factory.begin() as session:
            experiment = session.get(Experiment, experiment_id)
            draft = session.get(ExperimentRevision, experiment.current_draft_revision_id) if experiment and experiment.current_draft_revision_id else None
            if experiment is None:
                raise not_found("experiment", experiment_id)
            if draft is None or draft.state != "DRAFT":
                raise ServiceError("DRAFT_BASE_UNAVAILABLE", "该实验当前没有可编辑草稿", status_code=409)
            source = session.get(BrainRevision, brain_revision_id)
            if source is None or source.state != "PUBLISHED":
                raise not_found("brain_revision", brain_revision_id)
            if draft.lock_version != expected_lock_version:
                raise ServiceError("REVISION_CONFLICT", "实验草稿已被其他请求修改，请重新载入", status_code=409)
            workflows = self._load_bundle(session, source.id)
            self._require_complete(workflows, source.id)
            definition = ExperimentDefinition.model_validate(draft.definition_json)
            payload = definition.model_dump(mode="json", exclude_none=False)
            old_rows = list(session.scalars(select(ExperimentWorkflow).where(
                ExperimentWorkflow.revision_id == draft.id
            ).order_by(ExperimentWorkflow.workflow_key)))
            old_keys = set().union(*(_prompt_keys(WorkflowDefinition.model_validate(row.definition_json)) for row in old_rows)) if old_rows else set()
            for key in old_keys:
                payload["prompts"].pop(key, None)
            payload["prompts"].update(copy.deepcopy(source.prompts_json))
            updated_definition = ExperimentDefinition.model_validate(payload)
            now = _utc_now()
            for row in old_rows:
                session.delete(row)
            session.flush()
            self._write_experiment_workflows(session, experiment.id, draft.id, workflows, now)
            draft.definition_json = updated_definition.model_dump(mode="json", exclude_none=False)
            draft.definition_hash = definition_hash(updated_definition)
            draft.validation_json = None
            draft.validated_hash = None
            draft.snapshot_complete = False
            draft.lock_version += 1
            draft.updated_at = now
            draft.provenance_json = {
                **(draft.provenance_json or {}),
                "brain_id": source.brain_id,
                "brain_revision_id": source.id,
                "brain_bundle_hash": source.bundle_hash,
            }
            experiment.updated_at = now
            session.flush()
            from .experiments import ExperimentService
            return ExperimentService._revision_detail(draft)

    @staticmethod
    def _validate_bundle(
        session: Session,
        workflows: dict[str, WorkflowDefinition],
        prompts: dict[str, Any],
    ) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        custom_keys = set(
            session.scalars(select(WorkflowFunctionRecord.function_key)).all()
        )
        for key in DEFAULT_WORKFLOW_KEYS:
            workflow = workflows.get(key)
            if workflow is None:
                errors.append({"code": "WORKFLOW_MISSING", "path": f"workflows.{key}", "message": f"缺少 {key} 流程"})
                continue
            if workflow.execution_mode != "prompt_router":
                errors.append(
                    {
                        "code": "WORKFLOW_EXECUTION_MODE_NOT_RUNNABLE",
                        "path": f"workflows.{key}.execution_mode",
                        "message": "旧版 Prompt 后置钩子不能发布；请迁移为真实 Prompt 路由图",
                    }
                )
            for node in workflow.nodes:
                if node.kind in {"code", "script"}:
                    if (
                        node.script_mode == "shared"
                        and get_workflow_function(node.operation or "") is None
                        and node.operation not in custom_keys
                    ):
                        errors.append(
                            {
                                "code": "WORKFLOW_SCRIPT_NOT_REGISTERED",
                                "path": f"workflows.{key}.{node.node_id}",
                                "message": f"公共 Function {node.operation} 未注册",
                            }
                        )
                    if node.script_mode == "inline":
                        try:
                            validate_inline_workflow_function(node.script_source or "")
                        except ValueError as exc:
                            errors.append(
                                {
                                    "code": "WORKFLOW_INLINE_SCRIPT_INVALID",
                                    "path": f"workflows.{key}.{node.node_id}",
                                    "message": str(exc),
                                }
                            )
                if node.kind != "llm" or not node.prompt_key:
                    continue
                prompt = prompts.get(node.prompt_key)
                if not prompt:
                    errors.append({"code": "PROMPT_MISSING", "path": f"workflows.{key}.{node.node_id}", "message": f"缺少 Prompt {node.prompt_key}"})
                    continue
                invalid = _invalid_prompt_variables(node, prompt.get("content", ""))
                if invalid:
                    errors.append({"code": "PROMPT_VARIABLE_INVALID", "path": f"workflows.{key}.{node.node_id}", "message": "Prompt 变量未在节点输入中声明: " + ", ".join(invalid)})
        if not errors:
            errors.extend(
                workflow_execution_issues(
                    workflows,
                    function_sources=workflow_function_sources_in_session(
                        session, workflows
                    ),
                )
            )
        return errors

    @staticmethod
    def _write_workflows(session: Session, brain_id: str, revision_id: str, workflows: dict[str, WorkflowDefinition], now: datetime) -> None:
        for key in DEFAULT_WORKFLOW_KEYS:
            workflow = ensure_llm_context_inputs(workflows[key])
            session.add(BrainWorkflow(
                id=str(uuid4()), brain_id=brain_id, revision_id=revision_id,
                workflow_key=key, definition_json=workflow.model_dump(mode="json", exclude_none=False),
                workflow_hash=workflow_hash(workflow), created_at=now, updated_at=now,
            ))
        session.flush()

    @staticmethod
    def _write_experiment_workflows(session: Session, experiment_id: str, revision_id: str, workflows: dict[str, WorkflowDefinition], now: datetime) -> None:
        for key in DEFAULT_WORKFLOW_KEYS:
            workflow = ensure_llm_context_inputs(workflows[key])
            session.add(ExperimentWorkflow(
                id=str(uuid4()), experiment_id=experiment_id, revision_id=revision_id,
                workflow_key=key, definition_json=workflow.model_dump(mode="json", exclude_none=False),
                workflow_hash=workflow_hash(workflow), created_at=now, updated_at=now,
            ))
        session.flush()

    @staticmethod
    def _workflow_rows(session: Session, revision_id: str) -> list[BrainWorkflow]:
        return list(session.scalars(select(BrainWorkflow).where(
            BrainWorkflow.revision_id == revision_id
        ).order_by(BrainWorkflow.workflow_key)))

    @classmethod
    def _load_bundle(cls, session: Session, revision_id: str) -> dict[str, WorkflowDefinition]:
        return {
            row.workflow_key: WorkflowDefinition.model_validate(row.definition_json)
            for row in cls._workflow_rows(session, revision_id)
        }

    @staticmethod
    def _require_complete(bundle: dict[str, Any], revision_id: str) -> None:
        missing = [key for key in DEFAULT_WORKFLOW_KEYS if key not in bundle]
        if missing:
            raise ServiceError("WORKFLOWS_MISSING", "大脑流程快照不完整", status_code=409, details={"revision_id": revision_id, "workflow_keys": missing})

    @staticmethod
    def _validate_key(workflow_key: str) -> None:
        if workflow_key not in DEFAULT_WORKFLOW_KEYS:
            raise not_found("brain_workflow", workflow_key)

    @staticmethod
    def _require_draft(session: Session, brain_id: str) -> tuple[BrainTemplate, BrainRevision]:
        brain = session.get(BrainTemplate, brain_id)
        if brain is None:
            raise not_found("brain", brain_id)
        revision = session.get(BrainRevision, brain.current_draft_revision_id) if brain.current_draft_revision_id else None
        if revision is None or revision.state != "DRAFT":
            raise ServiceError("BRAIN_DRAFT_UNAVAILABLE", "该大脑当前没有可编辑草稿", status_code=409)
        return brain, revision

    @staticmethod
    def _require_revision(session: Session, brain_id: str, revision_id: str) -> tuple[BrainTemplate, BrainRevision]:
        brain = session.get(BrainTemplate, brain_id)
        revision = session.get(BrainRevision, revision_id)
        if brain is None:
            raise not_found("brain", brain_id)
        if revision is None or revision.brain_id != brain_id:
            raise not_found("brain_revision", revision_id)
        return brain, revision

    @staticmethod
    def _summary(row: BrainWorkflow) -> dict[str, Any]:
        workflow = WorkflowDefinition.model_validate(row.definition_json)
        return {
            "workflow_key": row.workflow_key, "title": workflow.title,
            "description": workflow.description, "workflow_hash": row.workflow_hash,
            "node_count": len(workflow.nodes),
            "llm_node_count": sum(node.kind == "llm" for node in workflow.nodes),
            "version_count": 1, "updated_at": _iso(row.updated_at),
        }

    def _workflow_detail(self, brain: BrainTemplate, revision: BrainRevision, row: BrainWorkflow) -> dict[str, Any]:
        workflow = WorkflowDefinition.model_validate(row.definition_json)
        return {
            "brain_id": brain.id, "revision_id": revision.id,
            "revision_no": revision.revision_no, "lock_version": revision.lock_version,
            "readonly": revision.state != "DRAFT",
            "workflow": workflow.model_dump(mode="json", exclude_none=False),
            "workflow_hash": row.workflow_hash,
            "prompts": {
                key: revision.prompts_json[key]
                for key in sorted(_prompt_keys(workflow)) if key in revision.prompts_json
            },
            "versions": [], "restored_from_version_id": None,
        }

    @staticmethod
    def _revision_summary(revision: BrainRevision | None) -> dict[str, Any] | None:
        if revision is None:
            return None
        return {
            "id": revision.id, "revision_no": revision.revision_no,
            "state": revision.state, "bundle_hash": revision.bundle_hash,
            "lock_version": revision.lock_version, "updated_at": _iso(revision.updated_at),
            "published_at": _iso(revision.published_at),
        }

    def _revision_detail(self, session: Session, brain: BrainTemplate, revision: BrainRevision) -> dict[str, Any]:
        result = self._revision_summary(revision) or {}
        result.update({
            "brain_id": brain.id, "brain_key": brain.brain_key, "brain_name": brain.name,
            "base_revision_id": revision.base_revision_id,
            "schema_version": revision.schema_version, "validation": revision.validation_json,
            "prompt_count": len(revision.prompts_json),
            "workflow_count": len(self._workflow_rows(session, revision.id)),
        })
        return result

    @staticmethod
    def _validate_capability_extension(
        session: Session, revision: BrainRevision
    ) -> list[dict[str, Any]]:
        row = session.get(BrainRevisionExtension, revision.id)
        if row is None:
            return []
        extension = BrainCapabilityExtension.model_validate(row.extension_json)
        errors: list[dict[str, Any]] = []
        for index, mount in enumerate(extension.mounts):
            bundle_revision = session.get(
                CapabilityBundleRevision, mount.capability_bundle_revision_id
            )
            if bundle_revision is None or bundle_revision.state != "PUBLISHED":
                errors.append(
                    {
                        "code": "BRAIN_CAPABILITY_BUNDLE_UNAVAILABLE",
                        "path": f"mounts.{index}.capability_bundle_revision_id",
                        "message": "大脑能力必须引用已发布能力包版本",
                    }
                )
                continue
            bundle = CapabilityBundleContract.model_validate(
                bundle_revision.composition_json
            )
            if not ({"BRAIN", "AGENT"} & set(bundle.targets)):
                errors.append(
                    {
                        "code": "BRAIN_CAPABILITY_TARGET_MISMATCH",
                        "path": f"mounts.{index}.capability_bundle_revision_id",
                        "message": "该能力包不能挂载到大脑或 Agent",
                    }
                )
            try:
                validate_json_schema(
                    mount.parameters,
                    bundle.exposed_parameters_schema,
                    f"$.mounts[{index}].parameters",
                )
            except ValueError as exc:
                errors.append(
                    {
                        "code": "BRAIN_CAPABILITY_PARAMETERS_INVALID",
                        "path": f"mounts.{index}.parameters",
                        "message": str(exc),
                    }
                )
        return errors

    @staticmethod
    def _copy_capability_extension(
        session: Session,
        *,
        source_revision_id: str,
        target_revision_id: str,
        brain_id: str,
        now: datetime,
    ) -> None:
        source = session.get(BrainRevisionExtension, source_revision_id)
        if source is None:
            return
        session.add(
            BrainRevisionExtension(
                revision_id=target_revision_id,
                brain_id=brain_id,
                schema_version=source.schema_version,
                extension_json=copy.deepcopy(source.extension_json),
                extension_hash=source.extension_hash,
                created_at=now,
                updated_at=now,
            )
        )

    @staticmethod
    def _capability_extension_detail(
        brain: BrainTemplate,
        revision: BrainRevision,
        extension: BrainCapabilityExtension,
        row: BrainRevisionExtension | None,
    ) -> dict[str, Any]:
        document = extension.model_dump(mode="json", exclude_none=False)
        return {
            "brain_id": brain.id,
            "revision_id": revision.id,
            "revision_state": revision.state,
            "lock_version": revision.lock_version,
            "schema_version": extension.schema_version,
            "extension": document,
            "extension_hash": row.extension_hash if row else _extension_digest(document),
            "readonly": revision.state == "PUBLISHED",
            "is_default": row is None,
        }

    def _usage_experiment_ids(self, session: Session, brain_id: str) -> set[str]:
        result: set[str] = set()
        for experiment_id, provenance in session.execute(select(ExperimentRevision.experiment_id, ExperimentRevision.provenance_json)):
            if (provenance or {}).get("brain_id") == brain_id:
                result.add(experiment_id)
        return result

    def _brain_detail(self, session: Session, brain: BrainTemplate) -> dict[str, Any]:
        draft = session.get(BrainRevision, brain.current_draft_revision_id) if brain.current_draft_revision_id else None
        published = session.get(BrainRevision, brain.current_published_revision_id) if brain.current_published_revision_id else None
        source = draft or published
        node_count = 0
        if source:
            node_count = sum(len(item.nodes) for item in self._load_bundle(session, source.id).values())
        return {
            "id": brain.id, "brain_key": brain.brain_key, "name": brain.name,
            "description": brain.description, "status": brain.status,
            "is_builtin": brain.is_builtin, "row_version": brain.row_version,
            "current_draft": self._revision_summary(draft),
            "current_published": self._revision_summary(published),
            "workflow_count": len(DEFAULT_WORKFLOW_KEYS), "node_count": node_count,
            "usage_count": len(self._usage_experiment_ids(session, brain.id)),
            "updated_at": _iso(brain.updated_at), "created_at": _iso(brain.created_at),
        }
