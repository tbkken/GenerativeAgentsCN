"""Memory evidence survives committed DTOs, both projections and filtered exports."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from uuid import UUID

import pytest

from generative_agents.ga_protocol.schemas.facts import MemoryDelta, StepResult
from generative_agents.ga_protocol.schemas.memory import MemoryDeltaKind
from generative_agents.ga_replay.projections.console import definition_names, memory_views
from generative_agents.ga_replay.projections.index import advance_views
from generative_agents.ga_runtime.storage.exports import export_run_artifact


def _facts(address):
    start = datetime.fromisoformat('2026-10-22T08:00:00+08:00')
    run_id, attempt_id, source_event_id = UUID(int=1), UUID(int=2), UUID(int=3)
    created = MemoryDelta(
        event_id=UUID(int=4), sequence=1, agent_key='alice', memory_id='memory-a',
        kind=MemoryDeltaKind.CREATED, memory_type='EVENT', description='记住 Bob 的观点',
        poignancy=0.8, source_event_id=source_event_id,
        subject='Bob', predicate='提出', object='共同讨论的观点', address=address,
        created_at=start, expires_at=start + timedelta(days=7),
        evidence_memory_ids=('earlier-a', 'earlier-b'),
    )
    other = MemoryDelta(
        event_id=UUID(int=5), sequence=2, agent_key='bob', memory_id='memory-b',
        kind=MemoryDeltaKind.CREATED, memory_type='EVENT', description='Bob 的记忆',
    )
    accessed = MemoryDelta(
        event_id=UUID(int=6), sequence=1, agent_key='alice', memory_id='memory-a',
        kind=MemoryDeltaKind.ACCESSED, memory_type='EVENT',
    )
    invalidated = MemoryDelta(
        event_id=UUID(int=7), sequence=1, agent_key='alice', memory_id='memory-a',
        kind=MemoryDeltaKind.INVALIDATED, memory_type='EVENT', reason='后续证据修正',
    )
    frames = [StepResult(
        run_id=run_id, attempt_id=attempt_id, step_no=step,
        virtual_time=start + timedelta(minutes=step - 1), agents=(), conversations=(),
        memory_deltas=deltas, schedule_revisions=(), domain_events=(), committed_model_usage=(),
    ).to_dict() for step, deltas in enumerate(((created, other), (accessed,), (invalidated,)), 1)]
    return {
        'summary': {'run_id': str(run_id), 'status': 'COMPLETED', 'committed_step': 3},
        'status': {'run_id': str(run_id), 'status': 'COMPLETED',
                   'active_attempt_id': str(attempt_id), 'committed_step': 3,
                   'total_steps': 3, 'updated_at': frames[-1]['virtual_time']},
        'definitions': [{'agent_key': key, 'name': key.title(), 'space_id': 'discussion'}
                        for key in ('alice', 'bob')],
        'definition': {'simulation': {'stride_minutes': 1}}, 'frames': frames,
    }


def _evidence(item):
    return {key: item[key] for key in (
        'source_event_id', 'subject', 'predicate', 'object', 'address',
        'evidence_memory_ids', 'expires_at',
    )}


@pytest.mark.parametrize('address', [(), ('town', 'square', 'cafe', 'table')])
def test_memory_delta_evidence_matches_full_and_incremental_projections(address):
    facts = _facts(address)
    raw = facts['frames'][0]['memory_deltas'][0]
    before = advance_views({**facts, 'frames': facts['frames'][:1]})
    unchanged = copy.deepcopy(before)
    after = advance_views(facts, before, start=1)
    assert before == unchanged
    assert after == advance_views(facts)
    assert after['memories'] == memory_views(facts, definition_names(facts))

    item = after['agent_details']['alice']['memories'][0]
    assert _evidence(item) == _evidence(raw)
    assert item['state'] == 'INVALIDATED'
    assert item['last_accessed_step'] == 2
    assert item['removed_step'] == 3
    assert item['invalidated_reason'] == '后续证据修正'
    item['address'].append('projection-only')
    item['evidence_memory_ids'].append('projection-only')
    assert raw['address'] == list(address)
    assert raw['evidence_memory_ids'] == ['earlier-a', 'earlier-b']
    assert before == unchanged


@pytest.mark.parametrize('address', [(), ('town', 'square', 'cafe', 'table')])
def test_filtered_memory_export_retains_committed_evidence(tmp_path, address):
    facts = _facts(address)
    memories = advance_views(facts)['memories']
    result = export_run_artifact(
        tmp_path, facts, job_type='FILTERED_MEMORIES',
        parameters={'agent_key': 'alice'}, memories=memories,
    )
    lines = (tmp_path / 'artifacts' / result['artifact_name']).read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    exported = json.loads(lines[0])
    assert exported == next(item for item in memories if item['agent_key'] == 'alice')
    assert _evidence(exported) == _evidence(facts['frames'][0]['memory_deltas'][0])
    provenance = json.loads(next((tmp_path / 'artifact-metadata').glob('*.json')).read_text(encoding='utf-8'))
    assert provenance['source_step'] == 3
    assert provenance['source_status'] == 'COMPLETED'
