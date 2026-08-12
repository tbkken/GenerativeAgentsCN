"""Experiment-isolated Prompt workflow editing and immutable restore history."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from generative_agents.config import (
    DEFAULT_WORKFLOW_KEYS,
    ExperimentDefinition,
    WorkflowDefinition,
    definition_hash,
    ensure_llm_context_inputs,
    make_default_workflows,
    workflow_hash,
)
from generative_agents.config.schema import (
    CustomWorkflowFunctionDefinition,
    REQUIRED_PROMPT_KEYS,
)
from generative_agents.config.prompt_variables import canonicalize_prompt_payload
from generative_agents.persistence import Database
from generative_agents.persistence.models import (
    Experiment,
    ExperimentRevision,
    ExperimentWorkflow,
    ExperimentWorkflowVersion,
    WorkflowFunctionRecord,
)
from generative_agents.runtime.workflow_functions import (
    WORKFLOW_FUNCTIONS,
    list_workflow_functions,
    validate_inline_workflow_function,
)
from generative_agents.runtime.workflow_engine import WorkflowExecutionError, WorkflowExecutor

from .errors import ServiceError, not_found


ALLOWED_SCRIPT_OPERATIONS = frozenset(WORKFLOW_FUNCTIONS)
PROMPT_VARIABLE_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}")
LEGACY_PROMPT_VARIABLE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}")


def _schema_example(schema: Any) -> Any:
    """Build a small deterministic fixture from one JSON Schema fragment."""

    if not isinstance(schema, dict):
        return None
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "null")
    if schema_type == "object":
        properties = schema.get("properties")
        return {
            key: _schema_example(value)
            for key, value in (properties.items() if isinstance(properties, dict) else ())
        }
    if schema_type == "array":
        count = max(1, int(schema.get("minItems", 0) or 0))
        return [_schema_example(schema.get("items", {})) for _ in range(count)]
    if schema_type == "string":
        return "trial"
    if schema_type == "integer":
        return int(schema.get("minimum", 0))
    if schema_type == "number":
        return float(schema.get("minimum", 0))
    if schema_type == "boolean":
        return False
    return None


def execute_workflow_trial(
    workflows: dict[str, WorkflowDefinition],
    workflow_key: str,
    *,
    inputs: dict[str, Any],
    llm_outputs: dict[str, Any],
    function_sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a deterministic whole-graph trial shared by experiment and brain UI."""

    traces: list[dict[str, Any]] = []

    def llm_fixture(node, _node_inputs, _runtime_context):
        if node.node_id in llm_outputs:
            return llm_outputs[node.node_id]
        if node.prompt_key and node.prompt_key in llm_outputs:
            return llm_outputs[node.prompt_key]
        schema = node.config.get("response_schema", {}).get("properties", {}).get("res", {})
        return _schema_example(schema)

    try:
        result = WorkflowExecutor(
            workflows,
            function_sources=function_sources or {},
            trace_handler=traces.append,
        ).execute(
            workflow_key,
            inputs,
            llm_handler=llm_fixture,
            runtime_context={"trigger": "trial_run", "agent_key": "trial-agent"},
            invocation_id="trial-run",
        )
    except WorkflowExecutionError as exc:
        raise ServiceError(
            "WORKFLOW_TEST_RUN_FAILED",
            str(exc),
            status_code=422,
            details={"trace": traces},
        ) from exc
    return {
        "workflow_key": workflow_key,
        "status": "SUCCEEDED",
        "output": result.value,
        "state": dict(result.state),
        "executed_nodes": list(result.executed_nodes),
        "trace": traces,
    }


def default_workflow_trial_inputs(
    workflow_key: str, prompt_key: str | None = None
) -> dict[str, Any]:
    """Return the deterministic Start fixture used by publication smoke tests."""

    return {
        "step_context": {
            "trigger": "new_day" if workflow_key == "schedule" else "step",
            "agent": {"key": "publication-trial", "name": "Publication Trial"},
            "clock": "2000-01-01T00:00:00Z",
            "memories": [],
            "visible_events": [],
            "prompt_key": prompt_key,
            "prompt_request": {
                "prompt": "publication trial",
                "failsafe": None,
                "retry": 1,
            },
        }
    }


def workflow_function_sources_in_session(
    session: Session,
    workflows: dict[str, WorkflowDefinition],
) -> dict[str, str]:
    """Snapshot the custom Function sources referenced by a workflow bundle."""

    operation_keys = {
        node.operation
        for workflow in workflows.values()
        for node in workflow.nodes
        if node.kind in {"code", "script"}
        and node.script_mode == "shared"
        and node.operation not in ALLOWED_SCRIPT_OPERATIONS
        and node.operation
    }
    if not operation_keys:
        return {}
    return {
        row.function_key: row.source
        for row in session.scalars(
            select(WorkflowFunctionRecord).where(
                WorkflowFunctionRecord.function_key.in_(operation_keys)
            )
        ).all()
    }


def workflow_execution_issues(
    workflows: dict[str, WorkflowDefinition],
    *,
    function_sources: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Prove every published graph reaches End through the production executor."""

    issues: list[dict[str, str]] = []
    for workflow_key in DEFAULT_WORKFLOW_KEYS:
        workflow = workflows.get(workflow_key)
        if workflow is None:
            continue
        prompt_keys = [
            node.prompt_key
            for node in workflow.nodes
            if node.kind == "llm" and node.prompt_key
        ]
        trial_routes = prompt_keys if workflow.execution_mode == "prompt_router" else [None]
        for prompt_key in trial_routes:
            try:
                execute_workflow_trial(
                    workflows,
                    workflow_key,
                    inputs=default_workflow_trial_inputs(workflow_key, prompt_key),
                    llm_outputs={},
                    function_sources=function_sources or {},
                )
            except ServiceError as exc:
                path = f"workflows.{workflow_key}"
                if prompt_key:
                    path += f".prompt_{prompt_key}"
                issues.append(
                    {
                        "code": "WORKFLOW_EXECUTION_FAILED",
                        "path": path,
                        "message": f"发布试运行未到达 End：{exc.message}",
                        "severity": "ERROR",
                    }
                )
    return issues


def _invalid_prompt_variables(node: Any, content: str) -> list[str]:
    roots = {item.name for item in node.inputs}
    invalid = {
            token
            for token in PROMPT_VARIABLE_PATTERN.findall(content or "")
            if token.split(".", 1)[0] not in roots
        }
    invalid.update(f"${{{token}}}" for token in LEGACY_PROMPT_VARIABLE_PATTERN.findall(content or ""))
    return sorted(invalid)


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
    *,
    allowed_script_operations: frozenset[str] | None = None,
) -> list[dict[str, str]]:
    """Return deterministic publication errors without making drafts uneditable."""

    issues: list[dict[str, str]] = []
    available_operations = allowed_script_operations or ALLOWED_SCRIPT_OPERATIONS
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
        if workflow.execution_mode != "prompt_router":
            issues.append(
                {
                    "code": "WORKFLOW_EXECUTION_MODE_NOT_RUNNABLE",
                    "path": f"workflows.{key}.execution_mode",
                    "message": "该流程仍使用旧版 Prompt 后置钩子，必须迁移为真实 Prompt 路由图后才能发布",
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
                else:
                    invalid_variables = _invalid_prompt_variables(
                        node, definition.prompts[node.prompt_key].content
                    )
                    if invalid_variables:
                        issues.append(
                            {
                                "code": "WORKFLOW_PROMPT_VARIABLE_AMBIGUOUS",
                                "path": f"workflows.{key}.nodes.{node.node_id}.prompt",
                                "message": "Prompt 变量必须以当前节点输入变量开头: "
                                + ", ".join(invalid_variables),
                                "severity": "ERROR",
                            }
                        )
            if node.kind in {"code", "script"}:
                if node.script_mode == "shared" and node.operation not in available_operations:
                    issues.append(
                        {
                            "code": "WORKFLOW_SCRIPT_NOT_REGISTERED",
                            "path": f"workflows.{key}.nodes.{node.node_id}.operation",
                            "message": f"公共 Function {node.operation} 未注册",
                            "severity": "ERROR",
                        }
                    )
                if node.script_mode == "inline":
                    try:
                        validate_inline_workflow_function(node.script_source or "")
                    except ValueError as exc:
                        issues.append(
                            {
                                "code": "WORKFLOW_INLINE_SCRIPT_INVALID",
                                "path": f"workflows.{key}.nodes.{node.node_id}.script_source",
                                "message": str(exc),
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
        workflow_bundle: dict[str, WorkflowDefinition] | None = None,
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
        if workflow_bundle is not None:
            workflows = {
                key: ensure_llm_context_inputs(value)
                for key, value in workflow_bundle.items()
            }
        elif source_rows:
            workflows = {
                row.workflow_key: ensure_llm_context_inputs(
                    WorkflowDefinition.model_validate(row.definition_json)
                )
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

    @staticmethod
    def _require_revision(
        session: Session, experiment_id: str, revision_id: str
    ) -> tuple[Experiment, ExperimentRevision, ExperimentDefinition]:
        experiment = session.get(Experiment, experiment_id)
        if experiment is None:
            raise not_found("experiment", experiment_id)
        revision = session.get(ExperimentRevision, revision_id)
        if revision is None or revision.experiment_id != experiment_id:
            raise not_found("revision", revision_id)
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
                "revision_no": revision.revision_no,
                "lock_version": revision.lock_version,
                "readonly": False,
                "items": [
                    self._summary(by_key[key], version_counts.get(key, 0))
                    for key in DEFAULT_WORKFLOW_KEYS
                ],
            }

    def list_revision_workflows(
        self, experiment_id: str, revision_id: str
    ) -> dict[str, Any]:
        """Return an immutable Revision's graph list for inspection."""

        with self.database.session_factory() as session:
            _experiment, revision, _definition = self._require_revision(
                session, experiment_id, revision_id
            )
            rows = list(
                session.scalars(
                    select(ExperimentWorkflow)
                    .where(ExperimentWorkflow.revision_id == revision.id)
                    .order_by(ExperimentWorkflow.workflow_key)
                ).all()
            )
            by_key = {row.workflow_key: row for row in rows}
            missing = [key for key in DEFAULT_WORKFLOW_KEYS if key not in by_key]
            if missing:
                raise ServiceError(
                    "WORKFLOWS_MISSING",
                    "该 Revision 的 Agent 编排快照不完整",
                    status_code=409,
                    details={"revision_id": revision.id, "workflow_keys": missing},
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
            return {
                "experiment_id": experiment_id,
                "revision_id": revision.id,
                "revision_no": revision.revision_no,
                "lock_version": revision.lock_version,
                "readonly": True,
                "items": [
                    self._summary(by_key[key], version_counts.get(key, 0))
                    for key in DEFAULT_WORKFLOW_KEYS
                ],
            }

    @staticmethod
    def _custom_function_item(row: WorkflowFunctionRecord) -> dict[str, Any]:
        return {
            "key": row.function_key,
            "function_key": row.function_key,
            "title": row.title,
            "description": row.description,
            "input_type": row.input_type,
            "output_type": row.output_type,
            "source": row.source,
            "implementation": f"database:{row.function_key}@{row.row_version}",
            "available": True,
            "scope": "custom",
            "editable": True,
            "row_version": row.row_version,
            "updated_at": _iso_utc(row.updated_at),
        }

    @classmethod
    def _function_catalog(cls, session: Session) -> list[dict[str, Any]]:
        custom_rows = list(
            session.scalars(
                select(WorkflowFunctionRecord).order_by(WorkflowFunctionRecord.function_key)
            ).all()
        )
        return [*(cls._custom_function_item(row) for row in custom_rows), *list_workflow_functions()]

    @staticmethod
    def allowed_script_operations_in_session(session: Session) -> frozenset[str]:
        custom = frozenset(session.scalars(select(WorkflowFunctionRecord.function_key)).all())
        return ALLOWED_SCRIPT_OPERATIONS | custom

    def list_functions(self) -> dict[str, Any]:
        with self.database.session_factory() as session:
            return {"items": self._function_catalog(session)}

    def save_custom_function(
        self,
        function_key: str,
        *,
        expected_row_version: int | None,
        function: CustomWorkflowFunctionDefinition,
    ) -> dict[str, Any]:
        if function.function_key != function_key:
            raise ServiceError(
                "WORKFLOW_FUNCTION_KEY_MISMATCH",
                "Function key 与 URL 不一致",
                status_code=422,
            )
        if function_key in ALLOWED_SCRIPT_OPERATIONS:
            raise ServiceError(
                "WORKFLOW_FUNCTION_KEY_RESERVED",
                "系统 Function key 不能被覆盖",
                status_code=422,
            )
        try:
            validate_inline_workflow_function(function.source)
        except ValueError as exc:
            raise ServiceError(
                "WORKFLOW_CUSTOM_FUNCTION_INVALID", str(exc), status_code=422
            ) from exc
        with self.database.session_factory.begin() as session:
            row = session.scalar(
                select(WorkflowFunctionRecord).where(
                    WorkflowFunctionRecord.function_key == function_key
                )
            )
            now = _utc_now()
            if row is None:
                if expected_row_version not in (None, 0):
                    raise ServiceError(
                        "WORKFLOW_FUNCTION_CONFLICT",
                        "Function 已被其他请求修改，请重新载入",
                        status_code=409,
                    )
                row = WorkflowFunctionRecord(
                    id=str(uuid4()),
                    function_key=function_key,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                if expected_row_version != row.row_version:
                    raise ServiceError(
                        "WORKFLOW_FUNCTION_CONFLICT",
                        "Function 已被其他请求修改，请重新载入",
                        status_code=409,
                        details={"actual_row_version": row.row_version},
                    )
                row.row_version += 1
                row.updated_at = now
            row.title = function.title
            row.description = function.description
            row.input_type = function.input_type
            row.output_type = function.output_type
            row.source = function.source
            session.flush()
            return {"item": self._custom_function_item(row), "items": self._function_catalog(session)}

    def delete_custom_function(
        self,
        function_key: str,
        *,
        expected_row_version: int,
    ) -> dict[str, Any]:
        with self.database.session_factory.begin() as session:
            row = session.scalar(
                select(WorkflowFunctionRecord).where(
                    WorkflowFunctionRecord.function_key == function_key
                )
            )
            if row is None:
                raise not_found("workflow_function", function_key)
            if row.row_version != expected_row_version:
                raise ServiceError(
                    "WORKFLOW_FUNCTION_CONFLICT",
                    "Function 已被其他请求修改，请重新载入",
                    status_code=409,
                    details={"actual_row_version": row.row_version},
                )
            references: list[str] = []
            for workflow_row in session.scalars(select(ExperimentWorkflow)).all():
                workflow = WorkflowDefinition.model_validate(workflow_row.definition_json)
                references.extend(
                    f"{workflow_row.experiment_id}.{workflow.workflow_key}.{node.node_id}"
                    for node in workflow.nodes
                    if node.kind in {"code", "script"}
                    and node.script_mode == "shared"
                    and node.operation == function_key
                )
            if references:
                raise ServiceError(
                    "WORKFLOW_FUNCTION_IN_USE",
                    "该自定义 Function 正被代码节点使用，不能删除",
                    status_code=409,
                    details={"references": references[:50]},
                )
            session.delete(row)
            session.flush()
            return {"items": self._function_catalog(session)}

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

    def get_revision_workflow(
        self, experiment_id: str, revision_id: str, workflow_key: str
    ) -> dict[str, Any]:
        """Return one Revision-owned graph without exposing a mutation path."""

        self._validate_key(workflow_key)
        with self.database.session_factory() as session:
            _experiment, revision, definition = self._require_revision(
                session, experiment_id, revision_id
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
        workflow = ensure_llm_context_inputs(workflow)
        if workflow.workflow_key != workflow_key:
            raise ServiceError(
                "WORKFLOW_KEY_MISMATCH", "流程 key 与 URL 不一致", status_code=422
            )
        with self.database.session_factory.begin() as session:
            experiment, revision, definition = self._require_draft(
                session, experiment_id
            )
            available_operations = self.allowed_script_operations_in_session(session)
            for node in workflow.nodes:
                if node.kind not in {"code", "script"}:
                    continue
                if node.script_mode == "shared" and node.operation not in available_operations:
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
            invalid_by_prompt: dict[str, list[str]] = {}
            for node in workflow.nodes:
                if node.kind != "llm" or not node.prompt_key:
                    continue
                content = prompt_contents.get(
                    node.prompt_key,
                    definition.prompts.get(node.prompt_key).content
                    if node.prompt_key in definition.prompts
                    else "",
                )
                invalid = _invalid_prompt_variables(node, content)
                if invalid:
                    invalid_by_prompt[node.prompt_key] = invalid
            if invalid_by_prompt:
                raise ServiceError(
                    "WORKFLOW_PROMPT_VARIABLE_AMBIGUOUS",
                    "Prompt 变量必须使用明确的输入变量与属性路径",
                    status_code=422,
                    details={"invalid_variables": invalid_by_prompt},
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

    def migrate_to_prompt_router(
        self,
        experiment_id: str,
        workflow_key: str,
        *,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        """Replace a legacy graph with the executable router default.

        ``save_workflow`` creates a normal immutable history version, so the
        previous graph remains recoverable after this explicit migration.
        """

        self._validate_key(workflow_key)
        return self.save_workflow(
            experiment_id,
            workflow_key,
            expected_lock_version=expected_lock_version,
            workflow=make_default_workflows()[workflow_key],
            prompt_contents={},
            label="迁移为真实 Prompt 路由",
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
            workflow = ensure_llm_context_inputs(
                WorkflowDefinition.model_validate(version.definition_json)
            )
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
            restored_prompts = canonicalize_prompt_payload(version.prompt_contents_json)
            for key, prompt in restored_prompts.items():
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
            issues = workflow_validation_issues(
                workflows,
                definition,
                allowed_script_operations=self.allowed_script_operations_in_session(session),
            )
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

    def test_run_workflow(
        self,
        experiment_id: str,
        workflow_key: str,
        *,
        workflow: WorkflowDefinition,
        inputs: dict[str, Any],
        llm_outputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute the unsaved canvas with deterministic LLM fixtures.

        This is intentionally a graph test, not a model connectivity test.  It
        proves that ports, branches and deterministic nodes actually execute;
        the model page owns live provider probes.
        """

        self._validate_key(workflow_key)
        workflow = ensure_llm_context_inputs(workflow)
        if workflow.workflow_key != workflow_key:
            raise ServiceError(
                "WORKFLOW_KEY_MISMATCH",
                "流程 key 与 URL 不一致",
                status_code=422,
            )
        with self.database.session_factory() as session:
            _experiment, revision, _definition = self._require_draft(
                session, experiment_id
            )
            workflows = self.load_revision_bundle_in_session(session, revision.id)
            workflows[workflow_key] = workflow
            operation_keys = {
                node.operation
                for item in workflows.values()
                for node in item.nodes
                if node.kind in {"code", "script"}
                and node.script_mode == "shared"
                and node.operation not in ALLOWED_SCRIPT_OPERATIONS
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
            "revision_no": revision.revision_no,
            "lock_version": revision.lock_version,
            "readonly": revision.state != "DRAFT",
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
