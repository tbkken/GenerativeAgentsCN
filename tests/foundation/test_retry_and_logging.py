"""基础能力回归测试：覆盖 ``test_retry_and_logging`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import requests

from generative_agents.modules.model.llm_model import LLMModel
from generative_agents.modules.utils.log import create_file_logger
from generative_agents.runtime.context import RunControl
from generative_agents.runtime.model_trace import ModelTraceEventType


class _AlwaysFailModel(LLMModel):
    """为 ``_AlwaysFailModel`` 相关场景组织共享测试状态、输入或断言。"""
    def setup(self, config):
        """构造当前测试场景所需的 ``setup`` 数据、文件或受控对象。"""
        return None

    def _completion(self, prompt, return_type, **kwargs):
        """为本测试模块封装 ``_completion`` 辅助步骤，减少重复的场景搭建代码。"""
        del prompt, return_type, kwargs
        raise RuntimeError("synthetic provider failure")


class _ChatTransportModel(LLMModel):
    def setup(self, config):
        return None


class _TraceRecorder:
    def __init__(self):
        self.run_id = uuid4()
        self.attempt_id = uuid4()
        self.events = []

    def append(self, event):
        self.events.append(event)


class _ChatResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


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


def test_chat_gateway_retries_timeout_and_malformed_tool_json(monkeypatch):
    recorder = _TraceRecorder()
    payloads = []
    running_facts_seen_by_transport = []
    outcomes = [
        requests.Timeout("temporary timeout"),
        _ChatResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "bad",
                                    "function": {
                                        "name": "act",
                                        "arguments": '{"target":',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ),
        _ChatResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "good",
                                    "function": {
                                        "name": "act",
                                        "arguments": '{"target":"door"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 4,
                    "total_tokens": 12,
                },
            }
        ),
    ]

    def fake_post(_url, **kwargs):
        payloads.append(kwargs["json"])
        running_facts_seen_by_transport.append(
            (
                recorder.events[-1].event_type,
                recorder.events[-1].status.value,
            )
        )
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(
        "generative_agents.modules.model.llm_model.requests.post", fake_post
    )
    model = _ChatTransportModel(
        {
            "base_url": "http://127.0.0.1:8001/v1",
            "model": "local-test",
            "enable_thinking": False,
            "retry_attempts": 3,
            "retry_backoff_seconds": 0,
        },
        recorder=recorder,
    )

    result = model.chat_completion(
        [{"role": "user", "content": "open the door"}],
        tools=[{"type": "function", "function": {"name": "act"}}],
    )

    assert result["tool_calls"][0]["id"] == "good"
    assert len(payloads) == 3
    assert all(
        payload["chat_template_kwargs"] == {"enable_thinking": False}
        for payload in payloads
    )
    assert any(
        "invalid JSON" in message.get("content", "")
        for message in payloads[2]["messages"]
    )
    assert running_facts_seen_by_transport == [
        (ModelTraceEventType.PHYSICAL_START, "RUNNING"),
        (ModelTraceEventType.PHYSICAL_START, "RUNNING"),
        (ModelTraceEventType.PHYSICAL_START, "RUNNING"),
    ]
    assert [event.event_type for event in recorder.events] == [
        ModelTraceEventType.PHYSICAL_START,
        ModelTraceEventType.PHYSICAL_ATTEMPT,
        ModelTraceEventType.PHYSICAL_START,
        ModelTraceEventType.PHYSICAL_ATTEMPT,
        ModelTraceEventType.PHYSICAL_START,
        ModelTraceEventType.PHYSICAL_ATTEMPT,
        ModelTraceEventType.LOGICAL_END,
    ]
    assert recorder.events[-1].status.value == "SUCCEEDED"


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
