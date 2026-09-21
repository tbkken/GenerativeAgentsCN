"""Deterministic quality diagnostics derived from committed StepResult files."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.io import open_shared_reader


QUALITY_PROJECTION_VERSION = 2
QUALITY_PROJECTION_SOURCE = "committed-step-results"
_DETERMINISTIC_CODES = {
    "NO_WORLD_ACTION_SELECTED",
    "BRAIN_ITERATION_ROLLED_BACK",
    "BRAIN_RUNTIME_DEGRADED",
    "MCP_TOOL_ERROR",
    "REPEATED_READ_WITHOUT_PROGRESS",
    "EMPTY_MEMORY_RETRIEVAL",
    "RUN_EXECUTION_FAILED",
    "OBJECT_RUNTIME_DEGRADED",
    "OBJECT_NO_WORLD_ACTION",
}
_RUNTIME_SIGNALS = {
    "brain.fallback",
    "brain.missing_action",
    "brain.rollback",
    "loop.detected",
    "loop.budget_exhausted",
    "object.fallback",
    "object.missing_action",
}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def deterministic_quality_issues(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Apply the Brain diagnostics to audit records, retaining optional fact identity.

    In-memory Brain records have no ``source`` and keep their existing output
    shape. File-derived records carry source identity and trace indexes so that
    repeated delivery of one fact cannot erase or duplicate another call.
    """
    issues: list[dict[str, Any]] = []
    seen: dict[str, str] = {}

    def append(record: Mapping[str, Any], item: Mapping[str, Any], issue: dict[str, Any]) -> None:
        if record.get("object_key"):
            issue["object_key"] = record["object_key"]
            issue["actor_kind"] = "GAME_OBJECT"
            issue["agent_key"] = None
        origin = record.get("source")
        if isinstance(origin, Mapping):
            source = {**origin, "trace_index": item["_trace_index"]}
            issue["source"] = source
            identity = _canonical({"source": source, "code": issue["code"]})
            issue_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            issue["issue_id"] = issue_id
            content = _canonical(issue)
            if issue_id in seen:
                if seen[issue_id] != content:
                    raise PackageError("quality issue identity has conflicting facts")
                return
            seen[issue_id] = content
        issues.append(issue)

    for record in records:
        previous_reads: dict[str, dict[str, Any]] = {}
        agent_key = str(record["agent_key"])
        for signal in record.get("runtime_signals", []):
            signal_event = str(signal.get("event") or "")
            append(record, signal, {
                "code": (
                    "OBJECT_NO_WORLD_ACTION" if signal_event == "object.missing_action"
                    else "OBJECT_RUNTIME_DEGRADED" if signal_event == "object.fallback"
                    else "NO_WORLD_ACTION_SELECTED" if signal_event == "brain.missing_action"
                    else "BRAIN_ITERATION_ROLLED_BACK" if signal_event == "brain.rollback"
                    else "BRAIN_RUNTIME_DEGRADED"
                ),
                "severity": "WARNING",
                "agent_key": agent_key,
                "step_no": record["step_no"],
                "message": (
                    "对象 Skill 未提交 world-act，系统收敛为 WAIT；未发送任何回复。" if signal_event == "object.missing_action"
                    else "对象 Skill 调用失败，未提交动作和记忆已回滚。" if signal_event == "object.fallback"
                    else "Brain 未提交 world-act，系统安全收敛为 WAIT。" if signal_event == "brain.missing_action"
                    else "失败的 Brain 迭代已回滚未提交动作和记忆副作用。" if signal_event == "brain.rollback"
                    else "Brain 发生回退或循环保护，当前步行为可能偏离 SOP。"
                ),
                "evidence": {key: value for key, value in signal.items() if key != "_trace_index"},
            })
        for call in record.get("mcp_calls", []):
            if call.get("is_error"):
                append(record, call, {
                    "code": "MCP_TOOL_ERROR",
                    "severity": "WARNING",
                    "agent_key": agent_key,
                    "step_no": record["step_no"],
                    "message": f"{call.get('tool')} 调用被能力边界拒绝。",
                    "evidence": {key: value for key, value in call.items() if key != "_trace_index"},
                })
        # New IterationContext is new information. Diagnose redundant reads
        # only inside this iteration, resetting after successful writes.
        for call in record.get("mcp_calls", []):
            if call.get("is_error"):
                continue
            tool = call.get("tool")
            if tool in {"world-act", "memory-stream-append", "memory-stream-supersede", "memory-stream-invalidate"}:
                previous_reads.clear()
                continue
            if tool not in {"memory-stream-search", "world-perceive", "world-navigate"}:
                continue
            fingerprint = json.dumps(
                [tool, call.get("input"), call.get("output")], ensure_ascii=False, sort_keys=True,
            )
            previous = previous_reads.get(fingerprint)
            if previous and int(record["step_no"]) == int(previous["step_no"]):
                append(record, call, {
                    "code": "REPEATED_READ_WITHOUT_PROGRESS",
                    "severity": "WARNING",
                    "agent_key": agent_key,
                    "step_no": record["step_no"],
                    "message": (
                        f"同一轮重复调用 {tool} 并获得相同结果，"
                        "期间没有成功写入或世界动作，请检查调用是否必要。"
                    ),
                    "evidence": {"previous_step": previous["step_no"], "tool": tool, "input": call.get("input")},
                })
            previous_reads[fingerprint] = {"step_no": record["step_no"]}
            if tool == "memory-stream-search" and str(call.get("output") or "").strip() == "[]":
                append(record, call, {
                    "code": "EMPTY_MEMORY_RETRIEVAL",
                    "severity": "WARNING",
                    "agent_key": agent_key,
                    "step_no": record["step_no"],
                    "message": "Brain 请求了记忆检索，但没有召回任何记忆。",
                    "evidence": {"input": call.get("input")},
                })
    return issues


def _read_frame(root: Path, run_id: str, step_no: int) -> dict[str, Any]:
    path = root / "frames" / f"step-{step_no:06d}.json.gz"
    try:
        with open_shared_reader(path) as handle:
            document = json.loads(gzip.decompress(handle.read()).decode("utf-8"))
    except (OSError, UnicodeError, ValueError, EOFError) as exc:
        raise PackageError(f"cannot read committed quality frame at Step {step_no}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise PackageError(f"unsupported quality frame schema at Step {step_no}")
    result = document.get("result")
    if not isinstance(result, dict):
        raise PackageError(f"missing StepResult at Step {step_no}")
    if result.get("run_id") != run_id or result.get("step_no") != step_no:
        raise PackageError(f"quality frame identity mismatch at Step {step_no}")
    if not isinstance(result.get("attempt_id"), str) or not result["attempt_id"]:
        raise PackageError(f"quality frame has no Attempt identity at Step {step_no}")
    if not isinstance(result.get("effects"), list):
        raise PackageError(f"quality frame effects are invalid at Step {step_no}")
    return result


def _audit_records(root: Path, run_id: str, committed_step: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_effects: dict[tuple[str, str, int, str], str] = {}
    for step_no in range(1, committed_step + 1):
        result = _read_frame(root, run_id, step_no)
        for effect in result["effects"]:
            if not isinstance(effect, dict):
                raise PackageError(f"invalid quality effect at Step {step_no}")
            payload = effect.get("payload")
            if effect.get("kind") != "SKILL_EXECUTED" or not isinstance(payload, dict):
                continue
            if payload.get("execution_source") not in {"BRAIN_RUNTIME", "OBJECT_SKILL_RUNTIME"}:
                continue
            effect_id = effect.get("effect_id")
            agent_keys = effect.get("agent_keys")
            is_object = payload.get("execution_source") == "OBJECT_SKILL_RUNTIME"
            object_key = payload.get("object_key") if is_object else None
            if is_object and (not isinstance(object_key, str) or not object_key):
                raise PackageError(f"object quality fact is missing its identity at Step {step_no}")
            if object_key:
                agent_keys = [f"game-object:{object_key}"]
            trace = payload.get("trace")
            if (
                not isinstance(effect_id, str) or not effect_id
                or not isinstance(agent_keys, list) or not agent_keys
                or any(not isinstance(key, str) or not key for key in agent_keys)
                or not isinstance(trace, list) or any(not isinstance(item, dict) for item in trace)
            ):
                raise PackageError(f"invalid Brain quality fact at Step {step_no}")
            identity = (run_id, result["attempt_id"], step_no, effect_id)
            content = _canonical(effect)
            if identity in seen_effects:
                if seen_effects[identity] != content:
                    raise PackageError(f"conflicting quality effect identity at Step {step_no}")
                continue
            seen_effects[identity] = content
            for agent_key in dict.fromkeys(agent_keys):
                records.append({
                    "agent_key": agent_key,
                    **({"object_key": object_key} if object_key else {}),
                    "step_no": step_no,
                    "source": {
                        "run_id": run_id,
                        "attempt_id": result["attempt_id"],
                        "step_no": step_no,
                        "agent_key": agent_key,
                        **({"object_key": object_key} if object_key else {}),
                        "effect_id": effect_id,
                    },
                    "mcp_calls": [
                        {
                            "skill": item.get("skill"),
                            "tool": item.get("tool"),
                            "input": item.get("input_text"),
                            "output": item.get("output_text"),
                            "is_error": bool(item.get("is_error", False)),
                            "_trace_index": index,
                        }
                        for index, item in enumerate(trace) if item.get("event") == "mcp.call"
                    ],
                    "runtime_signals": [
                        {**item, "_trace_index": index}
                        for index, item in enumerate(trace) if item.get("event") in _RUNTIME_SIGNALS
                    ],
                })
    return records


def _previous_evaluator(previous: dict | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Keep explicit evaluator findings, never stale deterministic diagnostics."""
    skipped = {"status": "SKIPPED", "error": None}
    if not isinstance(previous, dict) or not isinstance(previous.get("evaluator"), dict):
        return skipped, []
    evaluator = copy.deepcopy(previous["evaluator"])
    if evaluator.get("status") == "SKIPPED":
        return skipped, []
    explicit = evaluator.pop("issues", None)
    # The nested list already contains the preserved findings after projection.
    # Prefer that explicit list rather than combining two views of it, and do
    # not merge separate evaluator findings merely because their text matches.
    candidates = explicit if isinstance(explicit, list) else [
        item for item in (previous.get("issues") or [])
        if isinstance(item, dict) and item.get("code") == "BRAIN_CONFORMANCE_DEVIATION"
    ]
    issues: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict) or item.get("code") in _DETERMINISTIC_CODES:
            continue
        issues.append(copy.deepcopy(item))
    # Keep the explicit findings with their evaluator on subsequent projections.
    if issues:
        evaluator["issues"] = copy.deepcopy(issues)
    return evaluator, issues


def project_run_quality(
    run_root: Path,
    *,
    run_id: str,
    committed_step: int,
    brain_skill: str,
    evaluated_at: str,
    previous_report: dict | None = None,
) -> dict[str, Any]:
    """Return a stable full-Run report without writing files or invoking models."""
    if not isinstance(run_id, str) or not run_id:
        raise PackageError("quality projection requires a Run identity")
    if type(committed_step) is not int or committed_step < 0:
        raise PackageError("quality projection requires a nonnegative committed Step")
    records = _audit_records(Path(run_root), run_id, committed_step)
    evaluator, evaluator_issues = _previous_evaluator(previous_report)
    issues = [*deterministic_quality_issues(records), *evaluator_issues]
    evaluated = {(run_id, item["step_no"], item["agent_key"]) for item in records if not item.get("object_key")}
    evaluated_objects = {(run_id, item["step_no"], item["object_key"]) for item in records if item.get("object_key")}
    status = (
        "WARNING" if issues else "NOT_EVALUATED" if evaluator.get("status") == "SKIPPED"
        else "PASS" if evaluator.get("status") == "PASS" else "UNKNOWN"
    )
    return {
        "schema_version": 1,
        "projection_version": QUALITY_PROJECTION_VERSION,
        "source": QUALITY_PROJECTION_SOURCE,
        "source_run_id": run_id,
        "source_committed_step": committed_step,
        "quality_status": status,
        "execution_status_affected": False,
        "brain_skill": brain_skill,
        "evaluated_at": evaluated_at,
        "evaluated_agent_steps": len(evaluated),
        "evaluated_object_steps": len(evaluated_objects),
        "summary": (
            "发现需要观察的行为偏差" if issues
            else "基础诊断未发现告警；本次未执行业务评估。" if status == "NOT_EVALUATED"
            else evaluator.get("summary") or (previous_report or {}).get("summary") or "质量评估不可用"
        ),
        "issues": issues,
        "evaluator": evaluator,
    }
