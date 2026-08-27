"""Cursor-based, idempotent projection of append-only model call JSONL facts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    Run,
    RunAttempt,
    RunModelTraceCursor,
    RunModelUsage,
    RunResultSummary,
    RunStep,
)

from .context import RunPaths
from .model_trace import ModelTraceStatus


class ModelTraceProjectionError(RuntimeError):
    """模型追踪文件身份、游标或事件结构不满足投影契约。"""

    pass


class ModelTraceProjector:
    """按字节游标把 Attempt 模型追踪追加投影到数据库。"""

    def __init__(self, database: Database, *, var_dir: str | Path):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。
            var_dir: 运行时可变数据根目录，用于保存数据库、帧、检查点和产物。 类型：`str | Path`。

        返回:
            无返回值。
        """
        self._database = database
        self._var_dir = Path(var_dir).resolve()

    def project(
        self,
        *,
        run_id: str,
        attempt_id: str,
        relative_path: str,
    ) -> int:
        """执行 `ModelTraceProjector` 的`project`操作。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。
            attempt_id: 执行尝试的唯一标识，用于区分同一运行的重试或恢复批次。 类型：`str`。
            relative_path: `relative`对应的文件系统路径。 类型：`str`。

        返回:
            返回计算得到的整数值或版本号。

        异常:
            FileNotFoundError: 当所需文件或目录不存在时抛出。
            ModelTraceProjectionError: 当底层操作报告该异常条件时抛出。
        """
        paths = RunPaths.under(self._var_dir, UUID(run_id))
        trace_path = (self._var_dir / relative_path).resolve()
        if not trace_path.is_relative_to(paths.traces.resolve()):
            raise ModelTraceProjectionError(
                "model trace path is outside the run trace directory"
            )
        with self._database.session_factory() as read_session:
            if read_session.get(Run, run_id) is None:
                raise ModelTraceProjectionError("run does not exist")
            attempt = read_session.get(RunAttempt, attempt_id)
            if attempt is None or attempt.run_id != run_id:
                raise ModelTraceProjectionError(
                    "model trace attempt does not own this run"
                )
            attempt_start_step = attempt.start_step
            current = read_session.get(RunModelTraceCursor, (run_id, attempt_id))
            byte_offset = current.byte_offset if current else 0
            last_event_seq = current.last_event_seq if current else 0
        if not trace_path.is_file():
            # ModelTraceWriter creates JSONL lazily on the first append. A
            # failure before the first model call is a legitimate zero-trace
            # attempt. A previously projected file disappearing is not.
            if current is None:
                return 0
            raise FileNotFoundError(trace_path)

        records, next_offset = self._read_complete_records(trace_path, byte_offset)
        # RunStep is the bounded source used by timeline and artifact reports.
        # Recompute exact per-step totals from this attempt's complete trace so
        # retrying projection cannot double-count and resumed attempts remain
        # isolated from the steps committed by earlier attempts.
        complete_records, _complete_offset = self._read_complete_records(trace_path, 0)
        step_totals: dict[int, list[int]] = {}
        for record in complete_records:
            # Older Skill-brain calls did not attach a step number.  They were
            # emitted while an attempt was preparing its first committed step,
            # so the durable attempt boundary is the only safe attribution.
            step_no = record.get("step_no") or attempt_start_step
            if not isinstance(step_no, int) or isinstance(step_no, bool) or step_no < 1:
                continue
            totals = step_totals.setdefault(step_no, [0, 0])
            if record.get("event_type") == "LOGICAL_END":
                totals[0] += 1
            elif (
                record.get("event_type") == "PHYSICAL_ATTEMPT"
                and (record.get("attempt_no") or 1) > 1
            ):
                totals[1] += 1
        if not records:
            self._reconcile_step_totals(
                run_id=run_id,
                attempt_id=attempt_id,
                step_totals=step_totals,
            )
            return last_event_seq
        expected_sequence = last_event_seq
        for record in records:
            if record.get("run_id") != run_id or record.get("attempt_id") != attempt_id:
                raise ModelTraceProjectionError("model trace record scope mismatch")
            if record.get("event_seq") != expected_sequence + 1:
                raise ModelTraceProjectionError(
                    "model trace event sequence is not contiguous"
                )
            expected_sequence += 1

        with self._database.session_factory.begin() as session:
            cursor = session.get(RunModelTraceCursor, (run_id, attempt_id))
            if cursor is None:
                cursor = RunModelTraceCursor(
                    run_id=run_id,
                    attempt_id=attempt_id,
                    relative_path=relative_path,
                    last_event_seq=0,
                    byte_offset=0,
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(cursor)
                session.flush()
            if (
                cursor.last_event_seq != last_event_seq
                or cursor.byte_offset != byte_offset
            ):
                # Another projector won the race. Re-read from its boundary.
                raise ModelTraceProjectionError(
                    "model trace cursor changed concurrently"
                )
            logical_calls = 0
            retries = 0
            for record in records:
                self._apply_record(session, run_id, record)
                if record.get("event_type") == "LOGICAL_END":
                    logical_calls += 1
                elif (
                    record.get("event_type") == "PHYSICAL_ATTEMPT"
                    and (record.get("attempt_no") or 1) > 1
                ):
                    retries += 1
            cursor.last_event_seq = expected_sequence
            cursor.byte_offset = next_offset
            cursor.updated_at = datetime.now(timezone.utc)
            summary = session.get(RunResultSummary, run_id)
            matched_step = False
            for step_no, (step_calls, step_retries) in step_totals.items():
                step = session.get(RunStep, (run_id, step_no))
                if step is None or step.attempt_id != attempt_id:
                    continue
                step.model_logical_calls = step_calls
                step.model_retry_count = step_retries
                matched_step = True
            if summary is not None:
                if matched_step:
                    session.flush()
                    self._synchronize_summary_from_steps(session, run_id, summary)
                else:
                    summary.model_call_count += logical_calls
                    summary.model_retry_count += retries
                summary.result_version += 1
                summary.updated_at = datetime.now(timezone.utc)
            return expected_sequence

    def _reconcile_step_totals(
        self,
        *,
        run_id: str,
        attempt_id: str,
        step_totals: dict[int, list[int]],
    ) -> None:
        """执行`reconcile`仿真步`totals`的内部处理，供当前模块或类复用。

        参数:
            run_id: 仿真运行的唯一标识。 类型：`str`。
            attempt_id: 执行尝试的唯一标识，用于区分同一运行的重试或恢复批次。 类型：`str`。
            step_totals: 按仿真步汇总的模型调用次数与令牌用量。 类型：`dict[int, list[int]]`。

        返回:
            无返回值。
        """
        with self._database.session_factory.begin() as session:
            matched_step = False
            for step_no, (step_calls, step_retries) in step_totals.items():
                step = session.get(RunStep, (run_id, step_no))
                if step is None or step.attempt_id != attempt_id:
                    continue
                step.model_logical_calls = step_calls
                step.model_retry_count = step_retries
                matched_step = True
            summary = session.get(RunResultSummary, run_id)
            if matched_step and summary is not None:
                session.flush()
                self._synchronize_summary_from_steps(session, run_id, summary)

    @staticmethod
    def _synchronize_summary_from_steps(session, run_id: str, summary) -> None:
        """执行`synchronize`摘要`from``steps`的内部处理，供当前模块或类复用。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。
            run_id: 仿真运行的唯一标识。 类型：`str`。
            summary: 当前运行、步骤或模型调用的聚合摘要。

        返回:
            无返回值。
        """
        logical_calls, retries = session.execute(
            select(
                func.coalesce(func.sum(RunStep.model_logical_calls), 0),
                func.coalesce(func.sum(RunStep.model_retry_count), 0),
            ).where(RunStep.run_id == run_id)
        ).one()
        summary.model_call_count = int(logical_calls)
        summary.model_retry_count = int(retries)

    @staticmethod
    def _read_complete_records(path: Path, offset: int) -> tuple[list[dict], int]:
        """读取`complete``records`。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。
            offset: 从结果集或字节流起点跳过的数量。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ModelTraceProjectionError: 当底层操作报告该异常条件时抛出。
        """
        records: list[dict] = []
        consumed = 0
        with path.open("rb") as file_handle:
            file_handle.seek(offset)
            for raw_line in file_handle:
                if not raw_line.endswith(b"\n"):
                    break
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise ModelTraceProjectionError(
                        "invalid complete JSONL record"
                    ) from exc
                records.append(record)
                consumed += len(raw_line)
        return records, offset + consumed

    @staticmethod
    def _apply_record(session, run_id: str, record: dict) -> None:
        """应用`record`。

        参数:
            session: 当前数据库会话；事务提交与回滚由调用边界约定。
            run_id: 仿真运行的唯一标识。 类型：`str`。
            record: 当前读取、校验、投影或序列化的持久化记录。 类型：`dict`。

        返回:
            无返回值。

        异常:
            ModelTraceProjectionError: 当底层操作报告该异常条件时抛出。
        """
        key = (
            run_id,
            str(record.get("purpose") or "unknown"),
            str(record.get("provider") or "unknown"),
            str(record.get("resolved_model") or "unknown"),
        )
        usage = session.get(RunModelUsage, key)
        if usage is None:
            usage = RunModelUsage(
                run_id=key[0],
                purpose=key[1],
                provider=key[2],
                resolved_model=key[3],
                logical_call_count=0,
                successful_call_count=0,
                fallback_count=0,
                physical_attempt_count=0,
                retry_count=0,
                input_tokens=None,
                output_tokens=None,
                latency_buckets_json={
                    "lt_100": 0,
                    "lt_500": 0,
                    "lt_1000": 0,
                    "lt_5000": 0,
                    "gte_5000": 0,
                },
                max_latency_ms=0,
                updated_step=record.get("step_no"),
            )
            session.add(usage)
            session.flush()
        event_type = record.get("event_type")
        if event_type == "PHYSICAL_ATTEMPT":
            usage.physical_attempt_count += 1
            if (record.get("attempt_no") or 1) > 1:
                usage.retry_count += 1
            latency = int(record.get("latency_ms") or 0)
            buckets = dict(usage.latency_buckets_json)
            buckets[ModelTraceProjector._latency_bucket(latency)] += 1
            usage.latency_buckets_json = buckets
            usage.max_latency_ms = max(usage.max_latency_ms, latency)
            usage.input_tokens = ModelTraceProjector._add_nullable(
                usage.input_tokens, record.get("prompt_tokens")
            )
            usage.output_tokens = ModelTraceProjector._add_nullable(
                usage.output_tokens, record.get("completion_tokens")
            )
        elif event_type == "LOGICAL_END":
            usage.logical_call_count += 1
            if record.get("status") == ModelTraceStatus.SUCCEEDED:
                usage.successful_call_count += 1
            elif record.get("status") == "FALLBACK":
                usage.fallback_count += 1
        else:
            raise ModelTraceProjectionError(
                f"unknown model trace event type: {event_type}"
            )
        usage.updated_step = record.get("step_no") or usage.updated_step

    @staticmethod
    def _latency_bucket(latency_ms: int) -> str:
        """执行`latency``bucket`的内部处理，供当前模块或类复用。

        参数:
            latency_ms: 模型探测或调用从开始到结束的耗时毫秒数。 类型：`int`。

        返回:
            返回处理后的文本或稳定标识。
        """
        if latency_ms < 100:
            return "lt_100"
        if latency_ms < 500:
            return "lt_500"
        if latency_ms < 1000:
            return "lt_1000"
        if latency_ms < 5000:
            return "lt_5000"
        return "gte_5000"

    @staticmethod
    def _add_nullable(current: int | None, increment: int | None) -> int | None:
        """执行`add``nullable`的内部处理，供当前模块或类复用。

        参数:
            current: 更新前的当前值，用于计算增量或状态迁移。 类型：`int | None`。
            increment: 需要累加到当前计数或用量上的增量。 类型：`int | None`。

        返回:
            返回计算得到的整数值或版本号。 没有可用结果时返回 `None`。
        """
        if increment is None:
            return current
        return (current or 0) + int(increment)
