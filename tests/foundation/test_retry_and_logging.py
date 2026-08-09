from __future__ import annotations

from pathlib import Path

from generative_agents.modules.model.llm_model import LLMModel
from generative_agents.modules.utils.log import create_file_logger
from generative_agents.runtime.context import RunControl


class _AlwaysFailModel(LLMModel):
    def setup(self, config):
        return None

    def _completion(self, prompt, return_type, **kwargs):
        del prompt, return_type, kwargs
        raise RuntimeError("synthetic provider failure")


def test_model_retry_wait_stops_on_run_control_request():
    control = RunControl()
    waits = []

    def request_cancel(seconds: float) -> None:
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
