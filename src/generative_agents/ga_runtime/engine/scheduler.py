"""供 ga_runtime 使用的仿真循环与依赖构建。

Web 工作进程只根据已验证的运行清单构建依赖。本模块不读取启动目录，也不根据展示名
回退查找路径，从而保证同一运行始终使用发布时冻结的输入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from generative_agents.ga_runtime.engine.world import Game
from generative_agents.ga_runtime.engine.context import SimulationContext
from generative_agents.ga_runtime.engine.collector import StepResultCollector
from generative_agents.ga_runtime.engine.results import StepResultBuilder


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
            run_objects = getattr(self.game, "run_game_object_skills", None)
            if callable(run_objects):
                for event in run_objects(
                    step_no=step_no, total_steps=target_step, stride_minutes=stride_minutes,
                    observed_facts=builder.domain_events,
                ):
                    collector.capture_event(event)
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
