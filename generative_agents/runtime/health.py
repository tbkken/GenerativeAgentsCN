"""Cheap runtime-contract checks used by publication preflight.

The preflight must not execute a trial simulation, but it can still reject a
worker build whose core entry point reads instance state that its constructor
never establishes.  This catches deterministic Step-1 failures before any
model request is sent.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any


def _self_attributes(function: Any) -> tuple[set[str], set[str]]:
    """Return instance attributes read and assigned by one function."""

    source = textwrap.dedent(inspect.getsource(function))
    tree = ast.parse(source)
    reads: set[str] = set()
    writes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name) or node.value.id != "self":
            continue
        if isinstance(node.ctx, ast.Load):
            reads.add(node.attr)
        elif isinstance(node.ctx, (ast.Store, ast.Del)):
            writes.add(node.attr)
    return reads, writes


def runtime_health_issues(game_class: type | None = None) -> list[dict[str, str]]:
    """Return blocking issues for deterministic core-runtime invariants."""

    if game_class is None:
        from generative_agents.modules.game import Game

        game_class = Game
    try:
        _constructor_reads, constructor_writes = _self_attributes(
            game_class.__init__
        )
        method = getattr(game_class, "agent_think")
        method_reads, _method_writes = _self_attributes(method)
    except (OSError, TypeError, IndentationError, SyntaxError) as exc:
        return [
            {
                "code": "RUNTIME_CORE_HEALTH_CHECK_FAILED",
                "path": "runtime.Game",
                "message": f"无法验证运行内核初始化合同：{type(exc).__name__}: {exc}",
            }
        ]

    class_members = set(dir(game_class))
    missing = sorted(method_reads - constructor_writes - class_members)
    return [
        {
            "code": "RUNTIME_INSTANCE_ATTRIBUTE_UNINITIALIZED",
            "path": f"runtime.Game.{name}",
            "message": f"运行内核会读取未由构造函数初始化的状态：Game.{name}",
        }
        for name in missing
    ]


__all__ = ["runtime_health_issues"]
