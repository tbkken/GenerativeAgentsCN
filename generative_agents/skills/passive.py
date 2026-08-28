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
    """一次被动 Skill 的输出文本和规范化状态更新。"""

    skill: str
    revision: str
    output_text: str
    trace: tuple[dict[str, Any], ...]


class SnapshotPassiveSkillRuntime:
    """Execute feedback-only Game Object Skills from a frozen Run registry.

    The executor receives only copied, serializable request context. It has no
    Agent callback, scheduler, MCP server, or world mutation API, so a Game
    Object can only return text after an Agent explicitly invokes it. Text-only
    Skills use the shared model gateway; script-backed Skills remain a
    deterministic fast path.
    """

    def __init__(
        self,
        skills: Mapping[str, Mapping[str, Any]],
        *,
        registry=None,
        model_config: Mapping[str, Any] | None = None,
        model_client=None,
        recorder=None,
        control=None,
        logger=None,
    ) -> None:
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
        self._registry = registry
        self._model_config = dict(model_config or {})
        self._model_client = model_client
        self._recorder = recorder
        self._control = control
        self._logger = logger

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
        if document.get("kind") == "brain":
            raise PassiveSkillRuntimeError(
                f"Game Object cannot bind a Brain Skill: {name}"
            )
        if self._registry is not None:
            return self._run_frozen_skill(
                name,
                str(input_text),
                context=copy.deepcopy(dict(context)),
            )
        scripts = document.get("scripts")
        source = (
            scripts.get("scripts/main.py") if isinstance(scripts, Mapping) else None
        )
        if not isinstance(source, str) or not source.strip():
            raise PassiveSkillRuntimeError(
                "Text-only Game Object Skills require a frozen Skill registry "
                f"and model gateway: {name}"
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

    def _run_frozen_skill(
        self,
        name: str,
        input_text: str,
        *,
        context: Mapping[str, Any],
    ) -> PassiveSkillResult:
        """Run one immutable Skill without exposing world mutation MCPs."""

        from .runtime import SkillRuntime, SkillRuntimeError

        document = self._registry.get(name)
        if document.kind == "brain":
            raise PassiveSkillRuntimeError(
                f"Game Object cannot bind a Brain Skill: {name}"
            )
        config = self._model_config
        agent = context.get("agent") if isinstance(context.get("agent"), Mapping) else {}
        runtime = SkillRuntime(
            self._registry,
            base_url=str(config.get("base_url") or ""),
            model=str(config.get("resolved_model") or config.get("model") or ""),
            api_key=str(config.get("api_key") or ""),
            mcp=None,
            timeout=float(config.get("timeout_seconds") or 300),
            max_hops=int(config.get("max_hops") or 12),
            temperature=float(config.get("temperature") or 0.2),
            max_tokens=int(config.get("max_tokens") or 2048),
            enable_thinking=bool(config.get("enable_thinking", False)),
            provider=str(config.get("provider") or "vllm"),
            retry_attempts=int(config.get("retry_attempts") or 1),
            retry_backoff_seconds=float(
                config.get("retry_backoff_seconds") or 0
            ),
            model_client=self._model_client,
            recorder=self._recorder,
            control=self._control,
            logger=self._logger,
            agent_key=str(agent.get("agent_key") or "") or None,
            step_no=int(context.get("step_no") or 0) or None,
        )
        try:
            result = runtime.run(name, input_text, context=context)
        except SkillRuntimeError as exc:
            raise PassiveSkillRuntimeError(
                f"Game Object Skill {name} failed: {exc}"
            ) from exc
        output = result.output_text.strip()
        if not output:
            raise PassiveSkillRuntimeError(
                f"Game Object Skill {name} must return non-empty text"
            )
        revision = document.revision
        return PassiveSkillResult(
            skill=name,
            revision=revision,
            output_text=output,
            trace=(
                {
                    "event": "game_object_skill.start",
                    "skill": name,
                    "revision": revision,
                    "input_text": input_text,
                },
                *result.trace,
                {
                    "event": "game_object_skill.result",
                    "skill": name,
                    "revision": revision,
                    "output_text": output,
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
