"""Natural-language Skill execution over an OpenAI-compatible chat endpoint."""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping
from uuid import uuid4

import requests

from .registry import SkillDocument, SkillRegistry
from .mcp import SkillMCPServer


class SkillRuntimeError(RuntimeError):
    """A model, script, or child Skill could not complete."""


@dataclass(frozen=True, slots=True)
class SkillRunResult:
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

    def run(
        self,
        skill_name: str,
        input_text: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> SkillRunResult:
        """执行当前组件负责的完整流程，并返回本次执行结果。

        参数:
            skill_name: 需要调用的技能名称，必须能在当前运行的技能快照中解析。 类型：`str`。
            input_text: 传给模型或技能处理的原始输入文本。 类型：`str`。
            context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`Mapping[str, Any] | None`。 默认值：`None`。

        返回:
            返回 `SkillRunResult` 类型的处理结果。
        """
        trace: list[dict[str, Any]] = []
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
        """执行运行的内部处理，供当前模块或类复用。

        参数:
            document: 待校验、转换或持久化的结构化文档。 类型：`SkillDocument`。
            input_text: 传给模型或技能处理的原始输入文本。 类型：`str`。
            context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`dict[str, Any]`。
            trace: 传入当前算法的`trace`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`list[dict[str, Any]]`。
            depth: 树遍历、递归展开或引用解析允许到达的最大层级。 类型：`int`。

        返回:
            返回处理后的文本或稳定标识。

        异常:
            SkillRuntimeError: 当底层操作报告该异常条件时抛出。
        """
        if depth > self.max_hops:
            raise SkillRuntimeError("Skill call depth exceeded the configured limit")
        script_path = document.path.parent / "scripts" / "main.py"
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
        mcp_tools = self.mcp.tools() if self.mcp and "MCP" in document.markdown else []
        script_handlers = self._script_handlers(document)
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
        for _ in range(self.max_hops):
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
                except json.JSONDecodeError as exc:
                    raise SkillRuntimeError(
                        "Model returned invalid tool arguments"
                    ) from exc
                tool_name = str(function.get("name") or "")
                if tool_name == "call_skill":
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
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or f"call-{uuid4().hex}",
                        "name": tool_name,
                        "content": tool_output,
                    }
                )
        raise SkillRuntimeError(
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
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 2048,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            document = response.json()
            return dict(document["choices"][0]["message"])
        except (
            requests.RequestException,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as exc:
            raise SkillRuntimeError(f"Local Skill model call failed: {exc}") from exc

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
