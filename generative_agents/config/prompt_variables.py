"""Canonical Prompt variable paths for workflow-owned LLM nodes."""

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
    "schedule_daily": frozenset(
        {"context", "base_desc", "wake_up", "daily_schedule"}
    ),
    "schedule_decompose": frozenset({"context", "plan"}),
}


def prompt_input_roots(prompt_key: str) -> frozenset[str]:
    """Return the explicit input roots owned by one bundled LLM node."""

    return _DIRECT_INPUT_ROOTS.get(prompt_key, frozenset({"context"}))


def _canonical_path(path: str, *, input_roots: frozenset[str]) -> str:
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
    """Use only explicit LLM-node input roots and property paths."""

    roots = prompt_input_roots(prompt_key)
    return _PROMPT_VARIABLE.sub(
        lambda match: "{" + _canonical_path(match.group(1), input_roots=roots) + "}",
        content,
    )


def canonicalize_prompt_payload(
    prompts: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a deep-enough copy with every known Prompt body canonicalized."""

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
