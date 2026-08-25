"""Run immutable, passive Game Object Skills from a Run manifest snapshot."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Mapping


class PassiveSkillRuntimeError(RuntimeError):
    """A passive Game Object Skill is unavailable or violates its contract."""


@dataclass(frozen=True, slots=True)
class PassiveSkillResult:
    skill: str
    revision: str
    output_text: str
    trace: tuple[dict[str, Any], ...]


class SnapshotPassiveSkillRuntime:
    """Execute script-backed Skills without reading shared runtime files.

    The executor receives only copied, serializable request context.  It has no
    Agent callback, scheduler, MCP server, or world mutation API, so a Game
    Object can respond only after the simulation explicitly invokes it.
    """

    def __init__(self, skills: Mapping[str, Mapping[str, Any]]) -> None:
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            skills: 当前智能体可调用的技能指令仓库或执行器集合。 类型：`Mapping[str, Mapping[str, Any]]`。

        返回:
            无返回值。
        """
        self._skills = {
            str(name).replace("_", "-"): dict(document)
            for name, document in skills.items()
        }
        self._handlers: dict[tuple[str, str], Any] = {}

    def run(
        self,
        skill_name: str,
        input_text: str,
        *,
        context: Mapping[str, Any],
    ) -> PassiveSkillResult:
        """执行当前组件负责的完整流程，并返回本次执行结果。

        参数:
            skill_name: 需要调用的技能名称，必须能在当前运行的技能快照中解析。 类型：`str`。
            input_text: 传给模型或技能处理的原始输入文本。 类型：`str`。
            context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`Mapping[str, Any]`。

        返回:
            返回 `PassiveSkillResult` 类型的处理结果。

        异常:
            PassiveSkillRuntimeError: 当底层操作报告该异常条件时抛出。
        """
        name = str(skill_name).strip().casefold().replace("_", "-")
        document = self._skills.get(name)
        if document is None:
            raise PassiveSkillRuntimeError(
                f"Game Object Skill is not present in the Run manifest: {name}"
            )
        if document.get("kind") != "atomic":
            raise PassiveSkillRuntimeError(f"Game Object Skill must be atomic: {name}")
        scripts = document.get("scripts")
        source = (
            scripts.get("scripts/main.py") if isinstance(scripts, Mapping) else None
        )
        if not isinstance(source, str) or not source.strip():
            raise PassiveSkillRuntimeError(
                f"Game Object Skill must provide scripts/main.py: {name}"
            )
        revision = str(document.get("revision") or "")
        handler = self._handler(name, revision, source)
        safe_context = copy.deepcopy(dict(context))
        trace = (
            {
                "event": "game_object_skill.start",
                "skill": name,
                "revision": revision,
                "input_text": str(input_text),
            },
        )
        try:
            output = handler(str(input_text), safe_context)
        except Exception as exc:
            raise PassiveSkillRuntimeError(
                f"Game Object Skill {name} failed: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(output, str) or not output.strip():
            raise PassiveSkillRuntimeError(
                f"Game Object Skill {name} must return non-empty text"
            )
        return PassiveSkillResult(
            skill=name,
            revision=revision,
            output_text=output.strip(),
            trace=(
                *trace,
                {
                    "event": "game_object_skill.result",
                    "skill": name,
                    "revision": revision,
                    "output_text": output.strip(),
                },
            ),
        )

    def _handler(self, name: str, revision: str, source: str):
        """执行`handler`的内部处理，供当前模块或类复用。

        参数:
            name: 目标对象的人类可读名称。 类型：`str`。
            revision: 当前读取、发布、克隆或校验的修订版本记录。 类型：`str`。
            source: 当前操作使用的`source`。 类型：`str`。

        返回:
            返回函数计算得到的结果。

        异常:
            PassiveSkillRuntimeError: 当底层操作报告该异常条件时抛出。
        """
        cache_key = (name, revision)
        cached = self._handlers.get(cache_key)
        if cached is not None:
            return cached
        module = ModuleType(f"ga_snapshot_skill_{name.replace('-', '_')}_{revision}")
        module.__dict__["__builtins__"] = __builtins__
        exec(
            compile(source, f"<skill:{name}@{revision}/scripts/main.py>", "exec"),
            module.__dict__,
        )
        handler = getattr(module, "run", None)
        if not callable(handler):
            raise PassiveSkillRuntimeError(
                f"Game Object Skill must define run(input_text, context): {name}"
            )
        self._handlers[cache_key] = handler
        return handler


__all__ = [
    "PassiveSkillResult",
    "PassiveSkillRuntimeError",
    "SnapshotPassiveSkillRuntime",
]
