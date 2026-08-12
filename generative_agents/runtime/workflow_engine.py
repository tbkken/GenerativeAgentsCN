"""Executable, run-local data-flow engine for published Agent workflows.

The editor stores a graph.  This module is the runtime counterpart: values move
through declared ports and only selected branch edges are activated.  It is
deliberately independent from the legacy Agent so new scenario capabilities can
execute a workflow directly, while :meth:`execute_prompt_hook` provides the
explicit compatibility boundary used by Stanford Town Agents.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, MutableMapping

from generative_agents.config import WorkflowDefinition, WorkflowNode

from .workflow_functions import (
    invoke_inline_workflow_function,
    invoke_workflow_function,
)


LLMNodeHandler = Callable[[WorkflowNode, Mapping[str, Any], Mapping[str, Any]], Any]
WorkflowTraceHandler = Callable[[Mapping[str, Any]], None]


class WorkflowExecutionError(RuntimeError):
    """A published graph could not be executed according to its contract."""

    code = "WORKFLOW_EXECUTION_FAILED"

    def __init__(
        self,
        message: str,
        *,
        workflow_key: str,
        node_id: str | None = None,
    ) -> None:
        self.workflow_key = workflow_key
        self.node_id = node_id
        location = workflow_key if node_id is None else f"{workflow_key}.{node_id}"
        super().__init__(f"{location}: {message}")


@dataclass(slots=True)
class WorkflowExecutionResult:
    value: Any
    state: MutableMapping[str, Any]
    executed_nodes: tuple[str, ...]
    stopped_at_llm_nodes: tuple[str, ...] = ()


@dataclass(slots=True)
class _Execution:
    workflow: WorkflowDefinition
    runtime_context: Mapping[str, Any]
    state: MutableMapping[str, Any]
    llm_handler: LLMNodeHandler | None
    function_sources: Mapping[str, str]
    trace_handler: WorkflowTraceHandler | None
    invocation_id: str | None
    max_node_executions: int
    stop_before_llm: bool = False
    executed: list[str] = field(default_factory=list)
    stopped_at_llm: list[str] = field(default_factory=list)
    boundary_values: list[Any] = field(default_factory=list)


_UNRESOLVED = object()
_SKIPPED = object()


class _AttrValue:
    """Read-only attribute facade used by the restricted expression evaluator."""

    __slots__ = ("_value",)

    def __init__(self, value: Any) -> None:
        self._value = value

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        value = self._value
        if isinstance(value, Mapping) and name in value:
            return _expression_value(value[name])
        if hasattr(value, name):
            return _expression_value(getattr(value, name))
        raise AttributeError(name)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __iter__(self):
        for item in self._value:
            yield _expression_value(item)

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, _AttrValue):
            key = key._value
        return _expression_value(self._value[key])

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _AttrValue):
            other = other._value
        return self._value == other

    def __lt__(self, other: object) -> bool:
        if isinstance(other, _AttrValue):
            other = other._value
        return self._value < other

    def __le__(self, other: object) -> bool:
        if isinstance(other, _AttrValue):
            other = other._value
        return self._value <= other

    def __gt__(self, other: object) -> bool:
        if isinstance(other, _AttrValue):
            other = other._value
        return self._value > other

    def __ge__(self, other: object) -> bool:
        if isinstance(other, _AttrValue):
            other = other._value
        return self._value >= other

    def __str__(self) -> str:
        return str(self._value)


def _expression_value(value: Any) -> Any:
    if isinstance(value, (Mapping, list, tuple)) or hasattr(value, "__dict__"):
        return _AttrValue(value)
    return value


_ALLOWED_EXPRESSION_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Attribute,
    ast.Subscript,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.Call,
)
_EXPRESSION_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "all": all,
    "any": any,
    "bool": bool,
    "float": float,
    "int": int,
    "len": len,
    "max": max,
    "min": min,
    "str": str,
}


def evaluate_workflow_expression(
    expression: str,
    inputs: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
) -> Any:
    """Evaluate the small expression language exposed by the workflow editor."""

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid workflow expression: {exc.msg}") from exc
    for item in ast.walk(tree):
        if not isinstance(item, _ALLOWED_EXPRESSION_NODES):
            raise ValueError(
                f"workflow expression does not allow {type(item).__name__}"
            )
        if isinstance(item, ast.Name) and item.id.startswith("_"):
            raise ValueError("workflow expression cannot access private names")
        if isinstance(item, ast.Attribute) and item.attr.startswith("_"):
            raise ValueError("workflow expression cannot access private attributes")
        if isinstance(item, ast.Call):
            if not isinstance(item.func, ast.Name) or item.func.id not in _EXPRESSION_FUNCTIONS:
                raise ValueError("workflow expression calls an unapproved function")
    environment = {
        **_EXPRESSION_FUNCTIONS,
        **{key: _expression_value(value) for key, value in runtime_context.items()},
        **{key: _expression_value(value) for key, value in inputs.items()},
    }
    return eval(  # noqa: S307 - AST and callable allowlists are enforced above.
        compile(tree, "<workflow-expression>", "eval"),
        {"__builtins__": {}},
        environment,
    )


class WorkflowExecutor:
    """Execute immutable workflow definitions without ambient process state."""

    def __init__(
        self,
        workflows: Mapping[str, WorkflowDefinition],
        *,
        function_sources: Mapping[str, str] | None = None,
        trace_handler: WorkflowTraceHandler | None = None,
        max_node_executions: int = 1_000,
    ) -> None:
        if max_node_executions < 1:
            raise ValueError("max_node_executions must be positive")
        self._workflows = dict(workflows)
        self._function_sources = dict(function_sources or {})
        self._trace_handler = trace_handler
        self._max_node_executions = max_node_executions

    def execute(
        self,
        workflow_key: str,
        inputs: Mapping[str, Any],
        *,
        llm_handler: LLMNodeHandler,
        runtime_context: Mapping[str, Any] | None = None,
        state: MutableMapping[str, Any] | None = None,
        invocation_id: str | None = None,
    ) -> WorkflowExecutionResult:
        """Run a complete graph from Start through End."""

        execution = self._execution(
            workflow_key,
            llm_handler=llm_handler,
            runtime_context=runtime_context,
            state=state,
            invocation_id=invocation_id,
        )
        value = self._run_dataflow(execution, start_inputs=dict(inputs))
        return WorkflowExecutionResult(
            value=value,
            state=execution.state,
            executed_nodes=tuple(execution.executed),
            stopped_at_llm_nodes=tuple(execution.stopped_at_llm),
        )

    def execute_prompt_hook(
        self,
        workflow_key: str,
        node_id: str,
        value: Any,
        *,
        runtime_context: Mapping[str, Any] | None = None,
        state: MutableMapping[str, Any] | None = None,
        invocation_id: str | None = None,
    ) -> WorkflowExecutionResult:
        """Route one legacy Agent LLM result through its published graph.

        The deterministic prefix from Start is executed first.  The supplied LLM
        node is then treated as already completed and its result flows through
        downstream Script/control/state nodes.  Reaching another LLM is an
        explicit compatibility boundary; the legacy Agent will invoke that node
        at its own domain hook later.
        """

        execution = self._execution(
            workflow_key,
            llm_handler=None,
            runtime_context=runtime_context,
            state=state,
            invocation_id=invocation_id,
        )
        by_id = {node.node_id: node for node in execution.workflow.nodes}
        target = by_id.get(node_id)
        if target is None or target.kind != "llm":
            raise WorkflowExecutionError(
                "legacy prompt hook does not reference an LLM node",
                workflow_key=workflow_key,
                node_id=node_id,
            )

        # Execute the shared deterministic prefix so Start/prepare/read-state
        # nodes are not merely decorative.  Stop cleanly at the first LLM.
        prefix = self._execution(
            workflow_key,
            llm_handler=None,
            runtime_context=runtime_context,
            state=execution.state,
            invocation_id=invocation_id,
            stop_before_llm=True,
        )
        self._run_dataflow(
            prefix,
            start_inputs={"step_context": dict(runtime_context or {})},
            allow_incomplete=True,
        )
        execution.executed.extend(prefix.executed)
        execution.stopped_at_llm.extend(prefix.stopped_at_llm)

        output_map = self._normalize_outputs(target, value)
        self._trace(execution, target, "SUCCEEDED", output_map=output_map)
        execution.executed.append(target.node_id)
        result = self._run_dataflow(
            execution,
            seeded_node=(target.node_id, output_map),
            allow_incomplete=True,
        )
        if result is None:
            result = self._single_boundary_value(execution.boundary_values, value)
        return WorkflowExecutionResult(
            value=result,
            state=execution.state,
            executed_nodes=tuple(execution.executed),
            stopped_at_llm_nodes=tuple(execution.stopped_at_llm),
        )

    def _execution(
        self,
        workflow_key: str,
        *,
        llm_handler: LLMNodeHandler | None,
        runtime_context: Mapping[str, Any] | None,
        state: MutableMapping[str, Any] | None,
        invocation_id: str | None,
        stop_before_llm: bool = False,
    ) -> _Execution:
        workflow = self._workflows.get(workflow_key)
        if workflow is None:
            raise WorkflowExecutionError(
                "workflow is not present in the Run manifest",
                workflow_key=workflow_key,
            )
        return _Execution(
            workflow=workflow,
            runtime_context=dict(runtime_context or {}),
            state=state if state is not None else {},
            llm_handler=llm_handler,
            function_sources=self._function_sources,
            trace_handler=self._trace_handler,
            invocation_id=invocation_id,
            max_node_executions=self._max_node_executions,
            stop_before_llm=stop_before_llm,
        )

    def _run_dataflow(
        self,
        execution: _Execution,
        *,
        start_inputs: Mapping[str, Any] | None = None,
        seeded_node: tuple[str, Mapping[str, Any]] | None = None,
        allow_incomplete: bool = False,
    ) -> Any:
        workflow = execution.workflow
        by_id = {node.node_id: node for node in workflow.nodes}
        incoming: dict[str, list[int]] = {node_id: [] for node_id in by_id}
        outgoing: dict[str, list[int]] = {node_id: [] for node_id in by_id}
        for index, edge in enumerate(workflow.edges):
            incoming[edge.target_node_id].append(index)
            outgoing[edge.source_node_id].append(index)

        edge_values: list[Any] = [_UNRESOLVED] * len(workflow.edges)
        completed: set[str] = set()
        end_values: list[Any] = []

        if seeded_node is not None:
            seed_id, seed_outputs = seeded_node
            completed.add(seed_id)
            self._resolve_outgoing(
                execution,
                by_id[seed_id],
                seed_outputs,
                outgoing[seed_id],
                workflow,
                edge_values,
                branch_value=None,
            )

        while True:
            progress = False
            for node in workflow.nodes:
                if node.node_id in completed:
                    continue
                if seeded_node is not None and node.kind == "start":
                    completed.add(node.node_id)
                    self._skip_outgoing(outgoing[node.node_id], edge_values)
                    progress = True
                    continue
                if node.kind == "start":
                    if seeded_node is not None:
                        continue
                    node_inputs = dict(start_inputs or {})
                else:
                    indexes = incoming[node.node_id]
                    if any(edge_values[index] is _UNRESOLVED for index in indexes):
                        continue
                    node_inputs = self._collect_inputs(
                        node,
                        indexes,
                        workflow,
                        edge_values,
                    )
                    if node_inputs is None:
                        completed.add(node.node_id)
                        self._skip_outgoing(outgoing[node.node_id], edge_values)
                        self._trace(execution, node, "SKIPPED")
                        progress = True
                        continue

                if len(execution.executed) >= execution.max_node_executions:
                    raise WorkflowExecutionError(
                        "maximum node execution count exceeded",
                        workflow_key=workflow.workflow_key,
                        node_id=node.node_id,
                    )
                if node.kind == "llm" and (
                    execution.stop_before_llm or execution.llm_handler is None
                ):
                    completed.add(node.node_id)
                    execution.stopped_at_llm.append(node.node_id)
                    execution.boundary_values.extend(node_inputs.values())
                    self._skip_outgoing(outgoing[node.node_id], edge_values)
                    self._trace(execution, node, "BOUNDARY", input_map=node_inputs)
                    progress = True
                    continue
                try:
                    output_map, branch_value = self._execute_node(
                        execution, node, node_inputs
                    )
                except Exception as exc:
                    if isinstance(exc, WorkflowExecutionError):
                        raise
                    error_edges = [
                        index
                        for index in outgoing[node.node_id]
                        if workflow.edges[index].branch == "error"
                    ]
                    if node.config.get("failure_policy") == "error" and error_edges:
                        output_map = {
                            port.name: {
                                "type": type(exc).__name__,
                                "message": str(exc),
                            }
                            for port in node.outputs
                        }
                        branch_value = "error"
                        self._trace(execution, node, "ERROR_ROUTED", error=exc)
                    else:
                        self._trace(execution, node, "FAILED", error=exc)
                        raise WorkflowExecutionError(
                            f"{type(exc).__name__}: {exc}",
                            workflow_key=workflow.workflow_key,
                            node_id=node.node_id,
                        ) from exc

                completed.add(node.node_id)
                execution.executed.append(node.node_id)
                self._trace(
                    execution,
                    node,
                    "SUCCEEDED",
                    input_map=node_inputs,
                    output_map=output_map,
                )
                if node.kind == "end":
                    end_values.append(self._end_value(node_inputs, output_map))
                self._resolve_outgoing(
                    execution,
                    node,
                    output_map,
                    outgoing[node.node_id],
                    workflow,
                    edge_values,
                    branch_value=branch_value,
                )
                progress = True
            if not progress:
                break

        if end_values:
            return self._single_boundary_value(end_values, end_values[-1])
        unresolved = [
            node.node_id
            for node in workflow.nodes
            if node.node_id not in completed and node.kind != "start"
        ]
        if unresolved and not allow_incomplete:
            raise WorkflowExecutionError(
                "graph stalled before End; unresolved nodes: " + ", ".join(unresolved),
                workflow_key=workflow.workflow_key,
            )
        if not allow_incomplete:
            raise WorkflowExecutionError(
                "graph completed without producing an End value",
                workflow_key=workflow.workflow_key,
            )
        return None

    @staticmethod
    def _collect_inputs(
        node: WorkflowNode,
        indexes: list[int],
        workflow: WorkflowDefinition,
        edge_values: list[Any],
    ) -> dict[str, Any] | None:
        values: dict[str, list[Any]] = {}
        for index in indexes:
            value = edge_values[index]
            if value is _SKIPPED:
                continue
            edge = workflow.edges[index]
            values.setdefault(edge.target_port, []).append(value)
        result = {
            name: items[0] if len(items) == 1 else items
            for name, items in values.items()
        }
        if any(port.required and port.name not in result for port in node.inputs):
            return None
        # An optional port whose incoming edge was skipped is resolved with a
        # null value.  Keeping it in the input environment is important for
        # selector expressions: ``optional_value is None`` must not degrade
        # into an undefined-name error.
        for port in node.inputs:
            result.setdefault(port.name, None)
        return result

    def _execute_node(
        self,
        execution: _Execution,
        node: WorkflowNode,
        inputs: Mapping[str, Any],
    ) -> tuple[dict[str, Any], Any]:
        if node.kind == "start":
            if len(node.outputs) == 1:
                port = node.outputs[0].name
                value = inputs.get(port, inputs)
                return {port: value}, None
            return {
                port.name: inputs.get(port.name)
                for port in node.outputs
                if port.name in inputs or not port.required
            }, None
        if node.kind == "end":
            value = self._payload(inputs)
            return self._normalize_outputs(node, value), None
        if node.kind in {"code", "script"}:
            if node.script_mode == "inline":
                result = invoke_inline_workflow_function(
                    node.script_source or "", inputs, execution.runtime_context
                )
            elif node.operation in execution.function_sources:
                result = invoke_inline_workflow_function(
                    execution.function_sources[node.operation or ""],
                    inputs,
                    execution.runtime_context,
                )
            else:
                result = invoke_workflow_function(
                    node.operation or "", inputs, execution.runtime_context
                )
            return self._normalize_outputs(node, result), None
        if node.kind == "llm":
            result = execution.llm_handler(node, inputs, execution.runtime_context)
            return self._normalize_outputs(node, result), None
        if node.kind in {"selector", "if_else", "switch", "loop"}:
            selector_mode = node.config.get("selector_mode")
            if node.kind == "switch":
                selector_mode = "case"
            elif node.kind in {"if_else", "loop"}:
                selector_mode = "boolean"
            elif selector_mode is None:
                selector_mode = "boolean" if len(node.outputs) <= 2 else "case"
            evaluated = evaluate_workflow_expression(
                node.expression or "False", inputs, execution.runtime_context
            )
            if selector_mode == "case":
                raw = evaluated._value if isinstance(evaluated, _AttrValue) else evaluated
                selected = self._control_output(node, inputs, case_value=str(raw))
                return selected, str(raw)
            result = bool(
                evaluated
            )
            selected = self._control_output(node, inputs, truthy=result)
            return selected, result
        if node.kind == "parallel":
            payload = self._payload(inputs)
            return {port.name: payload for port in node.outputs}, None
        if node.kind == "read_state":
            value = self._read_state(execution.state, node.state_path or "")
            return self._normalize_outputs(node, value), None
        if node.kind in {"variable_assigner", "write_state"}:
            value = self._payload(inputs)
            self._write_state(execution.state, node.state_path or "", value)
            return self._normalize_outputs(node, value), None
        if node.kind == "variable_aggregator":
            groups = node.config.get("groups")
            outputs: dict[str, Any] = {}
            if isinstance(groups, Mapping):
                for output_name, input_names in groups.items():
                    names = input_names if isinstance(input_names, list) else [input_names]
                    outputs[str(output_name)] = next(
                        (inputs[name] for name in names if name in inputs and inputs[name] is not None),
                        None,
                    )
            else:
                first_value = next(
                    (value for value in inputs.values() if value is not None), None
                )
                for port in node.outputs:
                    outputs[port.name] = inputs.get(port.name, first_value)
            return self._normalize_outputs(node, outputs), None
        if node.kind == "subflow":
            subflow_key = node.subflow_key or ""
            result = self.execute(
                subflow_key,
                inputs,
                llm_handler=execution.llm_handler,  # type: ignore[arg-type]
                runtime_context=execution.runtime_context,
                state=execution.state,
                invocation_id=execution.invocation_id,
            )
            return self._normalize_outputs(node, result.value), None
        raise ValueError(f"unsupported workflow node kind: {node.kind}")

    @staticmethod
    def _normalize_outputs(node: WorkflowNode, value: Any) -> dict[str, Any]:
        port_names = [port.name for port in node.outputs]
        if not port_names:
            return {}
        if isinstance(value, Mapping):
            matching = {name: value[name] for name in port_names if name in value}
            if matching:
                return matching
        if len(port_names) == 1:
            return {port_names[0]: value}
        if isinstance(value, (list, tuple)) and len(value) == len(port_names):
            return dict(zip(port_names, value, strict=True))
        raise ValueError(
            f"node {node.node_id} must return outputs: {', '.join(port_names)}"
        )

    @staticmethod
    def _control_output(
        node: WorkflowNode,
        inputs: Mapping[str, Any],
        *,
        truthy: bool | None = None,
        case_value: str | None = None,
    ) -> dict[str, Any]:
        payload = inputs.get("context", WorkflowExecutor._payload(inputs))
        if truthy is not None:
            candidates = [
                port.name
                for index, port in enumerate(node.outputs)
                if (truthy and index == 0) or (not truthy and index == 1)
            ]
        else:
            candidates = [port.name for port in node.outputs if port.name == case_value]
        return {name: payload for name in candidates}

    @staticmethod
    def _payload(inputs: Mapping[str, Any]) -> Any:
        if "input" in inputs:
            return inputs["input"]
        if "result" in inputs:
            return inputs["result"]
        resolved = {name: value for name, value in inputs.items() if value is not None}
        if len(resolved) == 1:
            return next(iter(resolved.values()))
        return resolved

    @staticmethod
    def _end_value(inputs: Mapping[str, Any], outputs: Mapping[str, Any]) -> Any:
        if "flow_result" in outputs:
            return outputs["flow_result"]
        if "result" in inputs:
            return inputs["result"]
        return WorkflowExecutor._payload(inputs)

    @staticmethod
    def _edge_is_active(edge, branch_value: Any) -> bool:
        if branch_value == "error":
            return edge.branch == "error"
        if edge.branch == "error":
            return False
        if edge.branch == "always":
            return True
        if edge.branch == "true":
            return branch_value is True
        if edge.branch == "false":
            return branch_value is False
        if edge.branch == "case":
            return str(branch_value) == str(edge.case_value)
        return False

    def _resolve_outgoing(
        self,
        execution: _Execution,
        node: WorkflowNode,
        outputs: Mapping[str, Any],
        indexes: list[int],
        workflow: WorkflowDefinition,
        edge_values: list[Any],
        *,
        branch_value: Any,
    ) -> None:
        for index in indexes:
            edge = workflow.edges[index]
            active = self._edge_is_active(edge, branch_value)
            if active and edge.source_port in outputs:
                edge_values[index] = outputs[edge.source_port]
            else:
                edge_values[index] = _SKIPPED

    @staticmethod
    def _skip_outgoing(indexes: list[int], edge_values: list[Any]) -> None:
        for index in indexes:
            if edge_values[index] is _UNRESOLVED:
                edge_values[index] = _SKIPPED

    @staticmethod
    def _read_state(state: Mapping[str, Any], path: str) -> Any:
        current: Any = state
        for part in (item for item in path.split(".") if item):
            if not isinstance(current, Mapping) or part not in current:
                return None
            current = current[part]
        return current

    @staticmethod
    def _write_state(state: MutableMapping[str, Any], path: str, value: Any) -> None:
        parts = [item for item in path.split(".") if item]
        if not parts:
            raise ValueError("write_state requires a non-empty state path")
        current = state
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, MutableMapping):
                child = {}
                current[part] = child
            current = child
        current[parts[-1]] = value

    @staticmethod
    def _single_boundary_value(values: list[Any], fallback: Any) -> Any:
        if not values:
            return fallback
        first = values[0]
        try:
            if all(item == first for item in values[1:]):
                return first
        except Exception:
            pass
        return fallback

    @staticmethod
    def _trace(
        execution: _Execution,
        node: WorkflowNode,
        status: str,
        *,
        input_map: Mapping[str, Any] | None = None,
        output_map: Mapping[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        if execution.trace_handler is None:
            return
        event: dict[str, Any] = {
            "invocation_id": execution.invocation_id,
            "workflow_key": execution.workflow.workflow_key,
            "node_id": node.node_id,
            "node_kind": node.kind,
            "prompt_key": node.prompt_key,
            "status": status,
            "input_ports": sorted((input_map or {}).keys()),
            "output_ports": sorted((output_map or {}).keys()),
            "agent_key": execution.runtime_context.get("agent_key"),
            "virtual_time": execution.runtime_context.get("virtual_time"),
        }
        if error is not None:
            event["error_type"] = type(error).__name__
            event["error_message"] = str(error)[:1_000]
        execution.trace_handler(event)


__all__ = [
    "LLMNodeHandler",
    "WorkflowExecutionError",
    "WorkflowExecutionResult",
    "WorkflowExecutor",
    "evaluate_workflow_expression",
]
