from __future__ import annotations

import shutil
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
