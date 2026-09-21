from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.facts.quality import deterministic_quality_issues
from generative_agents.ga_protocol.facts.quality import project_run_quality


def _call(tool="world-navigate", *, error=True, output="blocked", request="{}"):
    return {
        "event": "mcp.call",
        "skill": "brain",
        "tool": tool,
        "input_text": request,
        "output_text": output,
        "is_error": error,
    }


def _effect(effect_id, trace, *, agent="agent-a", source="BRAIN_RUNTIME"):
    return {
        "kind": "SKILL_EXECUTED",
        "effect_id": effect_id,
        "agent_keys": [agent],
        "payload": {"execution_source": source, "trace": trace},
    }


def _frame(root: Path, step: int, effects: list, *, attempt="attempt-a", run="run-a"):
    path = root / "frames" / f"step-{step:06d}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    result = {"run_id": run, "attempt_id": attempt, "step_no": step, "effects": effects}
    path.write_bytes(gzip.compress(json.dumps({"schema_version": 1, "result": result}).encode(), mtime=0))
    return path


def _project(root: Path, step: int, previous=None):
    return project_run_quality(
        root, run_id="run-a", committed_step=step, brain_skill="brain",
        evaluated_at="2026-09-09T04:00:00Z", previous_report=previous,
    )


def test_object_diagnostics_keep_object_identity_and_separate_iteration_counts(tmp_path):
    effect = _effect("object-effect", [{"event": "object.missing_action"}], source="OBJECT_SKILL_RUNTIME")
    effect["agent_keys"] = []
    effect["payload"]["object_key"] = "camera"
    _frame(tmp_path, 1, [effect])
    report = _project(tmp_path, 1)
    assert report["evaluated_agent_steps"] == 0
    assert report["evaluated_object_steps"] == 1
    assert report["issues"][0]["object_key"] == "camera"
    assert report["issues"][0]["agent_key"] is None
    assert report["issues"][0]["code"] == "OBJECT_NO_WORLD_ACTION"


def test_projection_includes_both_attempts_and_replaces_old_deterministic_items(tmp_path):
    _frame(tmp_path, 1, [_effect("effect-1", [_call("memory-stream-search", error=False, output="[]")])])
    _frame(tmp_path, 2, [_effect("effect-2", [_call(), _call("world-act")])])
    _frame(tmp_path, 3, [_effect("effect-3", [_call()])], attempt="attempt-b")
    _frame(tmp_path, 4, [_effect("effect-4", [_call()])], attempt="attempt-b")
    previous = {"evaluator": {"status": "SKIPPED", "error": None}, "issues": [
        {"code": "MCP_TOOL_ERROR", "step_no": 999, "message": "obsolete"},
    ]}
    before = copy.deepcopy(previous)
    report = _project(tmp_path, 4, previous)
    assert report["evaluated_agent_steps"] == 4
    assert [issue["step_no"] for issue in report["issues"]] == [1, 2, 2, 3, 4]
    assert [issue["code"] for issue in report["issues"]] == ["EMPTY_MEMORY_RETRIEVAL", *["MCP_TOOL_ERROR"] * 4]
    assert {issue["source"]["attempt_id"] for issue in report["issues"]} == {"attempt-a", "attempt-b"}
    assert len({issue["issue_id"] for issue in report["issues"]}) == 5
    assert report["projection_version"] == 2
    assert report["source"] == "committed-step-results"
    assert report["source_committed_step"] == 4
    assert report["source_run_id"] == "run-a"
    assert report["execution_status_affected"] is False
    assert report["quality_status"] == "WARNING"
    assert previous == before
    assert _project(tmp_path, 4, report) == report


def test_uncommitted_tail_is_not_read_or_counted(tmp_path):
    _frame(tmp_path, 1, [_effect("effect-1", [_call("world-act", error=False)])])
    _frame(tmp_path, 2, [_effect("effect-2", [_call()])], run="foreign-run")
    report = _project(tmp_path, 1)
    assert report["evaluated_agent_steps"] == 1
    assert report["issues"] == []
    assert report["quality_status"] == "NOT_EVALUATED"


def test_duplicate_effects_deduplicate_but_identical_calls_and_other_agents_do_not(tmp_path):
    effect = _effect("same-effect", [_call(), _call(), _call("world-act")])
    _frame(tmp_path, 1, [effect, copy.deepcopy(effect), _effect("other-agent", [_call()], agent="agent-b")])
    _frame(tmp_path, 2, [_effect("same-effect", [_call()])])
    report = _project(tmp_path, 2)
    assert report["evaluated_agent_steps"] == 3
    assert len(report["issues"]) == 5
    assert len({issue["issue_id"] for issue in report["issues"]}) == 5
    assert [issue["source"]["trace_index"] for issue in report["issues"][:3]] == [0, 1, 2]
    assert report["issues"][0]["source"] == {
        "run_id": "run-a", "attempt_id": "attempt-a", "step_no": 1,
        "agent_key": "agent-a", "effect_id": "same-effect", "trace_index": 0,
    }
    assert "_trace_index" not in report["issues"][0]["evidence"]


def test_passive_skill_trace_is_not_counted_again(tmp_path):
    _frame(tmp_path, 1, [
        _effect("brain", [_call()]),
        _effect("passive", [_call()], source="GAME_OBJECT_SKILL"),
    ])
    assert len(_project(tmp_path, 1)["issues"]) == 1


def test_same_effect_identity_with_conflicting_content_is_rejected(tmp_path):
    _frame(tmp_path, 1, [_effect("same", [_call()]), _effect("same", [_call("world-act")])])
    with pytest.raises(PackageError, match="conflicting quality effect identity"):
        _project(tmp_path, 1)


@pytest.mark.parametrize("fault", ["run", "step", "attempt", "schema", "missing"])
def test_projection_rejects_invalid_committed_frame_identity(tmp_path, fault):
    path = _frame(tmp_path, 1, [_effect("effect-1", [_call()])])
    document = json.loads(gzip.decompress(path.read_bytes()))
    if fault == "run":
        document["result"]["run_id"] = "foreign-run"
    elif fault == "step":
        document["result"]["step_no"] = 2
    elif fault == "attempt":
        document["result"]["attempt_id"] = ""
    elif fault == "schema":
        document["schema_version"] = 999
    else:
        path.unlink()
    if fault != "missing":
        path.write_bytes(gzip.compress(json.dumps(document).encode()))
    with pytest.raises(PackageError):
        _project(tmp_path, 1)


def test_legacy_records_preserve_fields_and_read_diagnostics_reset_on_successful_write():
    read = {"tool": "memory-stream-search", "input": "query", "output": "[]", "is_error": False}
    write = {"tool": "memory-stream-append", "input": "note", "output": "ok", "is_error": False}
    record = {"agent_key": "agent-a", "step_no": 1, "runtime_signals": [], "mcp_calls": [read, read, write, read]}
    issues = deterministic_quality_issues([record, {**record, "step_no": 2, "mcp_calls": [read]}])
    assert [issue["code"] for issue in issues] == [
        "EMPTY_MEMORY_RETRIEVAL", "REPEATED_READ_WITHOUT_PROGRESS", "EMPTY_MEMORY_RETRIEVAL",
        "EMPTY_MEMORY_RETRIEVAL", "EMPTY_MEMORY_RETRIEVAL",
    ]
    assert issues[0] == {
        "code": "EMPTY_MEMORY_RETRIEVAL", "severity": "WARNING", "agent_key": "agent-a",
        "step_no": 1, "message": "Brain 请求了记忆检索，但没有召回任何记忆。", "evidence": {"input": "query"},
    }
    assert all("source" not in item and "issue_id" not in item for item in issues)


def test_runtime_signals_keep_trace_identity_and_do_not_change_execution_status(tmp_path):
    signals = [{"event": event} for event in ["brain.missing_action", "brain.rollback", "loop.detected"]]
    _frame(tmp_path, 1, [_effect("effect-1", signals)])
    report = _project(tmp_path, 1)
    assert [issue["code"] for issue in report["issues"]] == [
        "NO_WORLD_ACTION_SELECTED", "BRAIN_ITERATION_ROLLED_BACK", "BRAIN_RUNTIME_DEGRADED",
    ]
    assert [issue["source"]["trace_index"] for issue in report["issues"]] == [0, 1, 2]
    assert report["execution_status_affected"] is False


def test_explicit_evaluator_findings_survive_without_merging_different_steps(tmp_path):
    _frame(tmp_path, 1, [_effect("effect-1", [_call("world-act", error=False)])])
    findings = [{"code": "BRAIN_CONFORMANCE_DEVIATION", "step_no": step, "message": "same text"} for step in (1, 2)]
    previous = {"evaluator": {"status": "WARNING", "error": None}, "issues": [
        *findings, {"code": "MCP_TOOL_ERROR", "step_no": 99}, {"code": "UNKNOWN_STALE_CODE"},
    ]}
    report = _project(tmp_path, 1, previous)
    assert report["issues"] == findings
    assert report["evaluator"]["status"] == "WARNING"
    assert _project(tmp_path, 1, report) == report


def test_empty_committed_boundary_needs_no_frames(tmp_path):
    report = _project(tmp_path, 0)
    assert report["issues"] == []
    assert report["evaluated_agent_steps"] == 0
    assert report["evaluated_at"] == "2026-09-09T04:00:00Z"
