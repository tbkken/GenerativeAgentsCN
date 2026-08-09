"""Prompt workflow contracts shared by persistence, API validation and the UI.

The graph is deliberately declarative.  ``script`` nodes reference a registered
operation key; source code entered in the browser is never executed directly.
This keeps experiment isolation intact while still making data flow explicit.
"""

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
    "script",
    "llm",
    "if_else",
    "switch",
    "loop",
    "parallel",
    "read_state",
    "write_state",
    "subflow",
]


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
            "script": ("operation", self.operation),
            "if_else": ("expression", self.expression),
            "switch": ("expression", self.expression),
            "loop": ("expression", self.expression),
            "read_state": ("state_path", self.state_path),
            "write_state": ("state_path", self.state_path),
            "subflow": ("subflow_key", self.subflow_key),
        }
        contract = required.get(self.kind)
        if contract and not contract[1]:
            raise ValueError(f"{self.kind} node requires {contract[0]}")
        if self.kind != "llm" and self.prompt_key is not None:
            raise ValueError("prompt_key is only valid for llm nodes")
        if self.kind != "script" and self.operation is not None:
            raise ValueError("operation is only valid for script nodes")
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
        "生成、拆解和修订 Agent 的日程，并恢复跨天状态。",
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
        "把环境观察转换为带重要度的结构化记忆。",
        [
            ("poignancy_event", "评估事件重要性"),
            ("poignancy_chat", "评估对话重要性"),
        ],
    ),
    "action": (
        "行动与空间",
        "根据当前活动选择地点、对象并生成可执行事件。",
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
        "判断互动机会、生成多轮对话并总结关系变化。",
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
        "从高重要度记忆和对话中提炼问题、洞察与新记忆。",
        [
            ("reflect_focus", "提炼反思焦点"),
            ("reflect_insights", "生成认知洞察"),
            ("reflect_chat_planing", "提取对计划的影响"),
            ("reflect_chat_memory", "提取值得记忆的内容"),
        ],
    ),
}


def _port(name: str, data_type: str = "any", description: str = "") -> WorkflowPort:
    return WorkflowPort(name=name, data_type=data_type, description=description)


def _default_workflow(
    key: WorkflowKey,
    title: str,
    description: str,
    prompts: list[tuple[str, str]],
) -> WorkflowDefinition:
    nodes: list[WorkflowNode] = [
        WorkflowNode(
            node_id="start",
            kind="start",
            title="Agent Step",
            outputs=[_port("step_context", "StepContext", "当前 Agent 与仿真步上下文")],
            position=WorkflowPosition(x=36, y=24),
        ),
        WorkflowNode(
            node_id="prepare_context",
            kind="script",
            title="准备流程上下文",
            operation=f"{key}_prepare_context",
            inputs=[_port("step_context", "StepContext")],
            outputs=[_port("context", "any", "供后续节点消费的流程上下文")],
            position=WorkflowPosition(x=36, y=170),
        ),
    ]
    edges: list[WorkflowEdge] = [
        WorkflowEdge(
            source_node_id="start",
            source_port="step_context",
            target_node_id="prepare_context",
            target_port="step_context",
        )
    ]
    previous_id = "prepare_context"
    previous_port = "context"
    for index, (prompt_key, prompt_title) in enumerate(prompts, start=1):
        node_id = f"prompt_{prompt_key}"
        nodes.append(
            WorkflowNode(
                node_id=node_id,
                kind="llm",
                title=prompt_title,
                prompt_key=prompt_key,
                inputs=[_port("context", "any", "上游节点输出与 Agent 状态")],
                outputs=[_port("result", "any", "结构化模型输出")],
                position=WorkflowPosition(x=36, y=170 + index * 170),
            )
        )
        edges.append(
            WorkflowEdge(
                source_node_id=previous_id,
                source_port=previous_port,
                target_node_id=node_id,
                target_port="context",
            )
        )
        previous_id = node_id
        previous_port = "result"
    end_y = 170 + (len(prompts) + 1) * 170
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
    edges.append(
        WorkflowEdge(
            source_node_id=previous_id,
            source_port=previous_port,
            target_node_id="end",
            target_port="result",
        )
    )
    return WorkflowDefinition(
        workflow_key=key,
        title=title,
        description=description,
        nodes=nodes,
        edges=edges,
    )


def make_default_workflows() -> dict[str, WorkflowDefinition]:
    """Return fresh system workflows; callers may safely mutate the result."""

    return {
        key: _default_workflow(key, title, description, prompts)
        for key, (title, description, prompts) in _FLOW_SPECS.items()
    }


DEFAULT_WORKFLOW_KEYS = tuple(_FLOW_SPECS)
