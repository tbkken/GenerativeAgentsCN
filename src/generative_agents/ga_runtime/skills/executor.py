"""通过 OpenAI 兼容聊天端点执行自然语言 Skill。

Runtime 负责在模型调用、私有脚本、子 Skill 和公共接口之间循环，直到得到最终文本；
所有中间动作都会写入 trace，便于运行结果解释和故障排查。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Protocol
from uuid import uuid4

from generative_agents.ga_protocol.skills.documents import SkillDocument
from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_protocol.skills.decisions import validate_compiled_decision
from generative_agents.ga_protocol.skills.metadata import SkillRegistryError
from generative_agents.ga_protocol.skills.dependencies import referenced_interfaces
from generative_agents.ga_protocol.skills.dependencies import validate_interface_references
from generative_agents.ga_runtime.models.retry import ModelCallCancelled, ModelResponseFormatError, check_cancelled
from generative_agents.ga_runtime.storage.execution import execution_span

class SkillInterfaces(Protocol):
    """Identity-bound capabilities supplied by the Runtime iteration."""

    def descriptors(self) -> list[dict[str, Any]]: ...

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class SkillRuntimeError(RuntimeError):
    """A model, script, or child Skill could not complete."""


class RecoverableSkillRuntimeError(SkillRuntimeError):
    """A transient model failure or no-progress loop can degrade one step."""

    def __init__(self, message: str, *, trace=()) -> None:
        super().__init__(message)
        self.trace = tuple(trace)


class SkillModelError(RecoverableSkillRuntimeError):
    """The configured model gateway exhausted its retry policy."""


class SkillOutputFormatError(SkillModelError):
    """A model response remained invalid after bounded format correction."""


class SkillLoopError(RecoverableSkillRuntimeError):
    """The Brain repeated a stable tool call without making progress."""


class SkillBudgetExceeded(RecoverableSkillRuntimeError):
    """An independent execution budget stopped the next operation safely."""

    def __init__(self, budget_kind: str, *, limit: int, used: int, skill: str,
                 attempted: int | None = None, trace=()) -> None:
        self.budget_kind = budget_kind
        self.limit = limit
        self.used = used
        self.attempted = used + 1 if attempted is None else attempted
        self.skill = skill
        super().__init__(
            f"Skill {skill} exhausted {budget_kind} budget: used {used}, "
            f"limit {limit}, next operation {self.attempted}", trace=trace,
        )


@dataclass(frozen=True, slots=True)
class SkillRunResult:
    """一次 Skill 执行的最终文本和完整步骤轨迹。"""

    skill: str
    output_text: str
    trace: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        """执行 `SkillRunResult` 的`as``dict`操作。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        return {
            "skill": self.skill,
            "output_text": self.output_text,
            "trace": list(self.trace),
        }


class SkillRuntime:
    """Execute Skills and brains with one string between calls."""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        interfaces: SkillInterfaces | None = None,
        timeout: float = 300,
        max_call_depth: int = 8,
        max_skill_rounds: int = 32,
        max_tool_calls: int = 128,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        enable_thinking: bool = False,
        provider: str = "vllm",
        retry_attempts: int = 1,
        retry_backoff_seconds: float = 0,
        model_client=None,
        decision_config: Mapping[str, Any] | None = None,
        decision_client=None,
        recorder=None,
        control=None,
        logger=None,
        sleep=None,
        agent_key: str | None = None,
        step_no: int | None = None,
        max_identical_tool_calls: int = 2,
    ) -> None:
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            registry: 按稳定键解析技能、模型或其他组件的注册表。 类型：`SkillRegistry`。
            base_url: `base`的访问或连接地址。 类型：`str | None`。 默认值：`None`。
            model: 当前调用、筛选或序列化的模型配置或模型实例。 类型：`str | None`。 默认值：`None`。
            api_key: 调用模型服务使用的 API 密钥；为空时由密钥解析器按配置加载。 类型：`str | None`。 默认值：`None`。
            interfaces: 技能调用使用的身份绑定接口。 类型：`SkillInterfaces | None`。 默认值：`None`。
            timeout: 等待操作的最长秒数；超时后按调用协议返回或抛出异常。 类型：`float`。 默认值：`300`。
            max_call_depth: 最大子 Skill 调用深度，根 Skill 为 0。默认值：`8`。
            max_skill_rounds: 每次 Skill 执行独立的逻辑模型轮数上限。默认值：`32`。
            max_tool_calls: 整轮调用树已开始的工具调用次数上限。默认值：`128`。

        返回:
            无返回值。
        """
        self.registry = registry
        self.base_url = (
            base_url
            or os.getenv("GA_SKILL_LLM_BASE_URL")
            or "http://127.0.0.1:8888/v1"
        ).rstrip("/")
        self.model = model or os.getenv("GA_SKILL_LLM_MODEL") or "Qwen3.8-27B-UD-Q4_K_XL"
        self.api_key = (
            api_key if api_key is not None else os.getenv("GA_SKILL_LLM_API_KEY", "")
        )
        self.interfaces = interfaces
        self.timeout = timeout
        for name, value in (
            ('max_call_depth', max_call_depth), ('max_skill_rounds', max_skill_rounds),
            ('max_tool_calls', max_tool_calls),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        self.max_call_depth = max_call_depth
        self.max_skill_rounds = max_skill_rounds
        self.max_tool_calls = max_tool_calls
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.enable_thinking = bool(enable_thinking)
        self.provider = str(provider or "vllm")
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_backoff_seconds = max(0, float(retry_backoff_seconds))
        self._model_client = model_client
        self.decision_config = dict(decision_config or {})
        self._decision_client = decision_client
        self._recorder = recorder
        self._execution = getattr(recorder, "execution", None)
        self._control = control
        self._logger = logger
        self._sleep = sleep
        self.agent_key = agent_key
        self.step_no = step_no
        self.max_identical_tool_calls = max(1, int(max_identical_tool_calls))
        self._active_skill_name: str | None = None
        self._response_validator = None
        self._total_tool_calls = 0
        self._tool_progress: dict[str, tuple[str, int]] = {}
        self._semantic_tool_progress: dict[str, tuple[str, int, int]] = {}
        self._state_epoch = 0

    def run(
        self,
        skill_name: str,
        input_text: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> SkillRunResult:
        """执行指定 Skill，并返回最终文本和可审计轨迹。

        参数:
            skill_name: 需要调用的技能名称，必须能在当前运行的技能快照中解析。 类型：`str`。
            input_text: 传给模型或技能处理的原始输入文本。 类型：`str`。
            context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`Mapping[str, Any] | None`。 默认值：`None`。

        返回:
            包含规范化 Skill 名称、输出文本和每次脚本/子 Skill/接口调用的结果。
        """
        trace: list[dict[str, Any]] = []
        self._total_tool_calls = 0
        self._tool_progress = {}
        self._semantic_tool_progress = {}
        self._state_epoch = 0
        try:
            output = self._run(
                self.registry.get(skill_name),
                str(input_text),
                dict(context or {}),
                trace,
                depth=0,
            )
        except RecoverableSkillRuntimeError as exc:
            if not exc.trace:
                exc.trace = tuple(trace)
            raise
        return SkillRunResult(
            skill=self.registry.normalize_name(skill_name),
            output_text=output,
            trace=tuple(trace),
        )

    def _run(
        self,
        document: SkillDocument,
        input_text: str,
        context: dict[str, Any],
        trace: list[dict[str, Any]],
        *,
        depth: int,
        tool_call_id: str | None = None,
    ) -> str:
        check_cancelled(self._control)
        if depth > self.max_call_depth:
            self._budget_exceeded('call_depth', self.max_call_depth, depth - 1,
                                  document.name, trace, attempted=depth)
        with execution_span(
            self._execution, kind="skill", name=document.display_name or document.name,
            skill_key=document.name, step_no=self.step_no, agent_key=self.agent_key,
            tool_call_id=tool_call_id,
        ):
            return self._execute(document, input_text, context, trace, depth=depth)

    def _execute(
        self,
        document: SkillDocument,
        input_text: str,
        context: dict[str, Any],
        trace: list[dict[str, Any]],
        *,
        depth: int,
    ) -> str:
        """递归执行一个已解析 Skill，直到得到最终自然语言文本。

        参数:
            document: 待校验、转换或持久化的结构化文档。 类型：`SkillDocument`。
            input_text: 传给模型或技能处理的原始输入文本。 类型：`str`。
            context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`dict[str, Any]`。
            trace: 由整棵调用树共享的追加式审计事件列表。
            depth: 当前子 Skill 深度，用于阻止循环依赖无限递归。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            SkillRuntimeError: 当底层操作报告该异常条件时抛出。
        """
        check_cancelled(self._control)
        validate_interface_references(document.body)
        script_path = document.path.parent / "scripts" / "main.py"
        if document.llm_type == "decision":
            if (document.kind == "brain" or document.children or referenced_interfaces(document.body)
                    or document.scripts or script_path.is_file()
                    or (depth == 0 and getattr(self.interfaces, "default_interfaces", False))):
                raise SkillRuntimeError("Decision Skills must be leaves without Brain/object roles, interfaces, child Skills, or main.py")
            return self._execute_decision(document, input_text, context, trace)
        # 对象根 Skill 始终由模型执行；可选脚本仍可用于它组合的子 Skill。
        if script_path.is_file() and not (depth == 0 and getattr(self.interfaces, "default_interfaces", False)):
            trace.append(
                {
                    "event": "skill.start",
                    "skill": document.name,
                    "input_text": input_text,
                }
            )
            with execution_span(self._execution, kind="script", name="main.py"):
                output = self._run_script(script_path, input_text, context)
            check_cancelled(self._control)
            trace.append(
                {
                    "event": "script.result",
                    "skill": document.name,
                    "output_text": output,
                }
            )
            return output
        user_prompt = self._input_message(input_text, context)
        children = [self.registry.get(name) for name in document.children]
        declared_interfaces = set(referenced_interfaces(document.body))
        interface_descriptors = (
            [
                tool
                for tool in self.interfaces.descriptors()
                if (getattr(self.interfaces, "default_interfaces", False)
                    or str(tool.get("name") or "") in declared_interfaces)
                # Only the root Brain or object Skill owns the iteration action.
                # Child Skills return advice without committing world changes.
                and not (
                    str(tool.get("name") or "") == "world-act" and depth != 0
                )
            ]
            if self.interfaces
            else []
        )
        script_handlers = (
            {} if depth == 0 and getattr(self.interfaces, "default_interfaces", False)
            else self._script_handlers(document)
        )
        # 没有任何可调用依赖的叶子 Skill，只需要一次普通聊天完成。
        if not children and not interface_descriptors and not script_handlers:
            system_prompt = document.markdown
            trace.append(
                {
                    "event": "skill.start",
                    "skill": document.name,
                    "input_text": input_text,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                }
            )
            self._active_skill_name = document.name
            output = self._validated_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ], tools=[], skill=document.name, allowed={}, scripts={},
                completed={}, seen_ids=set(), trace=trace,
            )["content"]
            trace.append(
                {"event": "skill.result", "skill": document.name, "output_text": output}
            )
            return output

        system_prompt = (
            document.markdown
            + "\n\nYou may call one listed child Skill or runtime interface at a time. "
            "Pass child Skills all useful context as plain natural language with input_text, or "
            "set input_from to the tool_call_id of one of your own previously completed child "
            "Skill calls to pass its exact result without rewriting it. Supply exactly one of "
            "input_text and input_from; result references are local to this caller execution, "
            "not Skill names. Every tool call must have a unique non-empty tool_call_id. Treat every tool result "
            "as natural-language context. When the task is complete, answer directly without a tool call."
        )
        trace.append(
            {
                "event": "skill.start",
                "skill": document.name,
                "input_text": input_text,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        allowed = {child.name: child for child in children}
        tools = []
        if allowed:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "call_skill",
                        "description": (
                            "Call one child Skill and receive its natural-language result. "
                            "Supply its exact name and exactly one of input_text or input_from."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "enum": sorted(allowed)},
                                "input_text": {
                                    "type": "string",
                                    "description": "Complete natural-language context for the child Skill.",
                                },
                                "input_from": {
                                    "type": "string",
                                    "minLength": 1,
                                    "description": "The tool_call_id of your own previously completed child Skill call; pass its exact result text.",
                                },
                            },
                            "required": ["name"],
                            # Keep a plain object root for provider tool grammars.
                            # The full input union is enforced before execution.
                            "additionalProperties": False,
                        },
                    },
                }
            )
        tools.extend(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"] + (
                        " Supply exactly one of target_coord or target_address."
                        if tool["name"] == "world-navigate" else ""
                    ),
                    # Some tool grammars discard root properties when oneOf is
                    # present. Project only navigation's root union; Protocol and
                    # the identity-bound interface keep the full contract.
                    "parameters": {
                        key: value for key, value in tool["parameters"].items()
                        if not (tool["name"] == "world-navigate" and key == "oneOf")
                    },
                },
            }
            for tool in interface_descriptors
        )
        if script_handlers:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "run_skill_script",
                        "description": (
                            "Run one private deterministic function shipped inside this Skill. "
                            "Pass its useful input as natural language; runtime context is injected automatically."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "function": {
                                    "type": "string",
                                    "enum": sorted(script_handlers),
                                },
                                "input_text": {
                                    "type": "string",
                                    "description": "Natural-language input for the private function.",
                                },
                            },
                            "required": ["function", "input_text"],
                            "additionalProperties": False,
                        },
                    },
                }
            )
        # Results belong to this caller invocation, never to the shared trace or runtime.
        completed_child_results: dict[str, str] = {}
        seen_tool_call_ids: set[str] = set()
        # 组合 Skill 采用受限工具循环；每轮模型只能调用当前文档显式允许的能力。
        for _ in range(self.max_skill_rounds):
            self._active_skill_name = document.name
            response = self._validated_completion(
                messages, tools=tools, skill=document.name, allowed=allowed,
                scripts=script_handlers, completed=completed_child_results,
                seen_ids=seen_tool_call_ids, trace=trace,
            )
            tool_calls = response.get("tool_calls") or []
            if not tool_calls:
                output = str(response.get("content") or "")
                if not output.strip():
                    raise SkillRuntimeError(f"Skill {document.name} returned no text")
                trace.append(
                    {
                        "event": "skill.result",
                        "skill": document.name,
                        "output_text": output,
                    }
                )
                return output
            seen_tool_call_ids.update(call['id'] for call in tool_calls)
            assistant_message = {
                "role": "assistant",
                "content": response.get("content") or "",
                "tool_calls": tool_calls,
            }
            messages.append(assistant_message)
            for call in tool_calls:
                check_cancelled(self._control)
                function = call.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        raise TypeError("tool arguments must be a JSON object")
                except (json.JSONDecodeError, TypeError) as exc:
                    raise SkillOutputFormatError(
                        "Model returned invalid tool arguments"
                    ) from exc
                tool_name = str(function.get("name") or "")
                fingerprint_arguments = arguments
                if tool_name == "call_skill":
                    try:
                        child_input = self._child_input(arguments, completed_child_results)
                    except SkillModelError as exc:
                        self._reject_tool_call(document.name, call, str(exc), trace)
                    fingerprint_arguments = {"name": arguments.get("name"), "input_text": child_input}
                    if depth >= self.max_call_depth:
                        self._budget_exceeded('call_depth', self.max_call_depth, depth,
                            arguments['name'], trace, attempted=depth + 1, tool=tool_name)
                if self._total_tool_calls >= self.max_tool_calls:
                    self._budget_exceeded('tool_calls', self.max_tool_calls, self._total_tool_calls,
                                          document.name, trace, tool=tool_name)
                request_fingerprint = self._tool_request_fingerprint(
                    document.name, tool_name, fingerprint_arguments
                )
                previous = self._tool_progress.get(request_fingerprint)
                if previous is not None and previous[1] >= self.max_identical_tool_calls:
                    trace.append(
                        {
                            "event": "loop.detected",
                            "skill": document.name,
                            "tool": tool_name,
                            "arguments": arguments,
                            "repeat_count": previous[1],
                        }
                    )
                    raise SkillLoopError(
                        f"Skill {document.name} repeated {tool_name} without progress"
                    )
                semantic_fingerprint = self._semantic_tool_fingerprint(
                    document.name, tool_name, fingerprint_arguments
                )
                semantic_previous = self._semantic_tool_progress.get(
                    semantic_fingerprint
                )
                if (
                    semantic_previous is not None
                    and semantic_previous[1] >= self.max_identical_tool_calls
                    and semantic_previous[2] == self._state_epoch
                ):
                    trace.append(
                        {
                            "event": "loop.detected",
                            "skill": document.name,
                            "tool": tool_name,
                            "arguments": arguments,
                            "repeat_count": semantic_previous[1],
                            "semantic_scope": semantic_fingerprint,
                        }
                    )
                    raise SkillLoopError(
                        f"Skill {document.name} repeated {tool_name} without semantic progress"
                    )
                self._total_tool_calls += 1
                tool_result_is_error = False
                state_changed = False
                if tool_name == "call_skill":
                    # 子 Skill 获得自然语言上下文，但仍共享本次运行的审计轨迹。
                    child_name = arguments.get("name")
                    child = allowed.get(child_name)
                    if child is None:
                        raise SkillRuntimeError(
                            f"Skill {document.name} attempted unavailable child {child_name}"
                        )
                    trace.append(
                        {
                            "event": "skill.call",
                            "skill": document.name,
                            "child": child.name,
                            "tool_call_id": call["id"],
                            "input_text": child_input,
                            **({"input_from": arguments["input_from"]} if "input_from" in arguments else {}),
                        }
                    )
                    tool_output = self._run(
                        child, child_input, context, trace, depth=depth + 1,
                        tool_call_id=call["id"],
                    )
                    completed_child_results[call["id"]] = tool_output
                elif tool_name == "run_skill_script":
                    # 私有函数来自当前 Skill 目录，不能按模型给出的任意路径加载代码。
                    handler_name = str(arguments.get("function") or "")
                    handler = script_handlers.get(handler_name)
                    if handler is None:
                        raise SkillRuntimeError(
                            f"Skill {document.name} attempted unavailable script function {handler_name}"
                        )
                    script_input = str(arguments.get("input_text") or input_text)
                    with execution_span(self._execution, kind="script", name=handler_name,
                                        tool_call_id=call.get("id")):
                        tool_output = handler(script_input, context)
                        if not isinstance(tool_output, str):
                            raise SkillRuntimeError(
                                f"Skill script {handler_name} returned {type(tool_output).__name__}; expected str"
                            )
                    trace.append(
                        {
                            "event": "script.call",
                            "skill": document.name,
                            "function": handler_name,
                            "input_text": script_input,
                            "output_text": tool_output,
                        }
                    )
                elif self.interfaces and any(tool["name"] == tool_name for tool in interface_descriptors):
                    # 公共接口也必须出现在当前 Skill 声明的允许列表中。
                    with execution_span(self._execution, kind="interface", name=tool_name,
                                        tool_call_id=call.get("id")) as invocation:
                        result = self.interfaces.invoke(tool_name, arguments)
                        if result.get("ok") is not True:
                            invocation.status = "FAILED"
                            invocation.error_code = "InterfaceRejected"
                    is_error = result.get("ok") is not True
                    tool_result_is_error = is_error
                    state_changed = tool_name in {
                        "world-act",
                        "memory-stream-append",
                        "memory-stream-supersede",
                        "memory-stream-invalidate",
                    } and not is_error
                    tool_output = json.dumps(
                        {"ok": False, "error": str(result.get("error") or "Interface call failed")}
                        if is_error else result.get("result"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    )
                    trace.append(
                        {
                            "event": "interface.call",
                            "skill": document.name,
                            "tool": tool_name,
                            "input_text": json.dumps(arguments, ensure_ascii=False),
                            "output_text": tool_output,
                            "is_error": is_error,
                        }
                    )
                else:
                    raise SkillRuntimeError(
                        f"Skill {document.name} attempted unavailable tool {tool_name}"
                    )
                output_fingerprint = hashlib.sha256(
                    str(tool_output).encode("utf-8")
                ).hexdigest()
                if previous is not None and previous[0] == output_fingerprint:
                    repeat_count = previous[1] + 1
                else:
                    repeat_count = 1
                self._tool_progress[request_fingerprint] = (
                    output_fingerprint,
                    repeat_count,
                )
                if state_changed:
                    # A committed mutation is genuine progress.  Repeating a
                    # capability after it must be judged against the new state,
                    # not against pre-mutation outputs.
                    self._state_epoch += 1
                    self._tool_progress.clear()
                    self._semantic_tool_progress.clear()
                    semantic_previous = None
                if (
                    semantic_previous is not None
                    and semantic_previous[0] == output_fingerprint
                    and semantic_previous[2] == self._state_epoch
                ):
                    semantic_repeat_count = semantic_previous[1] + 1
                else:
                    semantic_repeat_count = 1
                self._semantic_tool_progress[semantic_fingerprint] = (
                    output_fingerprint,
                    semantic_repeat_count,
                    self._state_epoch,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": tool_name,
                        "content": tool_output,
                    }
                )
                if (
                    tool_name == "world-act"
                    and self.interfaces is not None
                    and not tool_result_is_error
                ):
                    # world-act is the terminal mutation choice for an Agent
                    # iteration.  Returning immediately prevents a later model
                    # turn from replacing it or spending the remaining budget
                    # after the decision is already complete.
                    output = "world-act accepted; this Agent iteration is complete."
                    trace.append(
                        {
                            "event": "skill.result",
                            "skill": document.name,
                            "output_text": output,
                        }
                    )
                    return output
        check_cancelled(self._control)
        self._budget_exceeded('skill_rounds', self.max_skill_rounds, self.max_skill_rounds,
                              document.name, trace)

    @staticmethod
    def _budget_exceeded(kind, limit, used, skill, trace, *, attempted=None, tool=None):
        error = SkillBudgetExceeded(kind, limit=limit, used=used, skill=skill, attempted=attempted)
        trace.append({'event': 'budget.exhausted', 'budget_kind': kind, 'skill': skill,
                      'limit': limit, 'used': used, 'attempted': error.attempted,
                      **({'tool': tool} if tool is not None else {})})
        raise error

    def _execute_decision(self, document, input_text, context, trace):
        """Return typed decisions as the child's result without any chat call."""
        try:
            validate_compiled_decision(document)
        except SkillRegistryError as exc:
            raise SkillRuntimeError(str(exc)) from exc
        if self._decision_client is None and not self.decision_config:
            raise SkillRuntimeError(f"Decision model is not configured for Skill {document.name}")
        state = {**context, "input_text": input_text} if context else input_text
        trace.append({"event": "skill.start", "skill": document.name, "llm_type": "decision",
                      "input_text": input_text, "decision_request": {
                          "model": self.decision_config.get("model"), "state": state,
                          "questions": document.decision_questions,
                      }})
        check_cancelled(self._control)
        try:
            if self._decision_client is None:
                from generative_agents.ga_runtime.models.decision import create_decision_model
                self._decision_client = create_decision_model(
                    self.decision_config, recorder=self._recorder, control=self._control,
                    logger=self._logger, sleep=self._sleep,
                )
            answers = self._decision_client.decision_completion(
                state, document.decision_questions, agent_key=self.agent_key,
                step_no=self.step_no, prompt_key=document.name,
                retry=int(self.decision_config.get("retry_attempts") or 1),
            )
        except ModelCallCancelled:
            raise
        except Exception as exc:
            raise SkillModelError(f"Decision model call failed after retries: {exc}") from exc
        check_cancelled(self._control)
        output = json.dumps(answers, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        trace.append({"event": "skill.result", "skill": document.name, "llm_type": "decision",
                      "output_text": output})
        return output

    def _complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """执行`complete`的内部处理，供当前模块或类复用。

        参数:
            messages: 按会话顺序排列的消息集合。 类型：`list[dict[str, Any]]`。
            tools: 传入当前算法的`tools`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`list[dict[str, Any]] | None`。 默认值：`None`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            SkillRuntimeError: 当底层操作报告该异常条件时抛出。
        """
        check_cancelled(self._control)
        def validate_response(response):
            if self._response_validator is not None:
                try:
                    self._response_validator(response)
                except SkillOutputFormatError as exc:
                    raise ModelResponseFormatError(str(exc), response=response) from exc
        try:
            response = dict(
                self._model().chat_completion(
                    messages,
                    tools=tools,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    agent_key=self.agent_key,
                    step_no=self.step_no,
                    purpose="skill_runtime",
                    prompt_key=self._active_skill_name,
                    retry=self.retry_attempts,
                    response_validator=validate_response,
                    format_repair_attempts=2,
                )
            )
        except ModelCallCancelled:
            raise
        except ModelResponseFormatError as exc:
            raise SkillOutputFormatError(f"Skill model output remained invalid after corrections: {exc}") from exc
        except Exception as exc:
            raise SkillModelError(f"Skill model call failed after retries: {exc}") from exc
        check_cancelled(self._control)
        return response

    def _validated_completion(self, messages, *, tools, skill, allowed, scripts,
                              completed, seen_ids, trace):
        """Validate the entire proposed batch while every tool is still unexecuted."""
        def validate(response):
            self._validate_tool_response(response, skill=skill, tools=tools, allowed=allowed,
                scripts=scripts, completed=completed, seen_ids=seen_ids, trace=trace)

        previous = self._response_validator
        self._response_validator = validate
        try:
            response = self._complete(messages, tools=tools)
            # Injected model clients and test adapters need the same boundary,
            # even when they do not implement the gateway's validator callback.
            validate(response)
            return response
        finally:
            self._response_validator = previous

    @classmethod
    def _validate_tool_response(cls, response, *, skill, tools, allowed, scripts,
                                completed, seen_ids, trace):
        from generative_agents.ga_runtime.models.gateway import LLMModel

        try:
            LLMModel._validate_chat_message(response)
        except ModelResponseFormatError as exc:
            cls._reject_tool_call(skill, {}, str(exc), trace)
        calls = response.get('tool_calls') or []
        # Validate a copy: a rejected response must not reserve IDs or create
        # result references. Corrected responses may reuse those unused IDs.
        cls._validate_tool_call_ids(skill, calls, set(seen_ids), trace)
        available_results = dict(completed)
        tool_names = {tool['function']['name'] for tool in tools}
        for call in calls:
            function = call['function']
            name = function['name']
            arguments = json.loads(function['arguments'])
            if name not in tool_names:
                cls._reject_tool_call(skill, call, f'Skill {skill} attempted unavailable tool {name}', trace)
            if name == 'call_skill':
                try:
                    cls._child_input(arguments, available_results)
                except SkillModelError as exc:
                    cls._reject_tool_call(skill, call, str(exc), trace)
                if arguments['name'] not in allowed:
                    cls._reject_tool_call(skill, call,
                        f'Skill {skill} attempted unavailable child {arguments["name"]}', trace)
                # A following call in this batch may refer to this earlier
                # child. Its actual result is resolved only after it succeeds.
                available_results[call['id']] = ''
            elif name == 'run_skill_script':
                if (set(arguments) != {'function', 'input_text'}
                        or not isinstance(arguments.get('function'), str)
                        or arguments['function'] not in scripts
                        or not isinstance(arguments.get('input_text'), str)):
                    cls._reject_tool_call(skill, call,
                        'run_skill_script requires an available function and string input_text', trace)

    def _model(self):
        """Lazily create the shared traced model adapter for direct runtimes."""

        if self._model_client is None:
            from generative_agents.ga_runtime.models.gateway import create_llm_model

            self._model_client = create_llm_model(
                {
                    "provider": self.provider,
                    "model": self.model,
                    "base_url": self.base_url,
                    "api_key": self.api_key,
                    "timeout_seconds": self.timeout,
                    "timeout": self.timeout,
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature,
                    "enable_thinking": self.enable_thinking,
                    "retry_attempts": self.retry_attempts,
                    "retry_backoff_seconds": self.retry_backoff_seconds,
                },
                recorder=self._recorder,
                control=self._control,
                logger=self._logger,
                sleep=self._sleep,
            )
        return self._model_client

    @staticmethod
    def _reject_tool_call(skill: str, call: Mapping[str, Any], error: str, trace: list[dict[str, Any]]) -> None:
        function = call.get("function") or {}
        tool_name = function.get("name")
        event = {
            "event": "skill.call.rejected" if tool_name == "call_skill" else "tool.call.rejected",
            "skill": skill, "tool": tool_name, "tool_call_id": call.get("id"), "error": error,
        }
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            arguments = None
        if isinstance(arguments, dict):
            event["arguments"] = arguments
            if tool_name == "call_skill":
                event["child"] = arguments.get("name")
                if "input_from" in arguments:
                    event["input_from"] = arguments["input_from"]
        trace.append(event)
        raise SkillOutputFormatError(error)

    @classmethod
    def _validate_tool_call_ids(cls, skill, calls, seen_ids, trace) -> None:
        """Reject an ambiguous message before any of its tools can run."""
        current_ids: set[str] = set()
        for call in calls:
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id.strip():
                cls._reject_tool_call(skill, call, "Tool call requires a non-empty string tool_call_id", trace)
            if call_id in seen_ids or call_id in current_ids:
                cls._reject_tool_call(skill, call, f"Model returned duplicate tool_call_id: {call_id}", trace)
            current_ids.add(call_id)
        seen_ids.update(current_ids)

    @staticmethod
    def _child_input(arguments: Mapping[str, Any], completed_results: Mapping[str, str]) -> str:
        """Resolve task text without interpreting or rewriting its contents."""
        if set(arguments) - {"name", "input_text", "input_from"}:
            raise SkillModelError("call_skill accepts only name and one of input_text or input_from")
        if not isinstance(arguments.get("name"), str) or not arguments["name"].strip():
            raise SkillModelError("call_skill name must be a non-empty string")
        if ("input_text" in arguments) == ("input_from" in arguments):
            raise SkillModelError("call_skill requires exactly one of input_text or input_from")
        if "input_text" in arguments:
            text = arguments["input_text"]
            if not isinstance(text, str):
                raise SkillModelError("call_skill input_text must be a string")
            return text
        source_id = arguments["input_from"]
        if not isinstance(source_id, str) or not source_id.strip():
            raise SkillModelError("call_skill input_from must be a non-empty tool_call_id string")
        if source_id not in completed_results:
            raise SkillModelError(
                f"call_skill input_from does not identify this caller's completed child result: {source_id}"
            )
        return completed_results[source_id]

    @staticmethod
    def _tool_request_fingerprint(
        skill_name: str, tool_name: str, arguments: Mapping[str, Any]
    ) -> str:
        encoded = json.dumps(
            {
                "skill": skill_name,
                "tool": tool_name,
                "arguments": arguments,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _semantic_tool_fingerprint(
        skill_name: str, tool_name: str, arguments: Mapping[str, Any]
    ) -> str:
        """Keep distinct reads separate, while detecting paraphrased delegation loops."""

        if tool_name in {"world-perceive", "world-navigate", "memory-stream-search"}:
            # Equal read results (including no matches) do not establish that
            # different queries, limits, radii or destinations are redundant.
            # Identical requests and the shared call budget still bound loops.
            return SkillRuntime._tool_request_fingerprint(skill_name, tool_name, arguments)

        target = ""
        if tool_name == "call_skill":
            target = str(arguments.get("name") or "")
        elif tool_name == "run_skill_script":
            target = str(arguments.get("function") or "")
        encoded = json.dumps(
            {"skill": skill_name, "tool": tool_name, "target": target},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _input_message(input_text: str, context: Mapping[str, Any]) -> str:
        """执行`input``message`的内部处理，供当前模块或类复用。

        参数:
            input_text: 传给模型或技能处理的原始输入文本。 类型：`str`。
            context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`Mapping[str, Any]`。

        返回:
            返回处理后的文本或稳定标识。
        """
        if not context:
            return input_text
        return (
            f"Current task:\n{input_text}\n\n"
            "Runtime context (read it as context, not as a required output schema):\n"
            f"{json.dumps(context, ensure_ascii=False, default=str)}"
        )

    @staticmethod
    def _run_script(path: Path, input_text: str, context: dict[str, Any]) -> str:
        """执行运行`script`的内部处理，供当前模块或类复用。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
            input_text: 传给模型或技能处理的原始输入文本。 类型：`str`。
            context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`dict[str, Any]`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            SkillRuntimeError: 当底层操作报告该异常条件时抛出。
        """
        module = SkillRuntime._load_script_module(path)
        handler = getattr(module, "run", None)
        if not callable(handler):
            raise SkillRuntimeError(
                f"Skill script must define run(input_text, context): {path}"
            )
        output = handler(input_text, context)
        if not isinstance(output, str):
            raise SkillRuntimeError(
                f"Skill script returned {type(output).__name__}; expected str"
            )
        return output

    @staticmethod
    def _load_script_module(path: Path) -> ModuleType:
        """加载`script``module`。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

        返回:
            返回 `ModuleType` 类型的处理结果。

        异常:
            SkillRuntimeError: 当底层操作报告该异常条件时抛出。
        """
        module_name = (
            f"ga_skill_{path.parent.parent.name.replace('-', '_')}_{uuid4().hex}"
        )
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise SkillRuntimeError(f"Cannot load Skill script: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _script_handlers(self, document: SkillDocument) -> dict[str, Any]:
        """执行`script``handlers`的内部处理，供当前模块或类复用。

        参数:
            document: 待校验、转换或持久化的结构化文档。 类型：`SkillDocument`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        handlers: dict[str, Any] = {}
        for relative_path in document.scripts:
            path = document.path.parent / relative_path
            if path.suffix.casefold() != ".py" or path.name == "main.py":
                continue
            module = self._load_script_module(path)
            for name, value in vars(module).items():
                if (
                    name.startswith("_")
                    or not callable(value)
                    or getattr(value, "__module__", None) != module.__name__
                ):
                    continue
                handlers[f"{path.stem}.{name}"] = value
        return handlers
