"""Durable, uncommitted actor decisions for a Step with online physical effects."""
from __future__ import annotations

from generative_agents.ga_protocol.packages.io import atomic_write_json, checked_package_path, read_json


class SceneIntents:
    def __init__(self, context):
        self.context = context
        self.path = checked_package_path(context.paths.root / "simworld-pending-step.json")

    def for_step(self, step):
        if self.path.is_file():
            saved = read_json(self.path)
            if saved.get("run_id") != str(self.context.run_id) or saved["step"] > step:
                raise ValueError("SimWorld 待确认动作不属于当前 Run 或 Step")
            if saved["step"] == step:
                return saved
        return {"run_id": str(self.context.run_id), "step": step, "actors": {}}

    def decide(self, game, agent_key, step, decide):
        transaction = self.for_step(step)
        decisions = transaction["actors"]
        stream = self.context.memory_stream
        if agent_key in decisions:
            previous = decisions[agent_key]
            if stream is not None:
                stream.rollback_iteration(previous["memory"])
            game.consume_external_observations(agent_key)
            outcome = previous["outcome"]
            iteration = outcome["info"]["iteration_context"]
            iteration["attempt_id"] = str(self.context.attempt_id)
            return outcome
        outcome = decide()
        decisions[agent_key] = {"outcome": outcome,
                                "memory": stream.begin_iteration(agent_key) if stream is not None else None}
        # The same decision is replayed after a lost reply, even if the Brain is nondeterministic.
        atomic_write_json(self.path, transaction)
        return outcome

    def committed(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            self.context.logger.warning("已提交的 SimWorld 待确认文件暂未清理；下步将按提交边界替换")
