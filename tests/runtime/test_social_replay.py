"""Social replay preserves thread history, boundaries and per-author counts."""
from __future__ import annotations

import copy

import pytest

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_replay.projections.console import (
    agent_views, conversation_views, definition_names, replay_web_manifest, social_view,
)
from generative_agents.ga_replay.projections.index import advance_views
from generative_agents.ga_replay.reader import ReplayReader


def _message(identity, speaker, sequence, step, *, parent=None, title=None):
    return {'message_id': identity, 'speaker_agent_key': speaker, 'sequence': sequence,
            'content': f'正文 {identity}', 'step_no': step, 'actor_sequence': sequence,
            'reply_to_message_id': parent, 'quoted_message_id': None, 'title': title,
            'source_event_id': f'event-{identity}'}


def _conversation(identity, messages, *, private=False):
    return {'conversation_id': identity, 'participant_agent_keys': ['alice', 'bob'],
            'visibility': 'private' if private else 'public',
            'kind': 'direct' if private else 'forum', 'location': [],
            'messages': messages, 'summary': messages[-1]['content']}


def _facts():
    definitions = [{'agent_key': key, 'name': key.title(), 'space_id': 'discussion',
                    'portrait_asset': f'assets/{key}.png'} for key in ('alice', 'bob')]
    frames = [
        {'step_no': 1, 'virtual_time': '2026-10-22T08:00:00+08:00', 'conversations': [
            _conversation('topic', [_message('root', 'alice', 1, 1, title='讨论主题'),
                                    _message('reply', 'bob', 2, 1, parent='root')])]},
        {'step_no': 2, 'virtual_time': '2026-10-22T08:01:00+08:00', 'conversations': [
            _conversation('topic', [_message('nested', 'alice', 3, 2, parent='reply')]),
            _conversation('direct', [_message('private-a', 'alice', 4, 2)], private=True)]},
        {'step_no': 3, 'virtual_time': '2026-10-22T08:02:00+08:00', 'conversations': [
            _conversation('direct', [_message('private-b', 'bob', 5, 3, parent='private-a')], private=True)]},
    ]
    for frame in frames:
        frame['agents'] = [{'agent_key': key, 'from_coord': None, 'to_coord': None,
                            'location': [], 'path': [], 'activity_kind': 'CHAT',
                            'action': {'description': '参与讨论'}} for key in ('alice', 'bob')]
    return {'summary': {'run_id': 'social-replay-test', 'experiment_id': 'experiment',
                        'committed_step': 3, 'requested_steps': 3, 'status': 'COMPLETED'},
            'definition': {'world': {'world_key': 'discussion', 'world_name': '讨论',
                'definition': {'environment_type': 'social_1d', 'presentation': 'forum'}},
                'simulation': {'stride_minutes': 1}, 'engine': {'brain_skill': 'brain'}},
            'definitions': definitions, 'frames': frames}


def test_history_boundary_filters_thread_list_and_nested_future_replies():
    facts = _facts()
    assert social_view(facts, 0)['conversations'] == []
    first = social_view(facts, 1)['conversations']
    assert [item['conversation_id'] for item in first] == ['topic']
    assert [message['message_id'] for message in first[0]['messages']] == ['root', 'reply']
    assert first[0]['messages'][1]['reply_to_message_id'] == 'root'
    second = {item['conversation_id']: item for item in social_view(facts, 2)['conversations']}
    assert second['topic']['messages'][-1]['reply_to_message_id'] == 'reply'
    assert second['direct']['message_count'] == 1
    assert second['direct']['visibility'] == 'private'
    assert second['direct']['messages'][0]['source_event_id'] == 'event-private-a'
    with pytest.raises(IndexError):
        social_view(facts, 4)


def test_incremental_rebuild_matches_full_reduction_and_counts_authored_messages():
    facts = _facts()
    prefix = {**facts, 'frames': facts['frames'][:1]}
    before = advance_views(prefix)
    before_copy = copy.deepcopy(before)
    after = advance_views(facts, before, start=1)
    assert before == before_copy
    assert after == advance_views(facts)
    assert after['conversations'] == conversation_views(facts, definition_names(facts))
    counts = {item['agent_key']: item['message_count'] for item in after['agents']}
    assert counts == {'alice': 3, 'bob': 2}
    assert all(item['coord'] is None and item['movement_steps'] == 0 for item in after['agents'])
    assert {item['agent_key']: item['message_count'] for item in agent_views(facts)[0]} == counts


def test_conversation_reduction_deduplicates_identical_facts_and_rejects_conflicts():
    facts = _facts()
    duplicated = copy.deepcopy(facts)
    duplicated['frames'][0]['conversations'].append(copy.deepcopy(facts['frames'][0]['conversations'][0]))
    assert conversation_views(duplicated, {}) == conversation_views(facts, {})
    duplicated['frames'][0]['conversations'][-1]['messages'][0]['content'] = '被篡改的正文'
    with pytest.raises(PackageError, match='conflicting'):
        conversation_views(duplicated, {})
    orphan = copy.deepcopy(facts)
    orphan['frames'][0]['conversations'][0]['messages'][1]['reply_to_message_id'] = 'future'
    with pytest.raises(PackageError, match='unavailable'):
        conversation_views(orphan, {})


def test_reader_state_retains_prior_messages_instead_of_only_latest_delta():
    facts = _facts()
    reader = ReplayReader.__new__(ReplayReader)
    reader.iter_steps = lambda **kwargs: iter(frame for frame in facts['frames'] if frame['step_no'] <= kwargs['end'])
    state = reader.state_at(2)
    assert [item['message_id'] for item in state['conversations']['topic']['messages']] == ['root', 'reply', 'nested']
    assert state['conversations']['direct']['message_count'] == 1


def test_social_manifest_has_portraits_but_no_fabricated_geometry():
    facts = _facts()
    original = copy.deepcopy(facts)
    manifest = replay_web_manifest(facts['summary']['run_id'], facts)
    assert manifest['renderer'] == 'SOCIAL_FORUM'
    assert manifest['environment_type'] == 'social_1d'
    assert manifest['world']['render_asset']['renderer'] == 'SOCIAL_FORUM'
    assert 'tiles' not in manifest['world']['definition']
    assert all('initial_coord' not in item and 'sprite_asset' not in item for item in manifest['agents'])
    assert manifest['agents'][0]['portrait_url'].endswith('/assets/alice.png')
    assert facts == original
