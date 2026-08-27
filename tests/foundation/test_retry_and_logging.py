"""基础能力回归测试：覆盖 ``test_retry_and_logging`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from pathlib import Path

from generative_agents.modules.model.llm_model import LLMModel
from generative_agents.modules.utils.log import create_file_logger
from generative_agents.runtime.context import RunControl


class _AlwaysFailModel(LLMModel):
    """为 ``_AlwaysFailModel`` 相关场景组织共享测试状态、输入或断言。"""
    def setup(self, config):
        """构造当前测试场景所需的 ``setup`` 数据、文件或受控对象。"""
        return None

    def _completion(self, prompt, return_type, **kwargs):
        """为本测试模块封装 ``_completion`` 辅助步骤，减少重复的场景搭建代码。"""
        del prompt, return_type, kwargs
        raise RuntimeError("synthetic provider failure")


def test_model_retry_wait_stops_on_run_control_request():
    """回归验证 ``test_model_retry_wait_stops_on_run_control_request`` 所描述的业务结果、故障边界和隔离约束。"""
    control = RunControl()
    waits = []

    def request_cancel(seconds: float) -> None:
        """为本测试模块封装 ``request_cancel`` 辅助步骤，减少重复的场景搭建代码。"""
        waits.append(seconds)
        control.request_cancel()

    model = _AlwaysFailModel(
        {"model": "test", "retry_attempts": 5, "retry_backoff_seconds": 5},
        control=control,
        sleep=request_cancel,
    )

    assert model.completion("prompt", failsafe="safe") == "safe"
    assert waits == [0.1]


def test_file_loggers_with_same_basename_do_not_share_handlers(tmp_path: Path):
    """回归验证 ``test_file_loggers_with_same_basename_do_not_share_handlers`` 所描述的业务结果、故障边界和隔离约束。"""
    first_path = tmp_path / "a" / "worker.log"
    second_path = tmp_path / "b" / "worker.log"
    first = create_file_logger(
        str(first_path), run_id="run-a", attempt_no=1
    )
    second = create_file_logger(
        str(second_path), run_id="run-b", attempt_no=1
    )

    first.error("only-a")
    second.error("only-b")
    for logger in (first, second):
        for handler in logger.handlers:
            handler.flush()

    assert "only-a" in first_path.read_text(encoding="utf-8")
    assert "only-b" not in first_path.read_text(encoding="utf-8")
    assert "only-b" in second_path.read_text(encoding="utf-8")
    assert "only-a" not in second_path.read_text(encoding="utf-8")
