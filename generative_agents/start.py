"""运行级仿真循环与可安全导入的命令行适配器。

Web 工作进程只根据已验证的运行清单构建依赖。本模块不读取启动目录，也不根据展示名
回退查找路径，从而保证同一运行始终使用发布时冻结的输入。
"""

from __future__ import annotations

import argparse
import copy
import importlib
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from generative_agents.modules.game import Game
from generative_agents.modules.config_adapter import ConfigAdapter
from generative_agents.runtime.checkpoint import (
    CheckpointBundleWriter,
    CheckpointSnapshot,
)
from generative_agents.runtime.commit import FileStepCommitter
from generative_agents.runtime.context import RunPaths, SimulationContext
from generative_agents.runtime.frame_store import FrameStore
from generative_agents.runtime.manifest import RunManifestStore
from generative_agents.runtime.result_collector import StepResultCollector
from generative_agents.runtime.file_result_projector import FileResultProjector
from generative_agents.runtime.results import StepResultBuilder


class StepCommitter(Protocol):
    """单步提交协议；实现必须保持帧、检查点和投影的持久化顺序。"""

    def commit(self, result, *, force_checkpoint: bool):
        """按照持久化顺序提交当前仿真步，并返回提交凭据。

        参数:
            result: 当前仿真步或上游组件产生的结构化结果。
            force_checkpoint: 是否无视常规间隔，为当前步骤强制生成检查点。 类型：`bool`。

        返回:
            无返回值。
        """
        ...


def apply_checkpoint_state(config: dict, state: Mapping) -> dict:
    """把检查点中的动态状态覆盖到已发布配置副本。

    参数:
        config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。 类型：`dict`。
        state: 检查点保存的动态状态映射，必须包含与已发布配置一致的智能体键集合。 类型：`Mapping`。

    返回:
        返回以字段名或业务键组织的结构化映射。

    异常:
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """
    restored = copy.deepcopy(config)
    checkpoint_agents = state.get("agents")
    if not isinstance(checkpoint_agents, Mapping):
        raise ValueError("checkpoint state must contain an agents mapping")
    configured_keys = set(restored.get("agents", {}))
    checkpoint_keys = set(checkpoint_agents)
    if checkpoint_keys != configured_keys:
        raise ValueError("checkpoint agent keys do not match the published Revision")

    def overlay(target: dict, source: Mapping) -> None:
        """执行 的`overlay`操作。

        参数:
            target: 当前操作使用的`target`。 类型：`dict`。
            source: 当前操作使用的`source`。 类型：`Mapping`。

        返回:
            无返回值。
        """
        for key, value in source.items():
            if isinstance(value, Mapping) and isinstance(target.get(key), dict):
                overlay(target[key], value)
            else:
                target[key] = copy.deepcopy(value)

    for agent_key, agent_state in checkpoint_agents.items():
        if not isinstance(agent_state, Mapping):
            raise ValueError(f"checkpoint agent state is invalid: {agent_key}")
        overlay(restored["agents"][agent_key], agent_state)
    return restored


@dataclass(slots=True)
class SimulationRunner:
    """运行一次隔离仿真的主循环，并只在完整步骤边界响应控制请求。

    ``Game`` 负责计算世界变化，``StepResultBuilder`` 收集本步事实，``committer``
    负责把事实持久化。Runner 不直接操作数据库，因此命令行运行和 Worker 运行可以
    复用同一套推进逻辑。
    """

    context: SimulationContext
    game: Game
    committer: StepCommitter
    checkpoint_interval_steps: int = 1
    completed_steps: int = 0
    agent_status: dict = field(init=False)

    def __post_init__(self) -> None:
        """完成数据类初始化后的规范化与不变量校验。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if self.checkpoint_interval_steps < 1:
            raise ValueError("checkpoint_interval_steps must be positive")
        self.agent_status = {
            agent_key: {"coord": tuple(agent.coord), "path": tuple(agent.path or ())}
            for agent_key, agent in self.game.agents.items()
        }

    def run(self, steps: int, *, stride_minutes: int) -> int:
        """从当前恢复边界向前执行若干完整仿真步。

        参数:
            steps: 本次调用需要推进的仿真步数量，必须为正整数。
            stride_minutes: 每个仿真步推进的虚拟分钟数，必须为正整数。

        返回:
            返回当前 Run 已提交的最后步骤号。

        异常:
            ValueError: 步数或虚拟时间步长不是正数。

        说明:
            每一步严格遵循“推进世界—捕获结果—写帧—可选检查点—更新投影”的顺序；控制请求只在安全边界生效。
        """
        if steps < 1 or stride_minutes < 1:
            raise ValueError("steps and stride_minutes must be positive")
        target_step = self.completed_steps + steps
        self._bind_agent_step(self.completed_steps + 1)
        self.game.reset_game()
        for offset in range(steps):
            # 暂停和取消只能在两步之间生效，绝不能留下“移动了一半但尚未提交”的状态。
            if (
                self.context.control.cancel_requested
                or self.context.control.pause_requested
            ):
                break
            step_no = self.completed_steps + 1
            self._bind_agent_step(step_no)
            # 本步所有可观察副作用先进入构建器，最后一次性冻结为 StepResult。
            builder = StepResultBuilder(
                run_id=self.context.run_id,
                attempt_id=self.context.attempt_id,
                step_no=step_no,
                virtual_time=self.context.clock.get_date(),
            )
            memory_stream = getattr(self.context, "memory_stream", None)
            if memory_stream is not None:
                memory_stream.begin_step(
                    step_no,
                    self.context.clock.get_date(),
                )
            collector = StepResultCollector(
                builder,
                name_to_key=self.game.agent_keys_by_name,
            )
            for agent_key, status in self.agent_status.items():
                agent = self.game.get_agent(agent_key)
                from_coord = tuple(agent.coord)
                outcome = self.game.agent_think(
                    agent_key,
                    status,
                    step_no=step_no,
                    total_steps=target_step,
                    stride_minutes=stride_minutes,
                )
                committed = self.game.commit_world_action(
                    agent_key,
                    outcome,
                    stride_minutes=stride_minutes,
                    movement_budget=self._movement_budget(stride_minutes),
                )
                outcome = committed["outcome"]
                planned_path = committed["planned_path"]
                executed_path = committed["executed_path"]
                remaining = committed["remaining_path"]
                collector.capture_agent(
                    agent_key,
                    agent,
                    from_coord,
                    outcome,
                    executed_path=executed_path,
                    planned_path=planned_path,
                    remaining_path=remaining,
                )
                # Agent.path 属于 Game.snapshot_state。运行中断后必须恢复尚未消费的原路径，
                # 不能直接跳到终点，也不能重新寻路，否则恢复前后的轨迹会分叉。
                status["coord"] = tuple(agent.coord)
                status["path"] = tuple(agent.path or ())
            if memory_stream is not None:
                for event in memory_stream.drain_result_events():
                    collector.capture_event(event)
            result = collector.freeze()
            # 普通间隔、最后一步或控制边界都可以产生检查点；提交器负责具体写入顺序。
            terminal_boundary = (
                offset == steps - 1
                or self.context.control.pause_requested
                or self.context.control.cancel_requested
            )
            force_checkpoint = (
                terminal_boundary or step_no % self.checkpoint_interval_steps == 0
            )
            self.committer.commit(result, force_checkpoint=force_checkpoint)
            self.completed_steps = step_no
            if not terminal_boundary:
                self.context.clock.forward(stride_minutes)
        return self.completed_steps

    def _bind_agent_step(self, step_no: int) -> None:
        """把当前步骤号绑定到所有智能体，供记忆和事件生成稳定序号。

        参数:
            step_no: 当前仿真步编号；提交后按运行维度单调递增。 类型：`int`。

        返回:
            无返回值。
        """
        for agent_key in self.agent_status:
            agent = self.game.get_agent(agent_key)
            bind = getattr(agent, "begin_step", None)
            if callable(bind):
                bind(step_no)

    def _movement_budget(self, stride_minutes: int) -> int:
        """根据算法配置计算本步最多可以消费多少个路径 Tile。

        参数:
            stride_minutes: 每个仿真步推进的虚拟分钟数。 类型：`int`。

        返回:
            返回至少为 1 的 Tile 数量。
        """
        profile = getattr(self.context, "algorithm", None)
        tiles_per_minute = int(getattr(profile, "movement_tiles_per_minute", 4))
        return max(1, stride_minutes * max(1, tiles_per_minute))


def build_file_committer(context: SimulationContext, game: Game) -> FileStepCommitter:
    """构建只写运行目录的提交器，供独立命令行仿真使用。

    参数:
        context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`SimulationContext`。
        game: 当前运行私有的仿真世界聚合。 类型：`Game`。

    返回:
        返回按“帧—检查点—文件投影”顺序工作的提交器。
    """
    checkpoint = CheckpointBundleWriter(
        context.paths,
        lambda _result: CheckpointSnapshot(
            state=game.snapshot_state(),
            conversation=game.conversation,
            storage_exporters=game.storage_exporters(),
            runtime_storage_exporters=game.runtime_storage_exporters(),
        ),
    )
    return FileStepCommitter(
        FrameStore(context.paths),
        FileResultProjector(context.paths),
        checkpoint,
    )


def build_runner(
    context: SimulationContext,
    definition,
    *,
    embedding_api_key: str = "",
    checkpoint_state: Mapping | None = None,
    checkpoint_conversation: Mapping | None = None,
    storage_root: str | Path | None = None,
) -> SimulationRunner:
    """构建`runner`。

    参数:
        context: 本次调用共享的运行上下文，包含路径、模型、技能和控制能力等依赖。 类型：`SimulationContext`。
        definition: 已校验的仿真定义，描述地图、智能体、模型与执行参数。
        embedding_api_key: 调用嵌入模型服务使用的 API 密钥；为空时由运行配置解析。 类型：`str`。 默认值：`''`。
        checkpoint_state: 从检查点读取的动态世界状态；为空表示从发布配置开始运行。 类型：`Mapping | None`。 默认值：`None`。
        checkpoint_conversation: 检查点保存的对话上下文；为空表示没有待恢复对话。 类型：`Mapping | None`。 默认值：`None`。
        storage_root: 存储使用的根目录路径。 类型：`str | Path | None`。 默认值：`None`。

    返回:
        返回 `SimulationRunner` 类型的处理结果。
    """

    config = ConfigAdapter().game_config(
        definition, embedding_api_key=embedding_api_key
    )
    if checkpoint_state is not None:
        config = apply_checkpoint_state(config, checkpoint_state)
    if storage_root is not None:
        config["storage_root"] = str(Path(storage_root))
    game = Game(
        config,
        copy.deepcopy(checkpoint_conversation or {}),
        context=context,
    )
    if checkpoint_state is not None:
        game.restore_runtime_state(dict(checkpoint_state))
    return SimulationRunner(
        context=context,
        game=game,
        committer=build_file_committer(context, game),
        checkpoint_interval_steps=definition.simulation.checkpoint_interval_steps,
    )


def build_parser() -> argparse.ArgumentParser:
    """构建`parser`。

    返回:
        返回 `argparse.ArgumentParser` 类型的处理结果。
    """
    parser = argparse.ArgumentParser(description="run one isolated experiment worker")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--attempt-id", type=UUID, required=True)
    parser.add_argument("--steps", type=int, required=True)
    parser.add_argument("--stride-minutes", type=int, required=True)
    parser.add_argument(
        "--runner-factory",
        required=True,
        help="Dotted callable module:function that verifies the manifest and returns SimulationRunner",
    )
    return parser


def _load_factory(path: str):
    """加载`factory`。

    参数:
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`str`。

    返回:
        返回函数计算得到的结果。

    异常:
        TypeError: 当参数类型不符合接口约定时抛出。
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("runner factory must use module:function syntax")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError("runner factory is not callable")
    return factory


def main(argv=None) -> int:
    """解析启动参数并执行当前模块的主流程。

    参数:
        argv: 命令行参数序列；为 `None` 时读取当前进程的命令行。 默认值：`None`。

    返回:
        返回计算得到的整数值或版本号。

    异常:
        TypeError: 当参数类型不符合接口约定时抛出。
    """
    args = build_parser().parse_args(argv)
    paths = RunPaths.under(args.data_root, args.run_id)
    manifest = RunManifestStore(paths).load_verified()
    runner = _load_factory(args.runner_factory)(
        paths=paths,
        manifest=manifest,
        attempt_id=args.attempt_id,
    )
    if not isinstance(runner, SimulationRunner):
        raise TypeError("runner factory must return SimulationRunner")
    runner.run(args.steps, stride_minutes=args.stride_minutes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
