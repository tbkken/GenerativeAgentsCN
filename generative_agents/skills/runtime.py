"""通过 OpenAI 兼容聊天端点执行自然语言 Skill。

Runtime 负责在模型调用、私有脚本、子 Skill 和 MCP 工具之间循环，直到得到最终文本；
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
from typing import Any, Mapping
from uuid import uuid4

from .registry import SkillDocument, SkillRegistry
from .mcp import SkillMCPServer


class SkillRuntimeError(RuntimeError):
    """A model, script, or child Skill could not complete."""


class RecoverableSkillRuntimeError(SkillRuntimeError):
    """A transient model failure or no-progress loop can degrade one step."""


class SkillModelError(RecoverableSkillRuntimeError):
    """The configured model gateway exhausted its retry policy."""


class SkillLoopError(RecoverableSkillRuntimeError):
    """The Brain repeated a stable tool call without making progress."""


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
    """Execute atomic Skills, packs, and brains with one string between calls."""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        mcp: SkillMCPServer | None = None,
        timeout: float = 300,
        max_hops: int = 8,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        enable_thinking: bool = False,
        provider: str = "vllm",
        retry_attempts: int = 1,
        retry_backoff_seconds: float = 0,
        model_client=None,
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
            mcp: 技能调用使用的 MCP 服务端或客户端适配器。 类型：`SkillMCPServer | None`。 默认值：`None`。
            timeout: 等待操作的最长秒数；超时后按调用协议返回或抛出异常。 类型：`float`。 默认值：`300`。
            max_hops: `hops`允许的最大值。 类型：`int`。 默认值：`8`。

        返回:
            无返回值。
        """
        self.registry = registry
        self.base_url = (
            base_url
            or os.getenv("GA_SKILL_LLM_BASE_URL")
            or "http://127.0.0.1:11434/v1"
        ).rstrip("/")
        self.model = model or os.getenv("GA_SKILL_LLM_MODEL") or "qwen3.8:27b-q4_K_M"
        self.api_key = (
            api_key if api_key is not None else os.getenv("GA_SKILL_LLM_API_KEY", "")
        )
        self.mcp = mcp
        self.timeout = timeout
        self.max_hops = max_hops
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.enable_thinking = bool(enable_thinking)
        self.provider = str(provider or "vllm")
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_backoff_seconds = max(0, float(retry_backoff_seconds))
        self._model_client = model_client
        self._recorder = recorder
        self._control = control
        self._logger = logger
        self._sleep = sleep
        self.agent_key = agent_key
        self.step_no = step_no
        self.max_identical_tool_calls = max(1, int(max_identical_tool_calls))
        self._active_skill_name: str | None = None
        self._total_tool_calls = 0
        self._tool_progress: dict[str, tuple[str, int]] = {}

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
            包含规范化 Skill 名称、输出文本和每次脚本/子 Skill/MCP 调用的结果。
        """
        trace: list[dict[str, Any]] = []
        self._total_tool_calls = 0
        self._tool_progress = {}
        output = self._run(
            self.registry.get(skill_name),
            str(input_text),
            dict(context or {}),
            trace,
            depth=0,
        )
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
        if depth > self.max_hops:
            raise SkillRuntimeError("Skill call depth exceeded the configured limit")
        script_path = document.path.parent / "scripts" / "main.py"
        # 带 main.py 的原子 Skill 是确定性快路径，不需要额外调用语言模型。
        if script_path.is_file():
            trace.append(
                {
                    "event": "skill.start",
                    "skill": document.name,
                    "input_text": input_text,
                }
            )
            output = self._run_script(script_path, input_text, context)
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
        mcp_tools = (
            [
                tool
                for tool in self.mcp.tools()
                if str(tool.get("name") or "") in document.markdown
            ]
            if self.mcp
            else []
        )
        script_handlers = self._script_handlers(document)
        # 没有任何可调用依赖的叶子 Skill，只需要一次普通聊天完成。
        if not children and not mcp_tools and not script_handlers:
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
            output = self._complete(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            )["content"]
            trace.append(
                {"event": "skill.result", "skill": document.name, "output_text": output}
            )
            return output

        system_prompt = (
            document.markdown
            + "\n\nYou may call one listed child Skill or MCP service at a time. "
            "Pass child Skills all useful context as plain natural language. Treat every tool result "
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
                        "description": "Call one child Skill and receive its natural-language result.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "enum": sorted(allowed)},
                                "input_text": {
                                    "type": "string",
                                    "description": "Complete natural-language context for the child Skill.",
                                },
                            },
                            "required": ["name", "input_text"],
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
                    "description": tool["description"],
                    "parameters": tool["inputSchema"],
                },
            }
            for tool in mcp_tools
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
        # 组合 Skill 采用受限工具循环；每轮模型只能调用当前文档显式允许的能力。
        for _ in range(self.max_hops):
            self._active_skill_name = document.name
            response = self._complete(messages, tools=tools)
            tool_calls = response.get("tool_calls") or []
            if not tool_calls:
                output = str(response.get("content") or "").strip()
                if not output:
                    raise SkillRuntimeError(f"Skill {document.name} returned no text")
                trace.append(
                    {
                        "event": "skill.result",
                        "skill": document.name,
                        "output_text": output,
                    }
                )
                return output
            assistant_message = {
                "role": "assistant",
                "content": response.get("content") or "",
                "tool_calls": tool_calls,
            }
            messages.append(assistant_message)
            for call in tool_calls:
                function = call.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        raise TypeError("tool arguments must be a JSON object")
                except (json.JSONDecodeError, TypeError) as exc:
                    raise SkillModelError(
                        "Model returned invalid tool arguments"
                    ) from exc
                tool_name = str(function.get("name") or "")
                request_fingerprint = self._tool_request_fingerprint(
                    document.name, tool_name, arguments
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
                self._total_tool_calls += 1
                if self._total_tool_calls > self.max_hops:
                    trace.append(
                        {
                            "event": "loop.budget_exhausted",
                            "skill": document.name,
                            "tool": tool_name,
                            "tool_call_count": self._total_tool_calls,
                        }
                    )
                    raise SkillLoopError(
                        f"Brain exceeded the configured {self.max_hops} tool-call budget"
                    )
                if tool_name == "call_skill":
                    # 子 Skill 获得自然语言上下文，但仍共享本次运行的审计轨迹。
                    child_name = arguments.get("name")
                    child = allowed.get(child_name)
                    if child is None:
                        raise SkillRuntimeError(
                            f"Skill {document.name} attempted unavailable child {child_name}"
                        )
                    child_input = str(arguments.get("input_text") or input_text)
                    trace.append(
                        {
                            "event": "skill.call",
                            "skill": document.name,
                            "child": child.name,
                            "input_text": child_input,
                        }
                    )
                    tool_output = self._run(
                        child, child_input, context, trace, depth=depth + 1
                    )
                elif tool_name == "run_skill_script":
                    # 私有函数来自当前 Skill 目录，不能按模型给出的任意路径加载代码。
                    handler_name = str(arguments.get("function") or "")
                    handler = script_handlers.get(handler_name)
                    if handler is None:
                        raise SkillRuntimeError(
                            f"Skill {document.name} attempted unavailable script function {handler_name}"
                        )
                    script_input = str(arguments.get("input_text") or input_text)
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
                elif self.mcp and any(tool["name"] == tool_name for tool in mcp_tools):
                    # MCP 工具也必须出现在当前 Skill 声明的允许列表中。
                    result = self.mcp.call(tool_name, arguments)
                    tool_output = "\n".join(
                        str(item.get("text") or "")
                        for item in result.get("content", [])
                        if item.get("type") == "text"
                    ).strip()
                    trace.append(
                        {
                            "event": "mcp.call",
                            "skill": document.name,
                            "tool": tool_name,
                            "input_text": json.dumps(arguments, ensure_ascii=False),
                            "output_text": tool_output,
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
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or f"call-{uuid4().hex}",
                        "name": tool_name,
                        "content": tool_output,
                    }
                )
        raise SkillLoopError(
            f"Skill {document.name} did not finish within {self.max_hops} calls"
        )

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
        try:
            return dict(
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
                )
            )
        except Exception as exc:
            raise SkillModelError(f"Skill model call failed after retries: {exc}") from exc

    def _model(self):
        """Lazily create the shared traced model adapter for direct runtimes."""

        if self._model_client is None:
            from generative_agents.modules.model.llm_model import create_llm_model

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
