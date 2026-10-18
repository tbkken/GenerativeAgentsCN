"""Independent execution limits must not truncate a progressing Skill tree."""
from __future__ import annotations

import copy
import json
from collections import deque

import pytest

from generative_agents.ga_protocol.skills.documents import SkillRegistry
from generative_agents.ga_runtime.memory.stream import FileMemoryStream
from generative_agents.ga_runtime.skills.executor import SkillBudgetExceeded, SkillRuntime
from tests.runtime.test_brain_capability_runtime import _server
from tests.skill_files import write_skill


def tool(name, arguments, call_id):
    return {'content': '', 'tool_calls': [{'id': call_id, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(arguments)}}]}


class Model:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.requests = []

    def chat_completion(self, messages, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        return self.responses.popleft()


def progressing_tree(tmp_path, *, branches=5, **limits):
    server = _server()
    memory = FileMemoryStream(tmp_path / 'memory', run_id=server.iteration.run_id,
                              attempt_id=server.iteration.attempt_id)
    memory.begin_step(1, server.iteration.now)
    server.memory_stream = memory
    registry = SkillRegistry(tmp_path / 'skills')
    write_skill(registry, 'brain', '按真实结果依次组织行为', kind='brain',
                body='调用 $remember 记录不同事实，最后使用 @world-act 开始活动。')
    write_skill(registry, 'remember', '记下并核对事实',
                body='使用 @memory-stream-append 记录事实，再用 @memory-stream-search 核对。')
    responses = []
    for index in range(branches):
        fact = f'独立事实 {index}'
        responses.extend([
            tool('call_skill', {'name': 'remember', 'input_text': fact}, f'child-{index}'),
            tool('memory-stream-append', {'content': fact}, f'write-{index}'),
            tool('memory-stream-search', {'query': fact}, f'read-{index}'),
            {'content': f'已保存并检索到 {fact}'},
        ])
    responses.append(tool('world-act', {'action_type': 'ACT', 'predicate': '整理', 'object': '资料'}, 'act'))
    model = Model(responses)
    runtime = SkillRuntime(registry, model_client=model, interfaces=server, **limits)
    return runtime, model, server, memory


def test_default_budgets_allow_a_progressing_tree_beyond_twelve_tools(tmp_path):
    runtime, model, server, memory = progressing_tree(tmp_path)
    result = runtime.run('brain', '逐项记下五条事实，再整理资料。')
    assert server.action.action_type == 'ACT'
    assert len(memory.search(agent_key=server.iteration.agent_key, limit=8)) == 5
    assert len(model.requests) == 21
    assert runtime._total_tool_calls == 16
    assert sum(event['event'] == 'interface.call' and event['tool'] == 'world-act'
               for event in result.trace) == 1
    assert not any(event['event'].startswith(('loop.', 'budget.')) for event in result.trace)
    assert server.invoke('world-act', {'action_type': 'WAIT'})['ok'] is False


def test_child_and_parent_model_rounds_are_independent(tmp_path):
    runtime, model, server, _ = progressing_tree(tmp_path, branches=2, max_skill_rounds=3)
    runtime.run('brain', '记录并整理。')
    assert server.action.action_type == 'ACT'
    assert len(model.requests) == 9  # Parent 3; each helper independently 3.


def test_round_budget_stops_before_the_next_model_request(tmp_path):
    runtime, model, server, memory = progressing_tree(tmp_path, max_skill_rounds=2)
    with pytest.raises(SkillBudgetExceeded) as caught:
        runtime.run('brain', '记录并整理。')
    error = caught.value
    assert (error.budget_kind, error.limit, error.used, error.attempted, error.skill) == (
        'skill_rounds', 2, 2, 3, 'remember')
    assert len(model.requests) == 3  # Parent chooses child; child writes and reads.
    assert runtime._total_tool_calls == 3
    assert len(memory.search(agent_key=server.iteration.agent_key)) == 1
    assert server.action is None
    assert error.trace[-1]['event'] == 'budget.exhausted'


def test_total_tool_budget_counts_nested_wrappers_and_stops_before_next_effect(tmp_path):
    runtime, model, server, memory = progressing_tree(tmp_path, max_tool_calls=4)
    with pytest.raises(SkillBudgetExceeded) as caught:
        runtime.run('brain', '记录并整理。')
    error = caught.value
    assert (error.budget_kind, error.limit, error.used, error.attempted, error.skill) == (
        'tool_calls', 4, 4, 5, 'remember')
    assert runtime._total_tool_calls == 4
    assert len(memory.search(agent_key=server.iteration.agent_key)) == 1
    assert server.action is None
    assert error.trace[-1]['event'] == 'budget.exhausted'


def test_call_depth_is_checked_before_entering_the_next_child(tmp_path):
    runtime, model, server, _ = progressing_tree(tmp_path, max_call_depth=1)
    write_skill(runtime.registry, 'remember', '继续委托', body='调用 $leaf。')
    write_skill(runtime.registry, 'leaf', '末层技能', body='返回结果。')
    model.responses = deque([
        tool('call_skill', {'name': 'remember', 'input_text': '交接'}, 'parent'),
        tool('call_skill', {'name': 'leaf', 'input_text': '交接'}, 'child'),
        {'content': 'must not run'},
    ])
    with pytest.raises(SkillBudgetExceeded) as caught:
        runtime.run('brain', '逐层处理。')
    error = caught.value
    assert (error.budget_kind, error.limit, error.used, error.attempted, error.skill) == (
        'call_depth', 1, 1, 2, 'leaf')
    assert len(model.requests) == 2
    assert runtime._total_tool_calls == 1
    assert not any(event['event'] == 'skill.start' and event['skill'] == 'leaf'
                   for event in error.trace)
    assert server.action is None


@pytest.mark.parametrize('depth', [8, 9])
def test_default_call_depth_allows_eight_levels_and_rejects_the_ninth(tmp_path, depth):
    registry = SkillRegistry(tmp_path / 'skills')
    for index in range(depth + 1):
        write_skill(registry, f'level-{index}', '逐层执行',
                    body=f'调用 $level-{index + 1}。' if index < depth else '返回结果。')
    responses = [tool('call_skill', {'name': f'level-{index}', 'input_text': '任务'}, f'child-{index}')
                 for index in range(1, depth + 1)]
    responses.extend({'content': '已完成'} for _ in range(depth + 1))
    model = Model(responses)
    runtime = SkillRuntime(registry, model_client=model)
    if depth == 8:
        assert runtime.run('level-0', '任务').output_text == '已完成'
        assert len(model.requests) == 17
    else:
        with pytest.raises(SkillBudgetExceeded) as caught:
            runtime.run('level-0', '任务')
        assert (caught.value.limit, caught.value.used, caught.value.attempted) == (8, 8, 9)
        assert len(model.requests) == 9
    assert runtime._total_tool_calls == 8


def test_world_action_at_exactly_the_128th_tool_is_accepted_without_another_call(tmp_path):
    runtime, model, server, memory = progressing_tree(tmp_path, branches=42, max_skill_rounds=64)
    write_skill(runtime.registry, 'brain', '逐项核对并行动', kind='brain',
                body='使用 $remember 记录事实，用 @memory-stream-search 核对，再用 @world-act 行动。')
    model.responses.insert(len(model.responses) - 1,
        tool('memory-stream-search', {'query': '独立事实'}, 'final-check'))
    runtime.run('brain', '记录并整理。')
    assert runtime._total_tool_calls == runtime.max_tool_calls == 128
    assert server.action.action_type == 'ACT'
    assert len(model.requests) == 170
    assert not model.responses
    assert len(memory.search(agent_key=server.iteration.agent_key, limit=100)) == 42


def test_each_run_starts_with_a_fresh_tool_budget(tmp_path):
    runtime, model, server, _ = progressing_tree(tmp_path, branches=2, max_tool_calls=7)
    responses = copy.deepcopy(model.responses)
    runtime.run('brain', '记录并整理。')
    assert runtime._total_tool_calls == 7
    server.discard_action()
    model.responses = responses
    runtime.run('brain', '下一轮继续处理。')
    assert runtime._total_tool_calls == 7
    assert server.action.action_type == 'ACT'


@pytest.mark.parametrize('field', ['max_call_depth', 'max_skill_rounds', 'max_tool_calls'])
@pytest.mark.parametrize('value', [0, -1, True, 1.5, None])
def test_budget_values_cannot_disable_limits(tmp_path, field, value):
    with pytest.raises(ValueError, match=field):
        progressing_tree(tmp_path, **{field: value})
