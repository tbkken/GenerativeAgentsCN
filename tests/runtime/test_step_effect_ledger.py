"""运行时回归测试：覆盖 ``test_step_effect_ledger`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from generative_agents.runtime.results import (
    MemoryDelta,
    MemoryDeltaKind,
    StepEffectKind,
    StepResult,
    StepResultBuilder,
    deterministic_record_id,
)
from generative_agents.runtime.result_collector import StepResultCollector
from generative_agents.skills import MemoryStream


def test_step_result_persists_one_canonical_effect_ledger():
    """回归验证 ``test_step_result_persists_one_canonical_effect_ledger`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id = uuid4()
    attempt_id = uuid4()
    now = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
    builder = StepResultBuilder(
        run_id=run_id,
        attempt_id=attempt_id,
        step_no=1,
        virtual_time=now,
    )
    builder.add_memory_delta(
        MemoryDelta(
            event_id=deterministic_record_id(run_id, 1, "memory", "1"),
            sequence=1,
            agent_key="resident-001",
            memory_id="memory-1",
            kind=MemoryDeltaKind.CREATED,
            memory_type="THOUGHT",
            description="The crossing is unsafe when the light is red.",
            poignancy=8,
            subject="resident-001",
            predicate="observed",
            object="red-light",
            address=("town", "crossing"),
            created_at=now,
            expires_at=now + timedelta(days=30),
            evidence_memory_ids=("event-1",),
        )
    )

    result = builder.freeze()

    assert {effect.kind for effect in result.effects} == {
        StepEffectKind.MEMORY_CREATED,
        StepEffectKind.REFLECTION_CREATED,
    }
    restored = StepResult.from_dict(result.to_dict())
    assert restored == result
    memory_effect = next(
        effect for effect in restored.effects if effect.kind == StepEffectKind.MEMORY_CREATED
    )
    assert memory_effect.payload["evidence_memory_ids"] == ["event-1"]
    assert memory_effect.payload["created_at"] == now.isoformat()


def test_run_memory_is_attempt_recoverable_and_run_isolated(tmp_path):
    """回归验证 ``test_run_memory_is_attempt_recoverable_and_run_isolated`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id = uuid4()
    attempt_one = uuid4()
    now = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
    first_path = tmp_path / "attempt-1" / "memory.sqlite"
    first = MemoryStream(first_path, run_id=run_id, attempt_id=attempt_one)
    first.begin_step(1, now)
    committed = first.append(
        agent_key="resident-001",
        content="Committed memory",
        kind="event",
        poignancy=5,
    )
    checkpoint = tmp_path / "checkpoint"
    first.export_storage(checkpoint)

    first.begin_step(2, now + timedelta(minutes=10))
    future = first.append(
        agent_key="resident-001",
        content="Future memory",
        kind="event",
        poignancy=6,
    )

    recovered_path = tmp_path / "attempt-2" / "memory.sqlite"
    recovered_path.parent.mkdir(parents=True)
    shutil.copy2(checkpoint / "memory.sqlite", recovered_path)
    recovered = MemoryStream(
        recovered_path,
        run_id=run_id,
        attempt_id=uuid4(),
    )
    recovered.begin_step(2, now + timedelta(minutes=10))

    assert [item["id"] for item in recovered.search(agent_key="resident-001")] == [
        committed["id"]
    ]
    replayed = recovered.append(
        agent_key="resident-001",
        content="Future memory",
        kind="event",
        poignancy=6,
    )
    assert replayed["id"] == future["id"]

    other_run = MemoryStream(
        recovered_path,
        run_id=uuid4(),
        attempt_id=uuid4(),
    )
    other_run.begin_step(1, now)
    assert other_run.search(agent_key="resident-001") == []


def test_skill_memory_side_effects_join_the_step_ledger(tmp_path):
    """回归验证 ``test_skill_memory_side_effects_join_the_step_ledger`` 所描述的业务结果、故障边界和隔离约束。"""
    run_id = uuid4()
    attempt_id = uuid4()
    now = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
    memory = MemoryStream(
        tmp_path / "memory.sqlite",
        run_id=run_id,
        attempt_id=attempt_id,
    )
    memory.begin_step(1, now)
    memory.append(
        agent_key="resident-001",
        content="A Skill-authored observation",
        kind="event",
        poignancy=7,
    )
    memory.search(agent_key="resident-001", query="observation")
    builder = StepResultBuilder(
        run_id=run_id,
        attempt_id=attempt_id,
        step_no=1,
        virtual_time=now,
    )
    collector = StepResultCollector(builder, name_to_key={})
    for event in memory.drain_result_events():
        collector.capture_event(event)

    result = collector.freeze()

    assert [delta.kind for delta in result.memory_deltas] == [
        MemoryDeltaKind.CREATED,
        MemoryDeltaKind.ACCESSED,
    ]
    assert StepEffectKind.EVENT_PERCEIVED in {
        effect.kind for effect in result.effects
    }


def test_memory_iteration_rollback_restores_rows_and_pending_events(tmp_path):
    run_id, attempt_id = uuid4(), uuid4()
    now = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
    memory = MemoryStream(
        tmp_path / "atomic-memory.sqlite",
        run_id=run_id,
        attempt_id=attempt_id,
    )
    memory.begin_step(1, now)
    committed = memory.append(
        agent_key="resident-001",
        content="提交前已经存在的事实",
    )
    baseline_events = memory.drain_result_events()
    snapshot = memory.begin_iteration("resident-001")

    memory.search(agent_key="resident-001", query="存在")
    memory.supersede(
        agent_key="resident-001",
        memory_id=committed["id"],
        content="失败调用产生的替代事实",
    )
    memory.append(
        agent_key="resident-001",
        content="失败调用产生的新事实",
    )
    memory.rollback_iteration(snapshot)

    found = memory.search(agent_key="resident-001", query="事实")
    assert [item["id"] for item in found] == [committed["id"]]
    assert found[0]["content"] == "提交前已经存在的事实"
    # The baseline had already been drained.  Rollback removes only events
    # emitted by the failed iteration; this final search emits one access event.
    assert baseline_events[0]["memory_id"] == committed["id"]
    assert [item["memory_kind"] for item in memory.drain_result_events()] == [
        MemoryDeltaKind.ACCESSED.value
    ]


def test_memory_supersede_and_invalidate_preserve_history_but_hide_stale_versions(
    tmp_path,
):
    database_path = tmp_path / "memory-lifecycle.sqlite"
    memory = MemoryStream(database_path, run_id=uuid4(), attempt_id=uuid4())
    started = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)
    memory.begin_step(1, started)
    original = memory.append(
        agent_key="resident-001",
        content="会议安排在下午两点",
        kind="event",
        poignancy=7,
    )
    memory.drain_result_events()

    memory.begin_step(2, started + timedelta(minutes=10))
    changed = memory.supersede(
        agent_key="resident-001",
        memory_id=original["id"],
        content="会议改到下午三点",
        reason="收到新的会议通知",
    )
    replacement = changed["replacement"]
    found = memory.search(agent_key="resident-001", query="会议几点")
    events = memory.drain_result_events()

    assert [item["id"] for item in found] == [replacement["id"]]
    assert [event["memory_kind"] for event in events[:2]] == [
        MemoryDeltaKind.SUPERSEDED.value,
        MemoryDeltaKind.CREATED.value,
    ]
    with sqlite3.connect(database_path) as connection:
        old_state, superseded_by = connection.execute(
            "SELECT state, superseded_by_memory_id FROM run_memories WHERE id = ?",
            (original["id"],),
        ).fetchone()
        new_state, supersedes = connection.execute(
            "SELECT state, supersedes_memory_id FROM run_memories WHERE id = ?",
            (replacement["id"],),
        ).fetchone()
    assert (old_state, superseded_by) == ("SUPERSEDED", replacement["id"])
    assert (new_state, supersedes) == ("ACTIVE", original["id"])

    memory.begin_step(3, started + timedelta(minutes=20))
    memory.invalidate(
        agent_key="resident-001",
        memory_id=replacement["id"],
        reason="会议已经取消",
    )

    assert memory.search(agent_key="resident-001", query="会议") == []
    with sqlite3.connect(database_path) as connection:
        state, reason = connection.execute(
            "SELECT state, invalidated_reason FROM run_memories WHERE id = ?",
            (replacement["id"],),
        ).fetchone()
    assert (state, reason) == ("INVALIDATED", "会议已经取消")
