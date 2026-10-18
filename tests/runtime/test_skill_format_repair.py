"""Malformed model tool batches cannot execute before bounded correction."""
from __future__ import annotations

import copy
import json
from collections import deque
from uuid import uuid4

import httpx
import pytest

from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_runtime.engine.context import RunControl
from generative_agents.ga_runtime.models.gateway import LLMModel
from generative_agents.ga_runtime.models.retry import ModelCallCancelled
from generative_agents.ga_runtime.models.trace import ModelTraceEventType
from generative_agents.ga_runtime.skills.executor import SkillRuntime, SkillOutputFormatError, SkillModelError
from tests.skill_files import write_skill


def call(name='call_skill', arguments=None, call_id='child'):
    return {'id': call_id, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(arguments if arguments is not None else {})}}


def response(*calls):
    return {'content': '', 'tool_calls': list(calls)}


class Recorder:
    run_id = uuid4()
    attempt_id = uuid4()

    def __init__(self):
        self.events = []
        self.after_attempt = None

    def append(self, event):
        self.events.append(event)
        if event.event_type == ModelTraceEventType.PHYSICAL_ATTEMPT and self.after_attempt:
            self.after_attempt(event)


class Model(LLMModel):
    def setup(self, config):
        self.outcomes = deque()
        self.requests = []
        return None

    def _request_json(self, _method, _url, *, payload=None, **_kwargs):
        self.requests.append(copy.deepcopy(payload))
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return {'choices': [{'message': outcome}]}


class Interfaces:
    def __init__(self):
        self.writes = []

    def descriptors(self):
        return [{'name': 'memory-stream-append', 'description': 'Append private memory',
                 'parameters': {'type': 'object', 'properties': {'content': {'type': 'string'}}}}]

    def invoke(self, name, arguments):
        self.writes.append((name, arguments))
        return {'ok': True, 'result': 'memory saved'}


def runtime(tmp_path, outcomes, *, retry=1, control=None, timeout=2, **limits):
    registry = SkillRegistry(tmp_path / 'skills')
    write_skill(registry, 'parent', '组织任务', body='使用 $source、$sink 和 @memory-stream-append。')
    write_skill(registry, 'source', '读取来源', body='返回完整原文。')
    write_skill(registry, 'sink', '处理文本', body='根据文本处理。')
    recorder = Recorder()
    model = Model({'model': 'test', 'base_url': 'http://unused/v1', 'retry_attempts': retry,
                   'retry_backoff_seconds': 0, 'timeout_seconds': timeout},
                  recorder=recorder, control=control)
    model.outcomes.extend(outcomes)
    interfaces = Interfaces()
    runner = SkillRuntime(registry, model_client=model, retry_attempts=retry,
                          interfaces=interfaces, control=control, **limits)
    return runner, model, recorder, interfaces


def test_empty_child_arguments_are_corrected_once_even_with_single_transport_attempt(tmp_path):
    runner, model, recorder, _ = runtime(tmp_path, [response(call()),
        response(call(arguments={'name': 'sink', 'input_text': '真实任务'})),
        {'content': '已处理'}, {'content': '完成'}])
    result = runner.run('parent', '处理任务')
    assert result.output_text == '完成'
    assert len(model.requests) == 4
    assert 'call_skill name must be a non-empty string' in model.requests[1]['messages'][-1]['content']
    attempts = [event for event in recorder.events if event.event_type == ModelTraceEventType.PHYSICAL_ATTEMPT]
    assert attempts[0].status.value == 'FAILED'
    assert attempts[0].call_id == attempts[1].call_id
    assert attempts[0].attempt_no == 1 and attempts[1].attempt_no == 2
    assert sum(event['event'] == 'skill.call' for event in result.trace) == 1


def test_format_repairs_do_not_consume_extra_skill_rounds_or_tool_calls(tmp_path):
    from tests.runtime.test_brain_capability_runtime import _server

    malformed = call('world-act')
    malformed['function']['arguments'] = '{'
    runner, model, recorder, _ = runtime(tmp_path, [response(malformed), response(malformed),
        response(call('world-act', {'action_type': 'ACT', 'predicate': '整理', 'object': '资料'}))],
        max_skill_rounds=1, max_tool_calls=1)
    write_skill(runner.registry, 'parent', '确定本轮动作', body='使用 @world-act 行动。')
    runner.interfaces = _server()
    runner.run('parent', '整理资料。')
    assert runner.interfaces.action.action_type == 'ACT'
    assert len(model.requests) == 3
    assert runner._total_tool_calls == 1
    assert sum(event.event_type == ModelTraceEventType.LOGICAL_END for event in recorder.events) == 1


def test_entire_batch_is_validated_before_memory_or_child_side_effects(tmp_path):
    write = call('memory-stream-append', {'content': 'only once'}, 'write')
    corrected = call(arguments={'name': 'sink', 'input_text': '处理'}, call_id='child')
    runner, model, _, interfaces = runtime(tmp_path, [response(write, call()),
        response(write, corrected), {'content': '子技能完成'}, {'content': '完成'}])
    runner.run('parent', '处理任务')
    assert len(interfaces.writes) == 1
    assert interfaces.writes[0][1] == {'content': 'only once'}
    assert len(model.requests) == 4


@pytest.mark.parametrize('bad', [
    response(call(arguments={'name': 'unavailable', 'input_text': 'test'})),
    response(call(arguments={'name': 'sink', 'input_text': 'test', 'input_from': 'x'})),
    response(call(arguments={'name': 'sink', 'input_from': 'unavailable'})),
    response(call(arguments={'name': 'sink', 'input_text': 4})),
    response(call('unavailable-tool')),
    response({'id': 'x', 'function': None}),
    response({'id': 'x', 'function': 'invalid'}),
    response('invalid'),
    {'content': '', 'tool_calls': {'function': 'invalid'}},
    response(call(call_id='same'), call(call_id='same')),
])
def test_invalid_batch_never_reaches_execution_and_can_be_replaced(tmp_path, bad):
    runner, model, _, interfaces = runtime(tmp_path, [bad, {'content': '未执行工具，已结束。'}])
    result = runner.run('parent', '处理任务')
    assert result.output_text == '未执行工具，已结束。'
    assert len(model.requests) == 2
    assert not interfaces.writes
    assert not any(event['event'] == 'skill.call' for event in result.trace)


def test_format_correction_exhaustion_is_typed_bounded_and_does_not_grow_context(tmp_path):
    runner, model, _, interfaces = runtime(tmp_path, [response(call())] * 8)
    with pytest.raises(SkillOutputFormatError, match='call_skill name'):
        runner.run('parent', '处理任务')
    assert len(model.requests) == 3
    assert len(model.requests[1]['messages']) == len(model.requests[2]['messages']) == 3
    assert not interfaces.writes


def test_transport_failure_during_format_repair_does_not_override_single_attempt_policy(tmp_path):
    failure = httpx.HTTPStatusError('generation timeout', request=httpx.Request('POST', 'http://test'),
                                   response=httpx.Response(504))
    runner, model, _, _ = runtime(tmp_path, [response(call()), failure, {'content': 'must not run'}])
    with pytest.raises(SkillModelError) as caught:
        runner.run('parent', '处理任务')
    assert not isinstance(caught.value, SkillOutputFormatError)
    assert len(model.requests) == 2


def test_cancellation_after_invalid_response_prevents_correction_request(tmp_path):
    control = RunControl()
    runner, model, recorder, _ = runtime(tmp_path, [response(call())] * 3, control=control)
    recorder.after_attempt = lambda _event: control.request_cancel()
    with pytest.raises(ModelCallCancelled):
        runner.run('parent', '处理任务')
    assert len(model.requests) == 1


def test_format_repair_shares_the_original_logical_deadline(tmp_path, monkeypatch):
    import generative_agents.ga_runtime.models.gateway as gateway
    clock = [100.0]
    monkeypatch.setattr(gateway.time, 'monotonic', lambda: clock[0])
    runner, model, recorder, _ = runtime(tmp_path, [response(call())] * 3, timeout=2)
    recorder.after_attempt = lambda _event: clock.__setitem__(0, 102.1)
    with pytest.raises(SkillModelError):
        runner.run('parent', '处理任务')
    assert len(model.requests) == 1


def test_interleaved_transport_failures_and_format_repairs_have_a_hard_total_bound(tmp_path):
    def timeout():
        return httpx.HTTPStatusError('timeout', request=httpx.Request('POST', 'http://test'),
                                     response=httpx.Response(504))
    runner, model, recorder, _ = runtime(tmp_path,
        [response(call()), timeout(), response(call()), timeout(), {'content': 'must not run'}], retry=2)
    with pytest.raises(SkillModelError) as caught:
        runner.run('parent', '处理任务')
    assert not isinstance(caught.value, SkillOutputFormatError)
    assert len(model.requests) == 4  # 2 transport attempts + 2 format corrections.
    calls = [event for event in recorder.events if event.event_type == ModelTraceEventType.PHYSICAL_ATTEMPT]
    assert len({event.call_id for event in calls}) == 1
    assert [event.attempt_no for event in calls] == [1, 2, 3, 4]


def test_previous_completed_writes_are_not_repeated_by_next_response_correction(tmp_path):
    runner, model, _, interfaces = runtime(tmp_path, [
        response(call('memory-stream-append', {'content': 'first'}, 'write')),
        response(call()), response(call(arguments={'name': 'sink', 'input_text': 'test'})),
        {'content': 'done'}, {'content': 'finished'}])
    runner.run('parent', '处理任务')
    assert len(interfaces.writes) == 1
    assert model.requests[1]['messages'][-1]['role'] == 'tool'
    assert model.requests[2]['messages'][-2]['role'] == 'tool'


def test_same_batch_child_result_handoff_survives_a_corrected_batch(tmp_path):
    first = call(arguments={'name': 'source', 'input_text': 'source'}, call_id='source')
    second = call(arguments={'name': 'sink', 'input_from': 'source'}, call_id='sink')
    runner, model, _, _ = runtime(tmp_path, [response(first, call()), response(first, second),
        {'content': '  exact original\n'}, {'content': 'done'}, {'content': 'finished'}])
    runner.run('parent', '处理任务')
    assert model.requests[3]['messages'][-1]['content'] == '  exact original\n'


def test_malformed_tool_json_is_corrected_and_the_raw_rejected_response_is_audited(tmp_path):
    broken = call()
    broken['function']['arguments'] = '{"name":'
    runner, model, recorder, _ = runtime(tmp_path, [response(broken), {'content': 'finished'}])
    runner.run('parent', '处理任务')
    attempts = [event for event in recorder.events if event.event_type == ModelTraceEventType.PHYSICAL_ATTEMPT]
    assert attempts[0].payload['response']['tool_calls'][0]['function']['arguments'] == '{"name":'
    assert len(model.requests) == 2
    assert all(message['role'] != 'assistant' for message in model.requests[1]['messages'])


def test_child_wire_schema_keeps_object_properties_for_qwen_tool_grammar(tmp_path):
    runner, model, _, _ = runtime(tmp_path, [{'content': '完成'}])
    runner.run('parent', '处理任务')
    schema = next(tool['function']['parameters'] for tool in model.requests[0]['tools']
                  if tool['function']['name'] == 'call_skill')
    assert schema['type'] == 'object'
    assert schema['required'] == ['name']
    assert set(schema['properties']) == {'name', 'input_text', 'input_from'}
    assert 'oneOf' not in schema


def test_navigation_wire_schema_is_flat_but_runtime_rejects_ambiguous_targets(tmp_path):
    from generative_agents.ga_runtime.capabilities.server import SimulationAPI
    from types import SimpleNamespace

    runner, model, _, _ = runtime(tmp_path, [response(call('world-navigate', {
        'target_coord': [1, 2], 'target_address': ['World', 'Home', 'Room']}, 'nav')),
        {'content': '请求被拒绝，停止导航。'}])
    write_skill(runner.registry, 'parent', '导航', body='使用 @world-navigate 导航。')
    interfaces = SimulationAPI(None, SimpleNamespace(agent_key='alice'))
    runner.interfaces = interfaces
    runner.run('parent', '导航任务')
    schema = model.requests[0]['tools'][0]['function']['parameters']
    assert 'oneOf' not in schema
    assert set(schema['properties']) == {'target_coord', 'target_address'}
    original = next(tool for tool in interfaces.descriptors() if tool['name'] == 'world-navigate')
    assert 'oneOf' in original['parameters']
    result = json.loads(model.requests[1]['messages'][-1]['content'])
    assert result['ok'] is False
    assert 'exactly one target' in result['error']
    assert len(model.requests) == 2  # Business rejection uses the next normal model turn.
