"""Trial preparation, the model gateway, and verbatim child results agree."""

import copy
import json

import pytest

from generative_agents.ga_runtime.models.gateway import LLMModel
from generative_agents.ga_runtime.skills.trial import execute_skill_trial
from generative_agents.ga_studio.resources.trials import prepare_copied_trial


def call(name, arguments, call_id):
    return {"content": "", "tool_calls": [{"id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


@pytest.mark.parametrize("continue_to_second", [False, True])
def test_trial_passes_raw_result_only_when_parent_requests_next_skill(
    tmp_path, monkeypatch, continue_to_second,
):
    snapshots = {
        name: {"kind": kind, "scripts": {}, "markdown":
               f"---\nname: {name}\ndescription: {name}\n---\n{body}"}
        for name, kind, body in (
            ("handoff", "brain", "按任务决定调用 $observe 或 $describe，完成后停止。"),
            ("observe", "skill", "保留已知事实与未确认事项。"),
            ("describe", "skill", "根据收到的原文回答，保留不确定性。"),
        )
    }
    source_input = "  林晨准备出门，但还没有确认目的地。\n请保留不确定性。\n"
    source_output = "  已确认：还在家。\n\n未确认：是否出门；门牌为 `001`。\n"
    config = {"provider": "vllm", "base_url": "http://unused/v1", "model": "test",
              "retry_attempts": 1}
    root = prepare_copied_trial(snapshots, "handoff", source_input,
        destination=tmp_path / "trial", config=config, base_url=config["base_url"], model="test")
    responses = [call("call_skill", {"name": "observe", "input_text": source_input}, "observed"),
                 {"content": source_output}]
    if continue_to_second:
        responses.extend([
            call("call_skill", {"name": "describe", "input_from": "observed"}, "described"),
            {"content": "收到原文，目的地仍未确认。"},
        ])
    responses.append({"content": "本次任务结束。"})
    answers, requests = iter(responses), []

    def request(self, method, url, *, headers=None, payload=None):
        requests.append(copy.deepcopy(payload))
        return {"choices": [{"message": next(answers)}]}

    monkeypatch.setattr(LLMModel, "_request_json", request)
    result = execute_skill_trial(root)

    expected_calls = 5 if continue_to_second else 3
    assert len(requests) == expected_calls
    assert requests[0]["messages"][-1]["content"] == source_input
    assert requests[1]["messages"][-1]["content"] == source_input
    assert requests[2]["messages"][-1]["content"] == source_output
    assert result["input_text"] == source_input
    assert result["output_text"] == "本次任务结束。"
    assert result["model_usage"] == {
        "logical_calls": expected_calls, "physical_attempts": expected_calls,
    }
    assert not any(item["event"] == "trial.context" for item in result["trace"])
    assert result["world_commit"] == "NOT_COMMITTED"
    children = [item for item in result["trace"] if item["event"] == "skill.call"]
    assert [item["child"] for item in children] == (
        ["observe", "describe"] if continue_to_second else ["observe"]
    )
    if continue_to_second:
        assert requests[3]["messages"][-1]["content"] == source_output
        assert children[-1]["input_from"] == "observed"
        assert children[-1]["input_text"] == source_output
