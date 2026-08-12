"""Agent workflow contracts shared by persistence, API validation and the UI."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,79}$"),
]
WorkflowKey = Literal["schedule", "memory", "action", "social", "reflection"]
NodeKind = Literal[
    "start",
    "end",
    "llm",
    "code",
    "selector",
    "variable_assigner",
    "variable_aggregator",
    "subflow",
    # Legacy aliases remain readable so published Stanford Town revisions and
    # in-flight Runs do not break.  They are not offered for new nodes.
    "script",
    "if_else",
    "switch",
    "loop",
    "parallel",
    "read_state",
    "write_state",
]

STANDARD_WORKFLOW_NODE_KINDS = (
    "start",
    "end",
    "llm",
    "code",
    "selector",
    "variable_assigner",
    "variable_aggregator",
    "subflow",
)
LEGACY_WORKFLOW_NODE_KINDS = (
    "script",
    "if_else",
    "switch",
    "loop",
    "parallel",
    "read_state",
    "write_state",
)


def list_standard_workflow_node_types() -> list[dict[str, Any]]:
    """Small executable palette aligned with Coze's atomic node vocabulary."""

    return [
        {"kind": "start", "title": "开始", "category": "boundary", "addable": False},
        {"kind": "end", "title": "结束", "category": "boundary", "addable": False},
        {"kind": "llm", "title": "大模型", "category": "ai", "addable": True},
        {"kind": "code", "title": "代码", "category": "data", "addable": True},
        {"kind": "selector", "title": "选择器", "category": "logic", "addable": True},
        {
            "kind": "variable_assigner",
            "title": "变量赋值",
            "category": "data",
            "addable": True,
        },
        {
            "kind": "variable_aggregator",
            "title": "变量聚合",
            "category": "data",
            "addable": True,
        },
        {"kind": "subflow", "title": "子工作流", "category": "reuse", "addable": True},
    ]


_STRING = {"type": "string"}
_BOOLEAN = {"type": "boolean"}
_INTEGER = {"type": "integer"}
_PROMPT_RESPONSE_VALUES: dict[str, dict[str, Any]] = {
    "base_desc": _STRING,
    "wake_up": {"type": "integer", "minimum": 0, "maximum": 11},
    "schedule_init": {"type": "array", "items": _STRING, "minItems": 3},
    "schedule_daily": {"type": "object", "additionalProperties": _STRING},
    "schedule_decompose": {
        "type": "array",
        "items": {
            "type": "array",
            "prefixItems": [_STRING, {"type": "integer", "minimum": 1}],
            "minItems": 2,
            "maxItems": 2,
        },
    },
    "schedule_revise": {
        "type": "array",
        "items": {
            "type": "array",
            "prefixItems": [_STRING, _STRING, _STRING],
            "minItems": 3,
            "maxItems": 3,
        },
    },
    "retrieve_plan": {"type": "array", "items": _STRING, "minItems": 1},
    "retrieve_thought": _STRING,
    "retrieve_currently": _STRING,
    "poignancy_event": {"type": "integer", "minimum": 1, "maximum": 10},
    "poignancy_chat": {"type": "integer", "minimum": 1, "maximum": 10},
    "determine_sector": _STRING,
    "determine_arena": _STRING,
    "determine_object": _STRING,
    "describe_event": {
        "type": "array",
        "items": {
            "type": "array",
            "prefixItems": [_STRING, _STRING, _STRING],
            "minItems": 3,
            "maxItems": 3,
        },
    },
    "describe_object": _STRING,
    "describe_emoji": _STRING,
    "decide_chat": _BOOLEAN,
    "decide_chat_terminate": _BOOLEAN,
    "decide_wait_example": _STRING,
    "decide_wait": {"type": "string", "enum": ["A", "B"]},
    "summarize_relation": _STRING,
    "generate_chat": {"type": "string", "minLength": 1},
    "generate_chat_check_repeat": _BOOLEAN,
    "summarize_chats": _STRING,
    "reflect_focus": {"type": "array", "items": _STRING},
    "reflect_insights": {
        "type": "array",
        "items": {
            "type": "array",
            "prefixItems": [_STRING, _STRING],
            "minItems": 2,
            "maxItems": 2,
        },
    },
    "reflect_chat_planing": _STRING,
    "reflect_chat_memory": _STRING,
}


def llm_response_schema(prompt_key: str | None) -> dict[str, Any]:
    """Return the strict response envelope used by model providers and retries."""

    value_schema = _PROMPT_RESPONSE_VALUES.get(
        prompt_key or "",
        {
            "type": [
                "object",
                "array",
                "string",
                "number",
                "integer",
                "boolean",
                "null",
            ]
        },
    )
    # Round-trip copying keeps nested schemas private to each node.
    value_schema = json.loads(json.dumps(value_schema, ensure_ascii=False))
    return {
        "type": "object",
        "properties": {"res": value_schema},
        "required": ["res"],
        "additionalProperties": False,
    }


class WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class WorkflowPort(WorkflowModel):
    name: Identifier
    data_type: Annotated[str, StringConstraints(min_length=1, max_length=120)] = "any"
    required: bool = True
    description: Annotated[str, StringConstraints(max_length=500)] = ""


class WorkflowPosition(WorkflowModel):
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=20_000)


class WorkflowNode(WorkflowModel):
    node_id: Identifier
    kind: NodeKind
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    inputs: list[WorkflowPort] = Field(default_factory=list)
    outputs: list[WorkflowPort] = Field(default_factory=list)
    position: WorkflowPosition
    prompt_key: Identifier | None = None
    operation: Identifier | None = None
    script_mode: Literal["shared", "inline"] | None = None
    script_source: Annotated[str, StringConstraints(max_length=12_000)] | None = None
    expression: Annotated[str, StringConstraints(max_length=4_000)] | None = None
    state_path: Annotated[str, StringConstraints(max_length=500)] | None = None
    subflow_key: WorkflowKey | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_kind_contract(self) -> "WorkflowNode":
        if len({item.name for item in self.inputs}) != len(self.inputs):
            raise ValueError("workflow node input names must be unique")
        if len({item.name for item in self.outputs}) != len(self.outputs):
            raise ValueError("workflow node output names must be unique")
        required = {
            "llm": ("prompt_key", self.prompt_key),
            "selector": ("expression", self.expression),
            "if_else": ("expression", self.expression),
            "switch": ("expression", self.expression),
            "loop": ("expression", self.expression),
            "variable_assigner": ("state_path", self.state_path),
            "read_state": ("state_path", self.state_path),
            "write_state": ("state_path", self.state_path),
            "subflow": ("subflow_key", self.subflow_key),
        }
        contract = required.get(self.kind)
        if contract and not contract[1]:
            raise ValueError(f"{self.kind} node requires {contract[0]}")
        if self.kind != "llm" and self.prompt_key is not None:
            raise ValueError("prompt_key is only valid for llm nodes")
        code_kinds = {"code", "script"}
        if self.kind not in code_kinds and self.operation is not None:
            raise ValueError("operation is only valid for code nodes")
        if self.kind not in code_kinds and (self.script_mode is not None or self.script_source is not None):
            raise ValueError("script_mode and script_source are only valid for code nodes")
        if self.kind in code_kinds:
            mode = self.script_mode or ("inline" if self.script_source else "shared")
            if mode == "shared" and not self.operation:
                raise ValueError("shared code node requires operation")
            if mode == "inline" and not (self.script_source or "").strip():
                raise ValueError("inline code node requires script_source")
            object.__setattr__(self, "script_mode", mode)
        if self.kind == "selector":
            config = dict(self.config)
            config.setdefault(
                "selector_mode", "boolean" if len(self.outputs) <= 2 else "case"
            )
            if config["selector_mode"] not in {"boolean", "case"}:
                raise ValueError("selector_mode must be boolean or case")
            object.__setattr__(self, "config", config)
        if self.kind == "llm":
            config = dict(self.config)
            config.setdefault("response_schema", llm_response_schema(self.prompt_key))
            config.setdefault(
                "retry_policy",
                {"max_attempts": 3, "retry_on_schema_error": True},
            )
            response_schema = config["response_schema"]
            if (
                not isinstance(response_schema, dict)
                or response_schema.get("type") != "object"
                or not isinstance(response_schema.get("properties"), dict)
                or "res" not in response_schema["properties"]
                or "res" not in response_schema.get("required", [])
            ):
                raise ValueError(
                    "llm node response_schema must be an object with required field res"
                )
            retry_policy = config["retry_policy"]
            attempts = retry_policy.get("max_attempts") if isinstance(retry_policy, dict) else None
            if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= 10:
                raise ValueError("llm node retry_policy.max_attempts must be between 1 and 10")
            if not isinstance(retry_policy.get("retry_on_schema_error"), bool):
                raise ValueError("llm node retry_on_schema_error must be boolean")
            object.__setattr__(self, "config", config)
        return self


class WorkflowEdge(WorkflowModel):
    source_node_id: Identifier
    source_port: Identifier
    target_node_id: Identifier
    target_port: Identifier
    branch: Literal["always", "true", "false", "case", "error"] = "always"
    case_value: str | None = None

    @model_validator(mode="after")
    def case_requires_value(self) -> "WorkflowEdge":
        if self.branch == "case" and self.case_value is None:
            raise ValueError("case edge requires case_value")
        if self.branch != "case" and self.case_value is not None:
            raise ValueError("case_value is only valid for case edges")
        return self


class WorkflowDefinition(WorkflowModel):
    workflow_key: WorkflowKey
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    description: Annotated[str, StringConstraints(max_length=2_000)] = ""
    execution_mode: Literal["prompt_router", "native", "legacy_prompt_hook"] = (
        "legacy_prompt_hook"
    )
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinition":
        by_id = {node.node_id: node for node in self.nodes}
        if len(by_id) != len(self.nodes):
            raise ValueError("workflow node_id must be unique")
        if sum(node.kind == "start" for node in self.nodes) != 1:
            raise ValueError("workflow requires exactly one start node")
        if sum(node.kind == "end" for node in self.nodes) != 1:
            raise ValueError("workflow requires exactly one end node")
        edge_keys: set[tuple[str, str, str, str, str, str | None]] = set()
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in by_id}
        reverse: dict[str, set[str]] = {node_id: set() for node_id in by_id}
        for edge in self.edges:
            key = (
                edge.source_node_id,
                edge.source_port,
                edge.target_node_id,
                edge.target_port,
                edge.branch,
                edge.case_value,
            )
            if key in edge_keys:
                raise ValueError("workflow edges must be unique")
            edge_keys.add(key)
            source = by_id.get(edge.source_node_id)
            target = by_id.get(edge.target_node_id)
            if source is None or target is None:
                raise ValueError("workflow edge references an unknown node")
            source_port = next((item for item in source.outputs if item.name == edge.source_port), None)
            target_port = next((item for item in target.inputs if item.name == edge.target_port), None)
            if source_port is None or target_port is None:
                raise ValueError("workflow edge references an unknown port")
            if (
                source_port.data_type != "any"
                and target_port.data_type != "any"
                and source_port.data_type != target_port.data_type
            ):
                raise ValueError("workflow edge data types are incompatible")
            adjacency[source.node_id].add(target.node_id)
            reverse[target.node_id].add(source.node_id)

        start_id = next(node.node_id for node in self.nodes if node.kind == "start")
        end_id = next(node.node_id for node in self.nodes if node.kind == "end")

        def visit(graph: dict[str, set[str]], initial: str) -> set[str]:
            seen: set[str] = set()
            pending = [initial]
            while pending:
                node_id = pending.pop()
                if node_id in seen:
                    continue
                seen.add(node_id)
                pending.extend(graph[node_id] - seen)
            return seen

        if visit(adjacency, start_id) != set(by_id):
            raise ValueError("every workflow node must be reachable from start")
        if visit(reverse, end_id) != set(by_id):
            raise ValueError("every workflow node must reach end")
        # Coze-style top-level workflows are DAGs. Loop and batch are composite
        # nodes with their own inner graph; a back-edge on the main canvas is not
        # a loop and would make readiness semantics ambiguous.
        indegree = {node_id: len(reverse[node_id]) for node_id in by_id}
        ready = [node_id for node_id, count in indegree.items() if count == 0]
        visited = 0
        while ready:
            node_id = ready.pop()
            visited += 1
            for target_id in adjacency[node_id]:
                indegree[target_id] -= 1
                if indegree[target_id] == 0:
                    ready.append(target_id)
        if visited != len(by_id):
            raise ValueError("workflow main graph must be acyclic")
        return self


def workflow_hash(workflow: WorkflowDefinition | dict[str, Any]) -> str:
    value = (
        workflow.model_dump(mode="json", exclude_none=False)
        if isinstance(workflow, WorkflowDefinition)
        else workflow
    )
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def workflow_bundle_hash(
    workflows: dict[str, WorkflowDefinition] | dict[str, dict[str, Any]],
) -> str:
    normalized = {
        key: (
            value.model_dump(mode="json", exclude_none=False)
            if isinstance(value, WorkflowDefinition)
            else value
        )
        for key, value in sorted(workflows.items())
    }
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_FLOW_SPECS: dict[WorkflowKey, tuple[str, str, list[tuple[str, str]]]] = {
    "schedule": (
        "日程与状态",
        "承接旧 Agent 的日程 Prompt 调用，并按 prompt_key 路由到唯一可执行分支。",
        [
            ("base_desc", "构建 Agent 基础上下文"),
            ("retrieve_plan", "检索历史计划"),
            ("retrieve_thought", "检索历史思考"),
            ("retrieve_currently", "恢复当前自我状态"),
            ("wake_up", "确定起床时间"),
            ("schedule_init", "生成日程骨架"),
            ("schedule_daily", "生成全天日程"),
            ("schedule_decompose", "拆解当前活动"),
            ("schedule_revise", "修订当前日程"),
        ],
    ),
    "memory": (
        "感知与记忆",
        "承接旧 Agent 的记忆 Prompt 调用，并路由到事件或对话重要度分支。",
        [
            ("poignancy_event", "评估事件重要性"),
            ("poignancy_chat", "评估对话重要性"),
        ],
    ),
    "action": (
        "行动与空间",
        "承接旧 Agent 的空间行动 Prompt 调用；行动顺序仍由 Stanford Agent 适配层负责。",
        [
            ("determine_sector", "选择目标区域"),
            ("determine_arena", "选择目标场所"),
            ("determine_object", "选择交互对象"),
            ("describe_event", "结构化 Agent 事件"),
            ("describe_object", "描述对象状态"),
            ("describe_emoji", "生成行动表情"),
        ],
    ),
    "social": (
        "社交与对话",
        "承接旧 Agent 的社交 Prompt 调用，并选择本次真实执行的对话分支。",
        [
            ("decide_chat", "判断是否发起对话"),
            ("summarize_relation", "总结双方关系"),
            ("generate_chat", "生成下一轮对话"),
            ("generate_chat_check_repeat", "检测重复表达"),
            ("decide_chat_terminate", "判断是否结束对话"),
            ("summarize_chats", "总结对话内容"),
            ("decide_wait_example", "构建等待判断样例"),
            ("decide_wait", "判断是否等待他人"),
        ],
    ),
    "reflection": (
        "反思与认知",
        "承接旧 Agent 的反思 Prompt 调用，并选择本次真实执行的认知分支。",
        [
            ("reflect_focus", "提炼反思焦点"),
            ("reflect_insights", "生成认知洞察"),
            ("reflect_chat_planing", "提取对计划的影响"),
            ("reflect_chat_memory", "提取值得记忆的内容"),
        ],
    ),
}

# Prompt templates normally consume one structured ``context`` object.  These
# schedule Prompts also expose values as explicit top-level template roots;
# keeping them as ports makes the editor and publication validator truthful.
_PROMPT_EXTRA_INPUTS: dict[str, tuple[str, ...]] = {
    "retrieve_currently": ("plan", "thought"),
    "schedule_init": ("base_desc", "wake_up"),
    "schedule_daily": ("base_desc", "daily_schedule"),
    "schedule_decompose": ("plan",),
}


def _port(name: str, data_type: str = "any", description: str = "") -> WorkflowPort:
    return WorkflowPort(name=name, data_type=data_type, description=description)


def _llm_node(
    node_id: str,
    prompt_key: str,
    title: str,
    *,
    inputs: list[WorkflowPort],
    output_type: str,
    x: float,
    y: float,
) -> WorkflowNode:
    return WorkflowNode(
        node_id=node_id,
        kind="llm",
        title=title,
        prompt_key=prompt_key,
        inputs=inputs,
        outputs=[_port("result", output_type, "通过 JSON Schema 校验后的 res 字段")],
        position=WorkflowPosition(x=x, y=y),
    )


def _default_schedule_workflow(title: str, description: str) -> WorkflowDefinition:
    """Mirror the real new-day, current-plan and interruption schedule paths."""

    nodes = [
        WorkflowNode(
            node_id="start",
            kind="start",
            title="开始本轮 Agent Step",
            outputs=[
                _port(
                    "step_context",
                    "StepContext",
                    "运行时自动注入：当前 Agent、仿真时钟、位置、可见事件、记忆与触发原因；不是上游节点生成的数据。",
                )
            ],
            position=WorkflowPosition(x=40, y=24),
        ),
        WorkflowNode(
            node_id="prepare_context",
            kind="code",
            title="准备日程上下文",
            operation="schedule_prepare_context",
            inputs=[_port("step_context", "StepContext", "来自 Start 的系统运行时上下文")],
            outputs=[_port("context", "ScheduleContext", "日程所需的 Agent、时钟、记忆和触发原因")],
            position=WorkflowPosition(x=40, y=155),
        ),
        _llm_node(
            "prompt_base_desc",
            "base_desc",
            "构建 Agent 基础上下文",
            inputs=[_port("context", "ScheduleContext")],
            output_type="AgentProfileText",
            x=40,
            y=286,
        ),
        WorkflowNode(
            node_id="schedule_trigger",
            kind="selector",
            title="判断日程触发原因",
            expression="context.trigger",
            inputs=[
                _port("context", "ScheduleContext"),
                _port("base_desc", "AgentProfileText"),
            ],
            outputs=[
                _port("new_day", "ScheduleContext", "尚未生成今日计划"),
                _port("current_plan", "ScheduleContext", "已有计划，需要检查当前活动"),
                _port("interruption", "ScheduleContext", "新事件要求修订日程"),
            ],
            position=WorkflowPosition(x=40, y=430),
            config={"selector_mode": "case"},
        ),
        WorkflowNode(
            node_id="has_recent_memories",
            kind="selector",
            title="是否有近期计划与思考",
            expression="bool(context.memories)",
            inputs=[_port("context", "ScheduleContext")],
            outputs=[
                _port("with_memories", "ScheduleContext"),
                _port("without_memories", "ScheduleContext"),
            ],
            position=WorkflowPosition(x=5, y=590),
        ),
        _llm_node(
            "prompt_retrieve_plan",
            "retrieve_plan",
            "检索历史计划",
            inputs=[_port("context", "ScheduleContext")],
            output_type="PlanMemory[]",
            x=2,
            y=750,
        ),
        _llm_node(
            "prompt_retrieve_thought",
            "retrieve_thought",
            "检索历史思考",
            inputs=[_port("context", "ScheduleContext")],
            output_type="ThoughtMemory",
            x=28,
            y=750,
        ),
        _llm_node(
            "prompt_retrieve_currently",
            "retrieve_currently",
            "恢复当前自我状态",
            inputs=[
                _port("plan", "PlanMemory[]"),
                _port("thought", "ThoughtMemory"),
            ],
            output_type="AgentState",
            x=16,
            y=920,
        ),
        WorkflowNode(
            node_id="keep_current_state",
            kind="code",
            title="沿用当前 Agent 状态",
            operation="identity",
            inputs=[_port("input", "ScheduleContext")],
            outputs=[_port("result", "AgentState")],
            position=WorkflowPosition(x=50, y=750),
        ),
        _llm_node(
            "prompt_wake_up",
            "wake_up",
            "确定起床时间",
            inputs=[
                _port("context", "ScheduleContext"),
                WorkflowPort(name="current_state", data_type="AgentState", required=False),
            ],
            output_type="WakeHour",
            x=18,
            y=1085,
        ),
        _llm_node(
            "prompt_schedule_init",
            "schedule_init",
            "生成日程骨架",
            inputs=[
                _port("base_desc", "AgentProfileText"),
                _port("wake_up", "WakeHour"),
            ],
            output_type="ScheduleOutline",
            x=18,
            y=1245,
        ),
        _llm_node(
            "prompt_schedule_daily",
            "schedule_daily",
            "生成并校验全天日程",
            inputs=[
                _port("base_desc", "AgentProfileText"),
                _port("wake_up", "WakeHour"),
                _port("daily_schedule", "ScheduleOutline"),
            ],
            output_type="DailySchedule",
            x=18,
            y=1405,
        ),
        WorkflowNode(
            node_id="need_decompose",
            kind="selector",
            title="当前活动是否需要拆解",
            expression=(
                "bool(current_context) and bool(current_context.plan) and "
                "current_context.plan.duration >= current_context.decompose_threshold"
            ),
            inputs=[
                WorkflowPort(name="new_schedule", data_type="DailySchedule", required=False),
                WorkflowPort(name="current_context", data_type="ScheduleContext", required=False),
            ],
            outputs=[
                _port("decompose_plan", "PlanContext"),
                _port("unchanged", "ScheduleResult"),
            ],
            position=WorkflowPosition(x=42, y=1565),
        ),
        _llm_node(
            "prompt_schedule_decompose",
            "schedule_decompose",
            "拆解当前活动",
            inputs=[_port("plan", "PlanContext")],
            output_type="DecomposedPlan",
            x=42,
            y=1725,
        ),
        _llm_node(
            "prompt_schedule_revise",
            "schedule_revise",
            "按突发事件修订日程",
            inputs=[_port("context", "ScheduleContext")],
            output_type="DecomposedPlan",
            x=72,
            y=750,
        ),
        WorkflowNode(
            node_id="finalize_schedule",
            kind="code",
            title="汇总并写回日程结果",
            operation="merge_context",
            inputs=[
                WorkflowPort(name="daily_schedule", data_type="DailySchedule", required=False),
                WorkflowPort(name="decomposition", data_type="DecomposedPlan", required=False),
                WorkflowPort(name="revised", data_type="DecomposedPlan", required=False),
                WorkflowPort(name="unchanged", data_type="ScheduleResult", required=False),
            ],
            outputs=[_port("context", "ScheduleResult")],
            position=WorkflowPosition(x=42, y=1885),
        ),
        WorkflowNode(
            node_id="end",
            kind="end",
            title="返回本轮日程状态",
            inputs=[_port("result", "ScheduleResult")],
            outputs=[_port("flow_result", "ScheduleResult")],
            position=WorkflowPosition(x=42, y=2045),
        ),
    ]

    def edge(
        source: str,
        source_port: str,
        target: str,
        target_port: str,
        branch: Literal["always", "true", "false", "case", "error"] = "always",
        case_value: str | None = None,
    ) -> WorkflowEdge:
        return WorkflowEdge(
            source_node_id=source,
            source_port=source_port,
            target_node_id=target,
            target_port=target_port,
            branch=branch,
            case_value=case_value,
        )

    edges = [
        edge("start", "step_context", "prepare_context", "step_context"),
        edge("prepare_context", "context", "prompt_base_desc", "context"),
        edge("prepare_context", "context", "schedule_trigger", "context"),
        edge("prompt_base_desc", "result", "schedule_trigger", "base_desc"),
        edge("schedule_trigger", "new_day", "has_recent_memories", "context", "case", "new_day"),
        edge("has_recent_memories", "with_memories", "prompt_retrieve_plan", "context", "true"),
        edge("has_recent_memories", "with_memories", "prompt_retrieve_thought", "context", "true"),
        edge("prompt_retrieve_plan", "result", "prompt_retrieve_currently", "plan"),
        edge("prompt_retrieve_thought", "result", "prompt_retrieve_currently", "thought"),
        edge("has_recent_memories", "without_memories", "keep_current_state", "input", "false"),
        edge("schedule_trigger", "new_day", "prompt_wake_up", "context", "case", "new_day"),
        edge("prompt_retrieve_currently", "result", "prompt_wake_up", "current_state"),
        edge("keep_current_state", "result", "prompt_wake_up", "current_state"),
        edge("prompt_base_desc", "result", "prompt_schedule_init", "base_desc"),
        edge("prompt_wake_up", "result", "prompt_schedule_init", "wake_up"),
        edge("prompt_base_desc", "result", "prompt_schedule_daily", "base_desc"),
        edge("prompt_wake_up", "result", "prompt_schedule_daily", "wake_up"),
        edge("prompt_schedule_init", "result", "prompt_schedule_daily", "daily_schedule"),
        edge("prompt_schedule_daily", "result", "need_decompose", "new_schedule"),
        edge("schedule_trigger", "current_plan", "need_decompose", "current_context", "case", "current_plan"),
        edge("need_decompose", "decompose_plan", "prompt_schedule_decompose", "plan", "true"),
        edge("need_decompose", "unchanged", "finalize_schedule", "unchanged", "false"),
        edge("prompt_schedule_daily", "result", "finalize_schedule", "daily_schedule"),
        edge("prompt_schedule_decompose", "result", "finalize_schedule", "decomposition"),
        edge("schedule_trigger", "interruption", "prompt_schedule_revise", "context", "case", "interruption"),
        edge("prompt_schedule_revise", "result", "finalize_schedule", "revised"),
        edge("finalize_schedule", "context", "end", "result"),
    ]
    return WorkflowDefinition(
        workflow_key="schedule",
        title=title,
        description=description,
        nodes=nodes,
        edges=edges,
    )


def _default_workflow(
    key: WorkflowKey,
    title: str,
    description: str,
    prompts: list[tuple[str, str]],
) -> WorkflowDefinition:
    """Build the executable Stanford Agent adapter for one capability group.

    The legacy Agent requests one Prompt operation at a time.  A selector-routed
    graph represents that contract honestly; a linear chain would incorrectly
    execute unrelated Prompts merely because they share a product category.
    """

    nodes: list[WorkflowNode] = [
        WorkflowNode(
            node_id="start",
            kind="start",
            title="Prompt 调用入口",
            outputs=[_port("step_context", "StepContext", "当前 Agent 与 Prompt 调用上下文")],
            position=WorkflowPosition(x=36, y=24),
        ),
        WorkflowNode(
            node_id="prepare_context",
            kind="code",
            title="准备流程上下文",
            operation=f"{key}_prepare_context",
            inputs=[_port("step_context", "StepContext")],
            outputs=[_port("context", "any", "供后续节点消费的流程上下文")],
            position=WorkflowPosition(x=36, y=170),
        ),
        WorkflowNode(
            node_id="route_prompt",
            kind="selector",
            title="按 Prompt 能力路由",
            expression="context.prompt_key",
            inputs=[_port("context", "any", "包含 prompt_key 与模型调用参数")],
            outputs=[
                _port(prompt_key, "any", f"路由到 {prompt_title}")
                for prompt_key, prompt_title in prompts
            ],
            position=WorkflowPosition(x=36, y=330),
            config={"selector_mode": "case"},
        ),
    ]
    edges: list[WorkflowEdge] = [
        WorkflowEdge(
            source_node_id="start",
            source_port="step_context",
            target_node_id="prepare_context",
            target_port="step_context",
        ),
        WorkflowEdge(
            source_node_id="prepare_context",
            source_port="context",
            target_node_id="route_prompt",
            target_port="context",
        ),
    ]
    columns = 3
    for index, (prompt_key, prompt_title) in enumerate(prompts):
        node_id = f"prompt_{prompt_key}"
        input_names = ("context", *_PROMPT_EXTRA_INPUTS.get(prompt_key, ()))
        nodes.append(
            _llm_node(
                node_id,
                prompt_key,
                prompt_title,
                inputs=[
                    _port(name, "any", "当前 Prompt 调用的具名上下文")
                    for name in input_names
                ],
                output_type="any",
                x=8 + (index % columns) * 34,
                y=520 + (index // columns) * 190,
            )
        )
        edges.extend(
            WorkflowEdge(
                source_node_id="route_prompt",
                source_port=prompt_key,
                target_node_id=node_id,
                target_port=input_name,
                branch="case",
                case_value=prompt_key,
            )
            for input_name in input_names
        )
    end_y = 710 + ((len(prompts) - 1) // columns) * 190
    nodes.append(
        WorkflowNode(
            node_id="end",
            kind="end",
            title="返回流程结果",
            inputs=[_port("result", "any")],
            outputs=[_port("flow_result", "any")],
            position=WorkflowPosition(x=36, y=end_y),
        )
    )
    edges.extend(
        WorkflowEdge(
            source_node_id=f"prompt_{prompt_key}",
            source_port="result",
            target_node_id="end",
            target_port="result",
        )
        for prompt_key, _prompt_title in prompts
    )
    return WorkflowDefinition(
        workflow_key=key,
        title=title,
        description=description,
        execution_mode="prompt_router",
        nodes=nodes,
        edges=edges,
    )


def ensure_llm_context_inputs(workflow: WorkflowDefinition) -> WorkflowDefinition:
    """Keep the runtime-provided ``context`` port explicit on every LLM node."""

    for node in workflow.nodes:
        if node.kind == "llm" and not any(port.name == "context" for port in node.inputs):
            node.inputs.append(
                WorkflowPort(
                    name="context",
                    data_type="any",
                    required=False,
                    description="运行时为该 Prompt 准备的具名输入上下文",
                )
            )
    return workflow


def make_default_workflows() -> dict[str, WorkflowDefinition]:
    """Return fresh system workflows; callers may safely mutate the result."""

    return {
        key: ensure_llm_context_inputs(
            _default_workflow(key, title, description, prompts)
        )
        for key, (title, description, prompts) in _FLOW_SPECS.items()
    }


DEFAULT_WORKFLOW_KEYS = tuple(_FLOW_SPECS)
