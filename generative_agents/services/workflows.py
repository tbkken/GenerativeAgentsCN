"""Experiment-isolated Prompt workflow editing and immutable restore history."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from generative_agents.config import (
    DEFAULT_WORKFLOW_KEYS,
    ExperimentDefinition,
    WorkflowDefinition,
    definition_hash,
    make_default_workflows,
    workflow_hash,
)
from generative_agents.config.schema import REQUIRED_PROMPT_KEYS
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    Experiment,
    ExperimentRevision,
    ExperimentWorkflow,
    ExperimentWorkflowVersion,
)

from .errors import ServiceError, not_found


ALLOWED_SCRIPT_OPERATIONS = frozenset(
    {
        "schedule_prepare_context",
        "memory_prepare_context",
        "action_prepare_context",
        "social_prepare_context",
        "reflection_prepare_context",
        "identity",
        "merge_context",
        "select_fields",
        "normalize_list",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    """Serialize SQLite-naive UTC values as unambiguous UTC instants."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _prompt_keys(workflow: WorkflowDefinition) -> list[str]:
    return [
        node.prompt_key
        for node in workflow.nodes
        if node.kind == "llm" and node.prompt_key is not None
    ]


def workflow_validation_issues(
    workflows: dict[str, WorkflowDefinition],
    definition: ExperimentDefinition,
) -> list[dict[str, str]]:
    """Return deterministic publication errors without making drafts uneditable."""

    issues: list[dict[str, str]] = []
    missing_flows = sorted(set(DEFAULT_WORKFLOW_KEYS) - set(workflows))
    extra_flows = sorted(set(workflows) - set(DEFAULT_WORKFLOW_KEYS))
    if missing_flows:
        issues.append(
            {
                "code": "WORKFLOWS_MISSING",
                "path": "workflows",
                "message": "缺少必需流程: " + ", ".join(missing_flows),
                "severity": "ERROR",
            }
        )
    if extra_flows:
        issues.append(
            {
                "code": "WORKFLOWS_UNKNOWN",
                "path": "workflows",
                "message": "当前版本不支持新建流程: " + ", ".join(extra_flows),
                "severity": "ERROR",
            }
        )

    placements: list[str] = []
    for key, workflow in sorted(workflows.items()):
        if workflow.workflow_key != key:
            issues.append(
                {
                    "code": "WORKFLOW_KEY_MISMATCH",
                    "path": f"workflows.{key}.workflow_key",
                    "message": "流程 key 与存储位置不一致",
                    "severity": "ERROR",
                }
            )
        for node in workflow.nodes:
            if node.kind == "llm" and node.prompt_key:
                placements.append(node.prompt_key)
                if node.prompt_key not in definition.prompts:
                    issues.append(
                        {
                            "code": "WORKFLOW_PROMPT_NOT_FOUND",
                            "path": f"workflows.{key}.nodes.{node.node_id}.prompt_key",
                            "message": f"Prompt {node.prompt_key} 不存在于当前实验",
                            "severity": "ERROR",
                        }
                    )
            if node.kind == "script" and node.operation not in ALLOWED_SCRIPT_OPERATIONS:
                issues.append(
                    {
                        "code": "WORKFLOW_SCRIPT_NOT_REGISTERED",
                        "path": f"workflows.{key}.nodes.{node.node_id}.operation",
                        "message": f"Script 操作 {node.operation} 未注册，运行时不会执行任意源码",
                        "severity": "ERROR",
                    }
                )
            if node.kind == "subflow" and node.subflow_key == key:
                issues.append(
                    {
                        "code": "WORKFLOW_RECURSIVE_SUBFLOW",
                        "path": f"workflows.{key}.nodes.{node.node_id}.subflow_key",
                        "message": "流程不能直接引用自身",
                        "severity": "ERROR",
                    }
                )

    counts = Counter(placements)
    missing_prompts = sorted(REQUIRED_PROMPT_KEYS - counts.keys())
    duplicate_prompts = sorted(key for key, count in counts.items() if count > 1)
    if missing_prompts:
        issues.append(
            {
                "code": "WORKFLOW_PROMPTS_UNPLACED",
                "path": "workflows",
                "message": "以下 Prompt 尚未放入流程: " + ", ".join(missing_prompts),
                "severity": "ERROR",
            }
        )
    if duplicate_prompts:
        issues.append(
            {
                "code": "WORKFLOW_PROMPTS_DUPLICATED",
                "path": "workflows",
                "message": "以下 Prompt 被多个 LLM 节点重复引用: " + ", ".join(duplicate_prompts),
                "severity": "ERROR",
            }
        )
    return issues


class WorkflowService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def seed_revision_in_session(
        session: Session,
        *,
        experiment_id: str,
        revision: ExperimentRevision,
        definition: ExperimentDefinition,
        source_revision_id: str | None = None,
        create_default_versions: bool = False,
    ) -> None:
        """Materialize revision rows, cloning a source Revision when available."""

        existing = session.scalar(
            select(func.count())
            .select_from(ExperimentWorkflow)
            .where(ExperimentWorkflow.revision_id == revision.id)
        )
        if existing:
            return
        source_rows: list[ExperimentWorkflow] = []
        if source_revision_id:
            source_rows = list(
                session.scalars(
                    select(ExperimentWorkflow)
                    .where(ExperimentWorkflow.revision_id == source_revision_id)
                    .order_by(ExperimentWorkflow.workflow_key)
                ).all()
            )
        if source_rows:
            workflows = {
                row.workflow_key: WorkflowDefinition.model_validate(row.definition_json)
                for row in source_rows
            }
        else:
            workflows = make_default_workflows()

        now = _utc_now()
        for key in DEFAULT_WORKFLOW_KEYS:
            workflow = workflows[key]
            row = ExperimentWorkflow(
                id=str(uuid4()),
                experiment_id=experiment_id,
                revision_id=revision.id,
                workflow_key=key,
                definition_json=workflow.model_dump(mode="json", exclude_none=False),
                workflow_hash=workflow_hash(workflow),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            if create_default_versions:
                session.add(
                    ExperimentWorkflowVersion(
                        id=str(uuid4()),
                        experiment_id=experiment_id,
                        workflow_key=key,
                        version_no=1,
                        label="默认流程",
                        definition_json=row.definition_json,
                        prompt_contents_json=WorkflowService._prompt_snapshot(
                            definition, workflow
                        ),
                        workflow_hash=row.workflow_hash,
                        is_default=True,
                        source_revision_id=revision.id,
                        created_at=now,
                    )
                )
        session.flush()

    @staticmethod
    def _prompt_snapshot(
        definition: ExperimentDefinition, workflow: WorkflowDefinition
    ) -> dict[str, Any]:
        payload = definition.model_dump(mode="json", exclude_none=False)["prompts"]
        return {
            key: payload[key]
            for key in _prompt_keys(workflow)
            if key in payload
        }

    @staticmethod
    def _require_draft(
        session: Session, experiment_id: str
    ) -> tuple[Experiment, ExperimentRevision, ExperimentDefinition]:
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
                "DRAFT_BASE_UNAVAILABLE", "该实验当前没有可编辑草稿", status_code=409
            )
        definition = ExperimentDefinition.model_validate(revision.definition_json)
        return experiment, revision, definition

    @classmethod
    def ensure_revision_in_session(
        cls,
        session: Session,
        *,
        experiment_id: str,
        revision: ExperimentRevision,
        definition: ExperimentDefinition,
    ) -> None:
        cls.seed_revision_in_session(
            session,
            experiment_id=experiment_id,
            revision=revision,
            definition=definition,
            source_revision_id=revision.base_revision_id,
            create_default_versions=False,
        )
        version_count = session.scalar(
            select(func.count())
            .select_from(ExperimentWorkflowVersion)
            .where(ExperimentWorkflowVersion.experiment_id == experiment_id)
        )
        if not version_count:
            rows = list(
                session.scalars(
                    select(ExperimentWorkflow)
                    .where(ExperimentWorkflow.revision_id == revision.id)
                    .order_by(ExperimentWorkflow.workflow_key)
                ).all()
            )
            now = _utc_now()
            for row in rows:
                workflow = WorkflowDefinition.model_validate(row.definition_json)
                session.add(
                    ExperimentWorkflowVersion(
                        id=str(uuid4()),
                        experiment_id=experiment_id,
                        workflow_key=row.workflow_key,
                        version_no=1,
                        label="默认流程",
                        definition_json=row.definition_json,
                        prompt_contents_json=cls._prompt_snapshot(definition, workflow),
                        workflow_hash=row.workflow_hash,
                        is_default=True,
                        source_revision_id=revision.id,
                        created_at=now,
                    )
                )
            session.flush()

    def list_workflows(self, experiment_id: str) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            _experiment, revision, definition = self._require_draft(
                session, experiment_id
            )
            self.ensure_revision_in_session(
                session,
                experiment_id=experiment_id,
                revision=revision,
                definition=definition,
            )
            rows = list(
                session.scalars(
                    select(ExperimentWorkflow)
                    .where(ExperimentWorkflow.revision_id == revision.id)
                    .order_by(ExperimentWorkflow.workflow_key)
                ).all()
            )
            version_counts = dict(
                session.execute(
                    select(
                        ExperimentWorkflowVersion.workflow_key,
                        func.count(ExperimentWorkflowVersion.id),
                    )
                    .where(ExperimentWorkflowVersion.experiment_id == experiment_id)
                    .group_by(ExperimentWorkflowVersion.workflow_key)
                ).all()
            )
            by_key = {row.workflow_key: row for row in rows}
            return {
                "experiment_id": experiment_id,
                "revision_id": revision.id,
                "lock_version": revision.lock_version,
                "items": [
                    self._summary(by_key[key], version_counts.get(key, 0))
                    for key in DEFAULT_WORKFLOW_KEYS
                ],
            }

    def get_workflow(self, experiment_id: str, workflow_key: str) -> dict[str, Any]:
        self._validate_key(workflow_key)
        with self.database.session_factory.begin() as session:
            _experiment, revision, definition = self._require_draft(
                session, experiment_id
            )
            self.ensure_revision_in_session(
                session,
                experiment_id=experiment_id,
                revision=revision,
                definition=definition,
            )
            row = session.scalar(
                select(ExperimentWorkflow).where(
                    ExperimentWorkflow.revision_id == revision.id,
                    ExperimentWorkflow.workflow_key == workflow_key,
                )
            )
            if row is None:
                raise not_found("workflow", workflow_key)
            workflow = WorkflowDefinition.model_validate(row.definition_json)
            versions = self._versions(session, experiment_id, workflow_key)
            return self._detail(row, workflow, definition, revision, versions)

    def save_workflow(
        self,
        experiment_id: str,
        workflow_key: str,
        *,
        expected_lock_version: int,
        workflow: WorkflowDefinition,
        prompt_contents: dict[str, str],
        label: str | None = None,
    ) -> dict[str, Any]:
        self._validate_key(workflow_key)
        if workflow.workflow_key != workflow_key:
            raise ServiceError(
                "WORKFLOW_KEY_MISMATCH", "流程 key 与 URL 不一致", status_code=422
            )
        for node in workflow.nodes:
            if node.kind == "script" and node.operation not in ALLOWED_SCRIPT_OPERATIONS:
                raise ServiceError(
                    "WORKFLOW_SCRIPT_NOT_REGISTERED",
                    f"Script 操作 {node.operation} 未注册",
                    status_code=422,
                )
        with self.database.session_factory.begin() as session:
            experiment, revision, definition = self._require_draft(
                session, experiment_id
            )
            if revision.lock_version != expected_lock_version:
                self._raise_conflict(expected_lock_version, revision.lock_version)
            self.ensure_revision_in_session(
                session,
                experiment_id=experiment_id,
                revision=revision,
                definition=definition,
            )
            referenced = set(_prompt_keys(workflow))
            unknown_updates = set(prompt_contents) - referenced
            if unknown_updates:
                raise ServiceError(
                    "WORKFLOW_PROMPT_NOT_OWNED",
                    "只能更新当前流程中 LLM 节点引用的 Prompt",
                    status_code=422,
                    details={"prompt_keys": sorted(unknown_updates)},
                )
            missing_prompts = referenced - definition.prompts.keys()
            missing_prompt_bodies = missing_prompts - prompt_contents.keys()
            if missing_prompt_bodies:
                raise ServiceError(
                    "WORKFLOW_PROMPT_NOT_FOUND",
                    "流程引用了不存在的 Prompt",
                    status_code=422,
                    details={"prompt_keys": sorted(missing_prompt_bodies)},
                )

            payload = definition.model_dump(mode="json", exclude_none=False)
            for key, content in prompt_contents.items():
                payload["prompts"][key] = {"content": content, "sha256": None}
            updated_definition = ExperimentDefinition.model_validate(payload)
            row = session.scalar(
                select(ExperimentWorkflow).where(
                    ExperimentWorkflow.revision_id == revision.id,
                    ExperimentWorkflow.workflow_key == workflow_key,
                )
            )
            if row is None:
                raise not_found("workflow", workflow_key)
            now = _utc_now()
            row.definition_json = workflow.model_dump(mode="json", exclude_none=False)
            row.workflow_hash = workflow_hash(workflow)
            row.updated_at = now
            revision.definition_json = updated_definition.model_dump(
                mode="json", exclude_none=False
            )
            revision.definition_hash = definition_hash(updated_definition)
            revision.validation_json = None
            revision.validated_hash = None
            revision.lock_version += 1
            revision.updated_at = now
            experiment.updated_at = now

            next_version = (
                session.scalar(
                    select(func.max(ExperimentWorkflowVersion.version_no)).where(
                        ExperimentWorkflowVersion.experiment_id == experiment_id,
                        ExperimentWorkflowVersion.workflow_key == workflow_key,
                    )
                )
                or 0
            ) + 1
            version = ExperimentWorkflowVersion(
                id=str(uuid4()),
                experiment_id=experiment_id,
                workflow_key=workflow_key,
                version_no=next_version,
                label=(label or f"版本 {next_version}").strip()[:120],
                definition_json=row.definition_json,
                prompt_contents_json=self._prompt_snapshot(updated_definition, workflow),
                workflow_hash=row.workflow_hash,
                is_default=False,
                source_revision_id=revision.id,
                created_at=now,
            )
            session.add(version)
            session.flush()
            return self._detail(
                row,
                workflow,
                updated_definition,
                revision,
                self._versions(session, experiment_id, workflow_key),
            )

    def restore_version(
        self,
        experiment_id: str,
        workflow_key: str,
        version_id: str,
        *,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        self._validate_key(workflow_key)
        with self.database.session_factory.begin() as session:
            experiment, revision, definition = self._require_draft(
                session, experiment_id
            )
            if revision.lock_version != expected_lock_version:
                self._raise_conflict(expected_lock_version, revision.lock_version)
            self.ensure_revision_in_session(
                session,
                experiment_id=experiment_id,
                revision=revision,
                definition=definition,
            )
            version = session.get(ExperimentWorkflowVersion, version_id)
            if (
                version is None
                or version.experiment_id != experiment_id
                or version.workflow_key != workflow_key
            ):
                raise not_found("workflow_version", version_id)
            workflow = WorkflowDefinition.model_validate(version.definition_json)
            row = session.scalar(
                select(ExperimentWorkflow).where(
                    ExperimentWorkflow.revision_id == revision.id,
                    ExperimentWorkflow.workflow_key == workflow_key,
                )
            )
            if row is None:
                raise not_found("workflow", workflow_key)
            current_workflow = WorkflowDefinition.model_validate(row.definition_json)
            payload = definition.model_dump(mode="json", exclude_none=False)
            target_prompt_keys = set(version.prompt_contents_json)
            other_prompt_keys: set[str] = set()
            for other in self.load_revision_bundle_in_session(session, revision.id).values():
                if other.workflow_key != workflow_key:
                    other_prompt_keys.update(_prompt_keys(other))
            for key in set(_prompt_keys(current_workflow)) - target_prompt_keys:
                if key not in other_prompt_keys:
                    payload["prompts"].pop(key, None)
            for key, prompt in version.prompt_contents_json.items():
                payload["prompts"][key] = prompt
            restored_definition = ExperimentDefinition.model_validate(payload)
            now = _utc_now()
            row.definition_json = workflow.model_dump(mode="json", exclude_none=False)
            row.workflow_hash = workflow_hash(workflow)
            row.updated_at = now
            revision.definition_json = restored_definition.model_dump(
                mode="json", exclude_none=False
            )
            revision.definition_hash = definition_hash(restored_definition)
            revision.validation_json = None
            revision.validated_hash = None
            revision.lock_version += 1
            revision.updated_at = now
            experiment.updated_at = now
            next_version = (
                session.scalar(
                    select(func.max(ExperimentWorkflowVersion.version_no)).where(
                        ExperimentWorkflowVersion.experiment_id == experiment_id,
                        ExperimentWorkflowVersion.workflow_key == workflow_key,
                    )
                )
                or 0
            ) + 1
            restored_version = ExperimentWorkflowVersion(
                id=str(uuid4()),
                experiment_id=experiment_id,
                workflow_key=workflow_key,
                version_no=next_version,
                label=(
                    "恢复默认流程"
                    if version.is_default
                    else f"恢复自版本 {version.version_no}"
                ),
                definition_json=row.definition_json,
                prompt_contents_json=self._prompt_snapshot(
                    restored_definition, workflow
                ),
                workflow_hash=row.workflow_hash,
                is_default=False,
                source_revision_id=revision.id,
                created_at=now,
            )
            session.add(restored_version)
            session.flush()
            detail = self._detail(
                row,
                workflow,
                restored_definition,
                revision,
                self._versions(session, experiment_id, workflow_key),
                restored_from=version.id,
            )
            detail["restored_as_version_id"] = restored_version.id
            detail["restored_as_version_no"] = restored_version.version_no
            return detail

    def validate_workflow(self, experiment_id: str, workflow_key: str) -> dict[str, Any]:
        self._validate_key(workflow_key)
        with self.database.session_factory.begin() as session:
            _experiment, revision, definition = self._require_draft(
                session, experiment_id
            )
            self.ensure_revision_in_session(
                session,
                experiment_id=experiment_id,
                revision=revision,
                definition=definition,
            )
            workflows = self.load_revision_bundle_in_session(session, revision.id)
            issues = workflow_validation_issues(workflows, definition)
            scoped = [
                issue
                for issue in issues
                if issue["path"] == "workflows"
                or issue["path"].startswith(f"workflows.{workflow_key}")
            ]
            return {
                "workflow_key": workflow_key,
                "valid": not scoped,
                "errors": scoped,
            }

    @staticmethod
    def load_revision_bundle_in_session(
        session: Session, revision_id: str
    ) -> dict[str, WorkflowDefinition]:
        rows = list(
            session.scalars(
                select(ExperimentWorkflow)
                .where(ExperimentWorkflow.revision_id == revision_id)
                .order_by(ExperimentWorkflow.workflow_key)
            ).all()
        )
        return {
            row.workflow_key: WorkflowDefinition.model_validate(row.definition_json)
            for row in rows
        }

    @staticmethod
    def _versions(
        session: Session, experiment_id: str, workflow_key: str
    ) -> list[ExperimentWorkflowVersion]:
        return list(
            session.scalars(
                select(ExperimentWorkflowVersion)
                .where(
                    ExperimentWorkflowVersion.experiment_id == experiment_id,
                    ExperimentWorkflowVersion.workflow_key == workflow_key,
                )
                .order_by(ExperimentWorkflowVersion.version_no.desc())
            ).all()
        )

    @staticmethod
    def _summary(row: ExperimentWorkflow, version_count: int) -> dict[str, Any]:
        workflow = WorkflowDefinition.model_validate(row.definition_json)
        return {
            "workflow_key": row.workflow_key,
            "title": workflow.title,
            "description": workflow.description,
            "workflow_hash": row.workflow_hash,
            "node_count": len(workflow.nodes),
            "llm_node_count": sum(node.kind == "llm" for node in workflow.nodes),
            "version_count": version_count,
            "updated_at": _iso_utc(row.updated_at),
        }

    @staticmethod
    def _version_detail(version: ExperimentWorkflowVersion) -> dict[str, Any]:
        return {
            "id": version.id,
            "version_no": version.version_no,
            "label": version.label,
            "workflow_hash": version.workflow_hash,
            "is_default": version.is_default,
            "created_at": _iso_utc(version.created_at),
        }

    @classmethod
    def _detail(
        cls,
        row: ExperimentWorkflow,
        workflow: WorkflowDefinition,
        definition: ExperimentDefinition,
        revision: ExperimentRevision,
        versions: list[ExperimentWorkflowVersion],
        *,
        restored_from: str | None = None,
    ) -> dict[str, Any]:
        return {
            "experiment_id": row.experiment_id,
            "revision_id": revision.id,
            "lock_version": revision.lock_version,
            "workflow": workflow.model_dump(mode="json", exclude_none=False),
            "workflow_hash": row.workflow_hash,
            "prompts": {
                key: definition.prompts[key].model_dump(mode="json", exclude_none=False)
                for key in _prompt_keys(workflow)
                if key in definition.prompts
            },
            "versions": [cls._version_detail(version) for version in versions],
            "restored_from_version_id": restored_from,
        }

    @staticmethod
    def _validate_key(workflow_key: str) -> None:
        if workflow_key not in DEFAULT_WORKFLOW_KEYS:
            raise not_found("workflow", workflow_key)

    @staticmethod
    def _raise_conflict(expected: int, actual: int) -> None:
        raise ServiceError(
            "REVISION_CONFLICT",
            "草稿已被其他请求修改，请重新载入",
            status_code=409,
            details={"expected_lock_version": expected, "actual_lock_version": actual},
        )
