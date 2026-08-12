"""Shared and inline deterministic functions for Agent workflows."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import inspect
import textwrap
from typing import Any, Callable, Mapping


WorkflowFunction = Callable[[Mapping[str, Any], Any], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class WorkflowFunctionSpec:
    key: str
    title: str
    description: str
    implementation: str
    source: str
    input_type: str
    output_type: str
    function: WorkflowFunction

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("function")
        value["available"] = callable(self.function)
        value["scope"] = "system"
        value["editable"] = False
        return value


def _value(inputs: Mapping[str, Any]) -> Any:
    if "input" in inputs:
        return inputs["input"]
    if len(inputs) == 1:
        return next(iter(inputs.values()))
    return dict(inputs)


def identity(inputs: Mapping[str, Any], _context: Any) -> Mapping[str, Any]:
    return {"result": _value(inputs)}


def merge_context(inputs: Mapping[str, Any], _context: Any) -> Mapping[str, Any]:
    merged: dict[str, Any] = {}
    for value in inputs.values():
        if isinstance(value, Mapping):
            merged.update(value)
    return {"context": merged}


def select_fields(inputs: Mapping[str, Any], context: Any) -> Mapping[str, Any]:
    value = _value(inputs)
    fields = getattr(context, "fields", None)
    if fields is None and isinstance(context, Mapping):
        fields = context.get("fields")
    if not isinstance(value, Mapping) or not fields:
        return {"result": value}
    return {"result": {key: value[key] for key in fields if key in value}}


def normalize_list(inputs: Mapping[str, Any], _context: Any) -> Mapping[str, Any]:
    value = _value(inputs)
    if value is None:
        result: list[Any] = []
    elif isinstance(value, list):
        result = value
    elif isinstance(value, (tuple, set)):
        result = list(value)
    else:
        result = [value]
    return {"result": result}


def _read_context(context: Any, name: str, default: Any = None) -> Any:
    if isinstance(context, Mapping):
        return context.get(name, default)
    return getattr(context, name, default)


def _prepare_context(flow_key: str, inputs: Mapping[str, Any], context: Any) -> Mapping[str, Any]:
    step_context = inputs.get("step_context")
    if step_context is None:
        step_context = context
    result = {
        "flow_key": flow_key,
        "step_context": step_context,
        "agent": _read_context(step_context, "agent"),
        "clock": _read_context(step_context, "clock"),
        "memories": _read_context(step_context, "memories", []),
        "visible_events": _read_context(step_context, "visible_events", []),
        "trigger": _read_context(step_context, "trigger", "step"),
        "plan": _read_context(step_context, "plan", {}),
        "decompose_threshold": _read_context(
            step_context, "decompose_threshold", 60
        ),
        "prompt_key": _read_context(step_context, "prompt_key"),
        "prompt_request": _read_context(step_context, "prompt_request"),
    }
    return {"context": result}


def schedule_prepare_context(inputs: Mapping[str, Any], context: Any) -> Mapping[str, Any]:
    return _prepare_context("schedule", inputs, context)


def memory_prepare_context(inputs: Mapping[str, Any], context: Any) -> Mapping[str, Any]:
    return _prepare_context("memory", inputs, context)


def action_prepare_context(inputs: Mapping[str, Any], context: Any) -> Mapping[str, Any]:
    return _prepare_context("action", inputs, context)


def social_prepare_context(inputs: Mapping[str, Any], context: Any) -> Mapping[str, Any]:
    return _prepare_context("social", inputs, context)


def reflection_prepare_context(inputs: Mapping[str, Any], context: Any) -> Mapping[str, Any]:
    return _prepare_context("reflection", inputs, context)


def _spec(
    key: str,
    title: str,
    description: str,
    input_type: str,
    output_type: str,
    function: WorkflowFunction,
    *,
    dependencies: tuple[Callable[..., Any], ...] = (),
) -> WorkflowFunctionSpec:
    source_parts = [textwrap.dedent(inspect.getsource(item)).strip() for item in (*dependencies, function)]
    return WorkflowFunctionSpec(
        key=key,
        title=title,
        description=description,
        implementation=f"{__name__}:{function.__name__}",
        source="\n\n".join(source_parts),
        input_type=input_type,
        output_type=output_type,
        function=function,
    )


WORKFLOW_FUNCTIONS: dict[str, WorkflowFunctionSpec] = {
    item.key: item
    for item in (
        _spec("identity", "原样传递", "将一个输入原样传给下游。", "any", "any", identity, dependencies=(_value,)),
        _spec("merge_context", "合并上下文", "按输入顺序合并对象字段。", "object", "object", merge_context),
        _spec("select_fields", "选择字段", "按节点配置选择对象字段。", "object", "object", select_fields, dependencies=(_value,)),
        _spec("normalize_list", "标准化列表", "把空值、单值或集合统一转换为列表。", "any", "array", normalize_list, dependencies=(_value,)),
        _spec("schedule_prepare_context", "准备日程上下文", "从系统注入的 StepContext 提取 Agent、时钟、记忆与日程触发原因。", "StepContext", "ScheduleContext", schedule_prepare_context, dependencies=(_read_context, _prepare_context)),
        _spec("memory_prepare_context", "准备记忆上下文", "提取本轮可见事件与 Agent 记忆状态。", "StepContext", "MemoryContext", memory_prepare_context, dependencies=(_read_context, _prepare_context)),
        _spec("action_prepare_context", "准备行动上下文", "提取当前计划、位置和可用空间。", "StepContext", "ActionContext", action_prepare_context, dependencies=(_read_context, _prepare_context)),
        _spec("social_prepare_context", "准备社交上下文", "提取附近 Agent、关系与对话状态。", "StepContext", "SocialContext", social_prepare_context, dependencies=(_read_context, _prepare_context)),
        _spec("reflection_prepare_context", "准备反思上下文", "提取高重要度记忆、对话与反思阈值。", "StepContext", "ReflectionContext", reflection_prepare_context, dependencies=(_read_context, _prepare_context)),
    )
}


def get_workflow_function(key: str) -> WorkflowFunctionSpec | None:
    return WORKFLOW_FUNCTIONS.get(key)


def list_workflow_functions() -> list[dict[str, Any]]:
    return [WORKFLOW_FUNCTIONS[key].public_dict() for key in sorted(WORKFLOW_FUNCTIONS)]


def invoke_workflow_function(
    key: str, inputs: Mapping[str, Any], context: Any = None
) -> Mapping[str, Any]:
    spec = get_workflow_function(key)
    if spec is None:
        raise KeyError(f"workflow function is not registered: {key}")
    return spec.function(inputs, context)


INLINE_FUNCTION_TEMPLATE = """def main(inputs, context):
    value = inputs.get(\"input\")
    return {\"result\": value}
"""

_INLINE_ALLOWED_BUILTINS: dict[str, Callable[..., Any] | type] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}
_INLINE_ALLOWED_METHODS = frozenset(
    {
        "append",
        "copy",
        "endswith",
        "extend",
        "get",
        "items",
        "join",
        "keys",
        "lower",
        "replace",
        "setdefault",
        "split",
        "startswith",
        "strip",
        "upper",
        "values",
    }
)
_INLINE_FORBIDDEN_NODES = (
    ast.AsyncFunctionDef,
    ast.Await,
    ast.ClassDef,
    ast.Delete,
    ast.Global,
    ast.Import,
    ast.ImportFrom,
    ast.Lambda,
    ast.Nonlocal,
    ast.Raise,
    ast.Try,
    ast.While,
    ast.With,
    ast.Yield,
    ast.YieldFrom,
)


def validate_inline_workflow_function(source: str) -> None:
    """Validate the deliberately small Python subset accepted by Script nodes."""

    if not isinstance(source, str) or not source.strip():
        raise ValueError("内联 Function 源码不能为空")
    if len(source) > 12_000:
        raise ValueError("内联 Function 源码不能超过 12000 个字符")
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"Python 语法错误（第 {exc.lineno or 1} 行）：{exc.msg}") from exc
    if len(list(ast.walk(tree))) > 400:
        raise ValueError("内联 Function 过于复杂，请拆分逻辑或改为公共 Function")
    functions = [item for item in tree.body if isinstance(item, ast.FunctionDef)]
    if len(tree.body) != 1 or len(functions) != 1 or functions[0].name != "main":
        raise ValueError("源码必须只定义一个 main(inputs, context) Function")
    function = functions[0]
    arguments = function.args
    if (
        [item.arg for item in arguments.args] != ["inputs", "context"]
        or arguments.vararg
        or arguments.kwarg
        or arguments.kwonlyargs
        or arguments.defaults
        or function.decorator_list
    ):
        raise ValueError("入口签名必须是 def main(inputs, context)，且不能使用装饰器或可变参数")
    for item in ast.walk(tree):
        if isinstance(item, _INLINE_FORBIDDEN_NODES):
            raise ValueError(f"内联 Function 不允许使用 {type(item).__name__}")
        if isinstance(item, ast.Name) and item.id.startswith("_"):
            raise ValueError("内联 Function 不允许访问以下划线开头的名称")
        if isinstance(item, ast.Attribute):
            if item.attr.startswith("_") or item.attr not in _INLINE_ALLOWED_METHODS:
                raise ValueError(f"内联 Function 不允许调用属性或方法 {item.attr}")
        if isinstance(item, ast.Call):
            if isinstance(item.func, ast.Name) and item.func.id not in _INLINE_ALLOWED_BUILTINS:
                raise ValueError(f"内联 Function 不允许调用 {item.func.id}")
            if not isinstance(item.func, (ast.Name, ast.Attribute)):
                raise ValueError("内联 Function 只允许调用白名单函数和常用容器方法")


def invoke_inline_workflow_function(
    source: str, inputs: Mapping[str, Any], context: Any = None
) -> Mapping[str, Any]:
    """Execute a validated inline Function with no imports or ambient builtins."""

    validate_inline_workflow_function(source)
    namespace: dict[str, Any] = {}
    exec(  # noqa: S102 - source passed the strict AST allowlist above.
        compile(source, "<workflow-inline-function>", "exec"),
        {"__builtins__": dict(_INLINE_ALLOWED_BUILTINS)},
        namespace,
    )
    result = namespace["main"](dict(inputs), context)
    if not isinstance(result, Mapping):
        raise ValueError("内联 Function 必须返回一个对象，例如 {\"result\": value}")
    return dict(result)
