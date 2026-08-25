"""Canonical variable paths used inside atomic Skill instruction templates."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_PROMPT_VARIABLE = re.compile(
    r"\$?\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}"
)

# Every bundled LLM node receives ``context``.  These nodes additionally own
# first-class upstream inputs, so their root variables must not be folded into
# the context object.
_DIRECT_INPUT_ROOTS: dict[str, frozenset[str]] = {
    "retrieve_currently": frozenset({"context", "plan", "thought"}),
    "schedule_init": frozenset({"context", "base_desc", "wake_up"}),
    "schedule_daily": frozenset({"context", "base_desc", "wake_up", "daily_schedule"}),
    "schedule_decompose": frozenset({"context", "plan"}),
}


def prompt_input_roots(prompt_key: str) -> frozenset[str]:
    """执行 的提示词`input``roots`操作。

    参数:
        prompt_key: 用于稳定定位提示词的键。 类型：`str`。

    返回:
        返回按接口约定组织的结果集合。
    """

    return _DIRECT_INPUT_ROOTS.get(prompt_key, frozenset({"context"}))


def _canonical_path(path: str, *, input_roots: frozenset[str]) -> str:
    """执行`canonical`路径的内部处理，供当前模块或类复用。

    参数:
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`str`。
        input_roots: 允许解析输入文件的受控根目录集合。 类型：`frozenset[str]`。

    返回:
        返回处理后的文本或稳定标识。
    """
    if path == "context":
        return "context.background"
    root = path.split(".", 1)[0]
    if root in input_roots:
        return path
    if path in {"name", "agent"}:
        return "context.agent.name"
    if path == "another":
        return "context.another.name"
    return f"context.{path}"


def canonicalize_prompt_content(content: str, *, prompt_key: str) -> str:
    """执行 的`canonicalize`提示词`content`操作。

    参数:
        content: 待解析、写入、哈希或发送给下游组件的正文内容。 类型：`str`。
        prompt_key: 用于稳定定位提示词的键。 类型：`str`。

    返回:
        返回处理后的文本或稳定标识。
    """

    roots = prompt_input_roots(prompt_key)
    return _PROMPT_VARIABLE.sub(
        lambda match: "{" + _canonical_path(match.group(1), input_roots=roots) + "}",
        content,
    )


def canonicalize_prompt_payload(
    prompts: Mapping[str, Any],
) -> dict[str, Any]:
    """执行 的`canonicalize`提示词载荷操作。

    参数:
        prompts: 当前修订版本声明的提示词配置集合。 类型：`Mapping[str, Any]`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """

    result: dict[str, Any] = {}
    for prompt_key, value in prompts.items():
        if isinstance(value, Mapping):
            item = dict(value)
            if isinstance(item.get("content"), str):
                canonical_content = canonicalize_prompt_content(
                    item["content"], prompt_key=prompt_key
                )
                if canonical_content != item["content"]:
                    item["content"] = canonical_content
                    item["sha256"] = None
            result[prompt_key] = item
        elif isinstance(value, str):
            result[prompt_key] = canonicalize_prompt_content(
                value, prompt_key=prompt_key
            )
        else:
            result[prompt_key] = value
    return result
