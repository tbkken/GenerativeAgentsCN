import json
from datetime import datetime, timezone

from generative_agents.ga_protocol.packages.io import atomic_write_json, read_json
from generative_agents.ga_replay.execution import (
    _audit_tree, _calls, _compact, _observations, _span_tree, _span_progress, _tree, execution_observation,
)
from generative_agents.ga_runtime.lifecycle.service import RunService
from tests.test_portable_package_protocol import _experiment

NOW = datetime(2026, 10, 15, 10, tzinfo=timezone.utc)


def call(name, args, token='call_0'):
    return {'id': token, 'function': {'name': name, 'arguments': json.dumps(args)}}


def model(seq, skill, response, messages=(), status='SUCCEEDED'):
    return _compact({'attempt_id': 'a', 'event_seq': seq, 'call_id': f'm{seq}', 'step_no': 1,
        'agent_key': 'person', 'prompt_key': skill, 'status': status, 'event_type': 'LOGICAL_END',
        'started_at': '2026-10-15T09:00:00+00:00', 'ended_at': '2026-10-15T09:00:01+00:00',
        'payload': {'request': list(messages), 'response': response}})


def tree(records, frame=None, state='RUNNING'):
    return _tree(_calls(records), actor='person', brain='brain', names={}, frame=frame,
                 attempt_state=state, now=NOW)


def test_child_result_and_interface_result_are_scoped_to_invocation():
    child = call('call_skill', {'name': 'child'})
    search = call('memory-stream-search', {'query': 'today'})
    rows = [model(1, 'brain', {'tool_calls': [child]}), model(2, 'child', {'tool_calls': [search]}),
        model(3, 'child', {'content': 'child answer'}, [
            {'role': 'assistant', 'tool_calls': [search]}, {'role': 'tool', 'tool_call_id': 'call_0', 'content': '[]'}]),
        model(4, 'brain', {'tool_calls': [child]}, [
            {'role': 'assistant', 'tool_calls': [child]}, {'role': 'tool', 'tool_call_id': 'call_0', 'content': 'child answer'}]),
        model(5, 'child', {'content': 'second answer'})]
    result = tree(rows)
    children = [node for node in result['nodes'] if node['kind'] == 'skill' and node['skill_key'] == 'child']
    assert len(children) == 2
    assert children[0]['id'] != children[1]['id']
    interface = next(node for node in result['nodes'] if node['kind'] == 'interface')
    assert interface['parent_id'] == children[0]['id']
    assert interface['interface_status'] == 'SUCCEEDED'
    assert interface['output_preview'] == '[]'
    assert interface['commit_status'] == 'NOT_APPLICABLE'


def test_model_proposal_does_not_prove_interface_or_commit():
    result = tree([model(1, 'brain', {'tool_calls': [call('world-act', {'action_type': 'WAIT'})]})])
    node = next(node for node in result['nodes'] if node['kind'] == 'interface')
    assert node['interface_status'] == 'REQUESTED'
    assert node['commit_status'] == 'PENDING'
    assert next(node for node in result['nodes'] if node['kind'] == 'model')['model_status'] == 'SUCCEEDED'


def test_executed_memory_is_discarded_after_cancel_and_rejected_call_never_commits():
    write = call('memory-stream-append', {'content': 'note'})
    history = [{'role': 'assistant', 'tool_calls': [write]},
               {'role': 'tool', 'tool_call_id': 'call_0', 'content': '{"memory_id":"m1"}'}]
    rows = [model(1, 'brain', {'tool_calls': [write]}), model(2, 'brain', {'content': 'done'}, history)]
    node = next(node for node in tree(rows, state='CANCELLED')['nodes'] if node['kind'] == 'interface')
    assert (node['interface_status'], node['commit_status']) == ('SUCCEEDED', 'DISCARDED')
    history[-1]['content'] = '{"ok":false,"error":"denied"}'
    rows[-1] = model(2, 'brain', {'content': 'done'}, history)
    node = next(node for node in tree(rows, state='CANCELLED')['nodes'] if node['kind'] == 'interface')
    assert (node['interface_status'], node['commit_status']) == ('FAILED', 'NOT_APPLICABLE')


def test_commit_requires_matching_fact_and_respects_brain_rollback():
    args = {'content': 'note'}
    audit = {'event': 'interface.call', 'skill': 'brain', 'tool': 'memory-stream-append',
             'input_text': json.dumps(args), 'output_text': '{"memory_id":"m1"}', 'is_error': False}
    frame = {'effects': [{'agent_keys': ['person'], 'payload': {'trace': [audit]}}],
             'memory_deltas': [{'agent_key': 'person', 'memory_id': 'm1', 'kind': 'CREATED'}]}
    rows = [model(1, 'brain', {'tool_calls': [call('memory-stream-append', args)]})]
    assert next(n for n in tree(rows, frame)['nodes'] if n['kind'] == 'interface')['commit_status'] == 'COMMITTED'
    frame['effects'][0]['payload']['trace'].append({'event': 'brain.rollback'})
    assert next(n for n in tree(rows, frame)['nodes'] if n['kind'] == 'interface')['commit_status'] == 'DISCARDED'
    frame['effects'][0]['payload']['trace'].pop()
    frame['memory_deltas'][0]['agent_key'] = 'someone-else'
    assert next(n for n in tree(rows, frame)['nodes'] if n['kind'] == 'interface')['commit_status'] == 'UNKNOWN'


def test_world_commit_matches_canonical_accepted_action_for_lowercase_request():
    args = {'action_type': ' wait '}
    audit = {'event': 'interface.call', 'skill': 'brain', 'tool': 'world-act',
             'input_text': json.dumps(args),
             'output_text': '{"accepted":true,"action":{"action_type":"WAIT"}}', 'is_error': False}
    frame = {'effects': [{'agent_keys': ['person'], 'payload': {'trace': [audit]}}],
             'domain_events': [{'agent_keys': ['person'], 'payload': {'structured_payload': {'action_type': 'WAIT'}}}]}
    rows = [model(1, 'brain', {'tool_calls': [call('world-act', args)]})]
    node = next(n for n in tree(rows, frame)['nodes'] if n['kind'] == 'interface')
    assert (node['interface_status'], node['commit_status']) == ('SUCCEEDED', 'COMMITTED')
    frame['domain_events'][0]['payload']['structured_payload']['action_type'] = 'ACT'
    assert next(n for n in tree(rows, frame)['nodes'] if n['kind'] == 'interface')['commit_status'] == 'UNKNOWN'


def test_span_records_do_not_corrupt_model_stream_or_need_payload():
    model_row = model(1, 'child', {'content': 'answer'})
    events = [dict(_execution=True, event_seq=1, span_id='root', parent_span_id=None, kind='skill',
                   phase='START', status='RUNNING', timestamp='2026-10-15T09:00:00+00:00', name='brain', skill_key='brain'),
              dict(_execution=True, event_seq=2, span_id='child', parent_span_id='root', kind='skill',
                   phase='START', status='RUNNING', timestamp='2026-10-15T09:00:00+00:00', name='child', skill_key='child'),
              dict(_execution=True, event_seq=3, span_id='m1', parent_span_id='child', kind='model', call_id='m1',
                   phase='END', status='SUCCEEDED', timestamp='2026-10-15T09:00:01+00:00', name='model', skill_key='child')]
    assert _calls([model_row, *events])[0]['trace_id'] == 'a:1'
    result = _span_tree(events, _calls([model_row]), actor='person', brain='brain', names={}, frame=None,
                        attempt_state='RUNNING', now=NOW)
    assert next(node for node in result['nodes'] if node['id'] == 'm1')['parent_id'] == 'child'
    assert result['complete']


def test_committed_skill_audit_recovers_hierarchy_without_payload():
    frame = {'effects': [{'agent_keys': ['person'], 'payload': {'trace': [
        {'event': 'skill.start', 'skill': 'brain'}, {'event': 'skill.call', 'skill': 'brain', 'child': 'child'},
        {'event': 'skill.start', 'skill': 'child'}, {'event': 'skill.result', 'skill': 'child', 'output_text': 'answer'},
        {'event': 'skill.result', 'skill': 'brain', 'output_text': 'done'},
    ]}}]}
    result = _audit_tree([], actor='person', brain='brain', names={}, frame=frame, attempt_state='COMPLETED', now=NOW)
    root, child = result['nodes']
    assert child['parent_id'] == root['id']
    assert child['status'] == 'SUCCEEDED'


def test_partial_jsonl_is_not_lost_and_replacement_invalidates_index(tmp_path):
    traces = tmp_path / 'traces'
    traces.mkdir()
    target = traces / 'model.jsonl'
    row = {'attempt_id': 'a', 'event_seq': 1, 'call_id': 'm1'}
    encoded = json.dumps(row)
    target.write_text(encoded[:12], encoding='utf-8')
    assert not _observations(tmp_path)
    with target.open('a', encoding='utf-8') as handle:
        handle.write(encoded[12:] + '\n')
    assert len(_observations(tmp_path)) == 1
    target.write_text(json.dumps({**row, 'call_id': 'm2'}) + '\n', encoding='utf-8')
    assert _observations(tmp_path)[0]['call_id'] == 'm2'


def test_execution_stream_cannot_shadow_a_model_trace_with_same_sequence(tmp_path):
    from generative_agents.ga_replay.queries import trace_page, trace_record, trace_records
    traces = tmp_path / 'traces'
    traces.mkdir()
    model_record = {'attempt_id': 'a', 'event_seq': 1, 'call_id': 'model', 'event_type': 'PHYSICAL_START'}
    execution = {'attempt_id': 'a', 'event_seq': 1, 'span_id': 'span', 'phase': 'START', 'kind': 'model'}
    (traces / 'execution-001.jsonl').write_text(json.dumps(execution)+'\n', encoding='utf-8')
    (traces / 'model-calls-001.jsonl').write_text(json.dumps(model_record)+'\n', encoding='utf-8')
    assert [row['call_id'] for row in trace_records(tmp_path)] == ['model']
    assert [row['call_id'] for row in trace_page(tmp_path)['items']] == ['model']
    assert trace_record(tmp_path, 'a:1')['call_id'] == 'model'


def test_object_progress_is_separate_from_person_count():
    common = {'_execution': True, 'step_no': 1, 'timestamp': '2026-10-15T09:00:00+00:00'}
    events = [dict(common, event_seq=1, span_id='person', kind='actor', phase='START', status='RUNNING', agent_key='person'),
              dict(common, event_seq=2, span_id='person', kind='actor', phase='END', status='SUCCEEDED', agent_key='person'),
              dict(common, event_seq=3, span_id='object', kind='actor', phase='START', status='RUNNING',
                   agent_key='game-object:lamp', actor_name='路灯', actor_kind='object', actor_index=1)]
    result = _span_progress(events, 1, {'person': '人物'}, 'RUNNING', NOW)
    assert result['completed_agents'] == 1
    assert result['observed_agents'] == 1
    assert result['current_actor'] == {'key': 'game-object:lamp', 'name': '路灯', 'kind': 'object', 'ordinal': None}


def test_object_world_commit_uses_object_identity_and_actor_container_is_not_missing(tmp_path):
    from tests.runtime.test_object_skill_runtime import game_with_runtime, runner
    game, _model = game_with_runtime(tmp_path, lambda messages, kwargs: ('world-act', {'action_type': 'WAIT'}), agents=False)
    run, committer = runner(game)
    run.run(1, stride_minutes=1)
    frame = committer.results[0].to_dict()
    common = {'timestamp': '2026-10-15T09:00:00+00:00', 'status': 'SUCCEEDED', 'phase': 'END'}
    events = [dict(common, event_seq=1, span_id='actor', parent_span_id='step-outside-actor-selection', kind='actor',
                   name='camera', agent_key='game-object:camera'),
              dict(common, event_seq=2, span_id='skill', parent_span_id='actor', kind='skill', name='facility', skill_key='facility'),
              dict(common, event_seq=3, span_id='interface', parent_span_id='skill', kind='interface',
                   name='world-act', skill_key='facility')]
    result = _span_tree(events, [], actor='game-object:camera', brain='facility', names={}, frame=frame,
                        attempt_state='COMPLETED', now=NOW)
    interface = next(node for node in result['nodes'] if node['kind'] == 'interface')
    assert interface['interface_status'] == 'SUCCEEDED'
    assert interface['commit_status'] == 'COMMITTED'
    assert result['complete'], result['diagnostics']


def test_selected_attempt_never_overrides_live_progress_or_commits_other_attempt(tmp_path):
    experiment = _experiment(tmp_path / 'packages')
    run = RunService().create(experiment, tmp_path / 'run', requested_steps=2)
    status = read_json(run / 'status.json')
    active = status.get('active_attempt_id')
    # CREATED runs do not have an Attempt yet; use valid independent UUIDs.
    from uuid import uuid4
    active, historical = str(uuid4()), str(uuid4())
    status.update(status='RUNNING', active_attempt_id=active)
    atomic_write_json(run / 'status.json', status)
    for ordinal, identity in enumerate((historical, active), 1):
        atomic_write_json(run / 'attempts' / identity / 'attempt.json',
                          {'attempt_id': identity, 'ordinal': ordinal, 'status': 'CANCELLED' if ordinal == 1 else 'RUNNING'})
    (run / 'traces').mkdir(exist_ok=True)
    records = []
    for identity in (historical, active):
        records.append({'attempt_id': identity, 'event_seq': 1, 'call_id': identity, 'agent_key': 'teacher',
                        'step_no': 1, 'prompt_key': 'brain', 'event_type': 'PHYSICAL_START', 'status': 'RUNNING',
                        'started_at': '2026-10-15T09:00:00+00:00'})
    (run / 'traces' / 'model.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in records), encoding='utf-8')
    view = execution_observation(run, attempt_id=historical, step_no=1, agent_key='teacher')
    assert view['attempt_id'] == historical
    assert view['progress']['attempt_id'] == active
    assert view['progress']['committed_step'] == 0
    assert view['progress']['completed_agents'] is None
    assert view['tree']['diagnostics']
