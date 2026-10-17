"""Read-only execution observations. Audit success never substitutes for a commit.

Invocation boundaries come from execution spans and saved tool-call evidence.
Durable outcomes come only from StepResult. The incremental index retains
compact call evidence, not prompts or world state.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from generative_agents.ga_protocol.packages.definition import read_experiment_documents
from generative_agents.ga_protocol.packages.io import checked_package_path
from generative_agents.ga_protocol.packages.reading import file_fingerprint, open_readonly_package
from generative_agents.ga_replay.queries import definition_for, metadata
from generative_agents.ga_replay.reader import ReplayReader

_cache = OrderedDict()
_lock = RLock()
_MUTATIONS = {'world-act', 'memory-stream-append', 'memory-stream-supersede', 'memory-stream-invalidate'}


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _preview(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:800] + ('…' if len(text) > 800 else '')


def _signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _tool(call):
    function = call.get('function') or {}
    args = _json(function.get('arguments') or '{}')
    args = args if isinstance(args, dict) else {}
    return {'id': str(call.get('id') or ''), 'name': str(function.get('name') or ''),
            'arguments': args, 'signature': _signature(args)}


def _result(value):
    parsed = _json(value)
    action = parsed.get('action') if isinstance(parsed, dict) else None
    return {'preview': _preview(value), 'failed': isinstance(parsed, dict) and parsed.get('ok') is False,
            'memory_id': parsed.get('id') or parsed.get('memory_id') if isinstance(parsed, dict) else None,
            'action_type': action.get('action_type') if isinstance(action, dict) else None,
            'accepted': parsed.get('accepted') is True if isinstance(parsed, dict) else False}


def _compact(record):
    if 'span_id' in record and 'phase' in record:
        return dict(record, _execution=True)
    item = {key: value for key, value in record.items() if key != 'payload'}
    item['trace_id'] = f"{item.get('attempt_id', '')}:{item.get('event_seq', 0)}"
    payload = record.get('payload')
    item['payload_available'] = isinstance(payload, dict)
    payload = payload if isinstance(payload, dict) else {}
    request = payload.get('request') or []
    if isinstance(request, dict):
        request = request.get('messages') or []
    item['messages'] = []
    for message in request if isinstance(request, list) else []:
        if not isinstance(message, dict):
            continue
        if message.get('role') == 'assistant' and message.get('tool_calls'):
            item['messages'].append({'calls': [{'id': str(call.get('id') or '')} for call in message['tool_calls']]})
        elif message.get('role') == 'tool':
            item['messages'].append({'result_id': str(message.get('tool_call_id') or ''),
                                     'result': _result(message.get('content'))})
    response = payload.get('response')
    item['calls'] = [_tool(call) for call in response.get('tool_calls') or []] if isinstance(response, dict) else []
    item['output_preview'] = _preview(response.get('content') or '') if isinstance(response, dict) else _preview(response or '')
    return item


def _observations(package):
    """Tolerate a partial trailing JSONL record and parse appended bytes once."""
    with open_readonly_package(Path(package)) as root, _lock:
        source = str(Path(package).absolute())
        old = _cache.get(source, {})
        current = {}
        for raw in sorted(checked_package_path(root / 'traces').glob('*.jsonl')):
            path = checked_package_path(raw)
            signature = file_fingerprint(path)
            previous = old.get(path.name)
            if previous and previous['signature'] == signature:
                current[path.name] = previous
                continue
            stat = path.stat()
            append = previous and previous['signature'][:3] == signature[:3] and stat.st_size > previous['size']
            records = list(previous['records']) if append else []
            offset = previous['offset'] if append else 0
            with path.open('rb') as handle:
                handle.seek(offset)
                while line := handle.readline():
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeError):
                        if not line.endswith(b'\n'):
                            break
                        offset = handle.tell()
                        continue
                    if isinstance(record, dict):
                        records.append(_compact(record))
                    offset = handle.tell()
            current[path.name] = {'signature': signature, 'size': stat.st_size, 'offset': offset, 'records': records}
        _cache[source] = current
        _cache.move_to_end(source)
        while len(_cache) > 4:
            _cache.popitem(last=False)
        return [record for item in current.values() for record in item['records']]


def _calls(records):
    grouped = OrderedDict()
    for record in sorted(records, key=lambda row: int(row.get('event_seq') or 0)):
        if record.get('_execution'):
            continue
        if not record.get('call_id'):
            continue
        key = str(record['call_id'])
        previous = grouped.get(key)
        merged = dict(record)
        if previous:
            merged['first_seq'] = previous['first_seq']
            merged['started_at'] = previous.get('started_at') or record.get('started_at')
            if not record.get('payload_available') and previous.get('payload_available'):
                for field in ('messages', 'calls', 'output_preview', 'payload_available'):
                    merged[field] = previous[field]
        else:
            merged['first_seq'] = int(record.get('event_seq') or 0)
        grouped[key] = merged
    return list(grouped.values())


def _elapsed(started, ended, now):
    try:
        start = datetime.fromisoformat(started)
        end = datetime.fromisoformat(ended) if ended else now
        return max(0, int((end - start).total_seconds() * 1000))
    except (ValueError, TypeError):
        return None


def _node(identifier, parent, kind, name, *, skill=None, at=None, trace_id=None):
    return {'id': identifier, 'parent_id': parent, 'kind': kind, 'name': name, 'skill_key': skill,
            'status': 'UNKNOWN', 'started_at': at, 'ended_at': None, 'elapsed_ms': None,
            'trace_id': trace_id, 'model_status': 'NOT_APPLICABLE',
            'interface_status': 'NOT_APPLICABLE', 'commit_status': 'NOT_APPLICABLE',
            'evidence': [], 'input_preview': '', 'output_preview': ''}


def _actor_matches(item, actor, *, world=False):
    payload = item.get('payload') or {}
    structured = payload.get('structured_payload') or {}
    if actor.startswith('game-object:'):
        key = actor.removeprefix('game-object:')
        return structured.get('actor_object_key') == key if world else payload.get('object_key') == key
    # An object's reply can mention its recipient in agent_keys; this must not
    # be mistaken for an action performed by that recipient.
    return actor in (item.get('agent_keys') or []) and (not world or structured.get('actor_kind') != 'GAME_OBJECT')


def _tree(calls, *, actor, brain, names, frame, attempt_state, now):
    nodes, by_id, tools, stack = [], {}, {}, []
    diagnostics = []
    prefix = f"{actor}:"

    def add(node):
        nodes.append(node)
        by_id[node['id']] = node
        return node

    def tool_node(call, parent, record, ordinal):
        token = call['id']
        if not token:
            diagnostics.append('工具调用缺少稳定 ID，无法可靠关联返回结果。')
            return None
        scope = (parent['id'], ordinal, token)
        if scope in tools:
            return tools[scope]
        kind = 'skill' if call['name'] == 'call_skill' else 'interface'
        key = str(call['arguments'].get('name') or '') if kind == 'skill' else call['name']
        node = add(_node(prefix + 'tool:' + str(record['call_id']) + ':' + str(ordinal), parent['id'], kind, names.get(key, key),
                         skill=key if kind == 'skill' else parent.get('skill_key'),
                         at=record.get('ended_at'), trace_id=record.get('trace_id')))
        node.update(status='REQUESTED', input_preview=_preview(call['arguments']),
                    _tool=call['name'], _signature=call['signature'], _arguments=call['arguments'])
        node['evidence'].append('模型提出调用请求；请求本身不证明执行成功。')
        if kind == 'interface':
            node['interface_status'] = 'REQUESTED'
            node['commit_status'] = 'PENDING' if call['name'] in _MUTATIONS else 'NOT_APPLICABLE'
        tools[scope] = node
        return node

    for record in calls:
        key = str(record.get('prompt_key') or '')
        existing = next((node for node in reversed(stack) if node.get('skill_key') == key), None)
        pending = next((node for node in reversed(nodes) if node['kind'] == 'skill'
                        and node.get('skill_key') == key and node['status'] == 'REQUESTED'), None)
        if pending:
            skill = pending
            stack.append(skill)
        elif existing:
            skill = existing
            stack = stack[:stack.index(existing) + 1]
        else:
            skill = add(_node(prefix + 'skill:' + str(record['call_id']), None, 'skill',
                              names.get(key, key or '未知 SKILL'), skill=key,
                              at=record.get('started_at'), trace_id=record.get('trace_id')))
            stack = [skill]
            if key != brain:
                skill['evidence'].append('没有足够证据确认父调用，作为独立根展示。')
                diagnostics.append('部分 SKILL 缺少可确认的父调用证据，作为独立根展示。')
        if skill['status'] == 'REQUESTED':
            skill['status'] = 'RUNNING'
        # Tool IDs are local to a Skill conversation. Pair returns with the
        # preceding declaration in that message history, never actor-wide IDs.
        declaration_count, declarations = 0, {}
        for message in record.get('messages') or []:
            for call in message.get('calls') or []:
                declarations[call['id']] = (skill['id'], declaration_count, call['id'])
                declaration_count += 1
            node = tools.get(declarations.get(message.get('result_id')))
            if node and 'result' in message:
                result = message['result']
                node.update(status='FAILED' if result['failed'] else 'SUCCEEDED',
                            output_preview=result['preview'], _result=result)
                node['ended_at'] = node.get('ended_at') or record.get('started_at')
                if node['kind'] == 'interface':
                    node['interface_status'] = node['status']
                if '实际工具返回结果' not in node['evidence']:
                    node['evidence'].append('实际工具返回结果')
        model = add(_node(prefix + 'model:' + str(record['call_id']), skill['id'], 'model',
                          str(record.get('resolved_model') or '模型调用'), skill=key,
                          at=record.get('started_at'), trace_id=record.get('trace_id')))
        status = str(record.get('status') or 'UNKNOWN')
        if status == 'RUNNING' and attempt_state not in {'RUNNING', 'QUEUED', 'CREATED'}:
            status = 'UNKNOWN'
            model['evidence'].append('该 Attempt 已结束，但此模型请求缺少结束记录。')
        model.update(status=status, model_status=status, output_preview=record.get('output_preview') or '')
        model['ended_at'] = None if status == 'RUNNING' else record.get('ended_at')
        model['evidence'].append('模型审计记录；返回成功不等于接口执行或世界提交。')
        if status == 'RUNNING':
            skill['status'] = 'RUNNING'
        elif status in {'FAILED', 'ABORTED'}:
            skill['status'] = status
        if not record.get('payload_available'):
            diagnostics.append('部分调用未保存 Payload；仅能展示模型记录，无法补造父子调用与工具结果。')
        if status == 'SUCCEEDED':
            for index, call in enumerate(record.get('calls') or []):
                tool_node(call, skill, record, declaration_count + index)
            if not record.get('calls') and record.get('output_preview'):
                skill.update(status='SUCCEEDED', ended_at=record.get('ended_at'), output_preview=record['output_preview'])
                skill['evidence'].append('模型返回 SKILL 文本；是否回传父调用另由工具返回记录确认。')

    audit = []
    if frame:
        for effect in frame.get('effects') or []:
            if _actor_matches(effect, actor):
                audit.extend((effect.get('payload') or {}).get('trace') or [])
    rollback = any(item.get('event') == 'brain.rollback' for item in audit)
    consumed = set()
    for position, event in enumerate(audit):
        if event.get('event') != 'interface.call':
            continue
        args = _json(event.get('input_text') or '{}')
        signature = _signature(args)
        node = next((node for node in nodes if node['id'] not in consumed and node['kind'] == 'interface'
                     and node.get('_tool') == event.get('tool') and node.get('_signature') == signature
                     and node.get('skill_key') == event.get('skill')), None)
        if node is None:
            parent = next((node for node in reversed(nodes) if node['kind'] == 'skill'
                           and node.get('skill_key') == event.get('skill')), None)
            if parent is None:
                key = str(event.get('skill') or brain)
                parent = add(_node(prefix + 'audit-skill:' + key, None, 'skill', names.get(key, key), skill=key))
            node = add(_node(prefix + f'audit-interface:{position}', parent['id'], 'interface',
                             str(event.get('tool') or ''), skill=event.get('skill')))
            node.update(_tool=event.get('tool'), _signature=signature, _arguments=args,
                        input_preview=_preview(args))
        consumed.add(node['id'])
        result = _result(event.get('output_text'))
        interface_status = 'SUCCEEDED' if event.get('is_error') is False else 'FAILED' if event.get('is_error') is True else 'UNKNOWN'
        node.update(status=interface_status,
                    interface_status=interface_status,
                    output_preview=result['preview'], _result=result)
        node['evidence'].append('已提交 StepResult 中的真实接口执行记录')

    for node in nodes:
        tool = node.get('_tool')
        if node['kind'] == 'interface' and tool in _MUTATIONS:
            node['commit_status'] = 'PENDING'
            if node['interface_status'] == 'FAILED':
                node['commit_status'] = 'NOT_APPLICABLE'
            elif rollback or (not frame and attempt_state == 'CANCELLED'):
                node['commit_status'] = 'DISCARDED'
                node['evidence'].append('本轮回滚或取消；未进入已提交事实。')
            elif frame and node['interface_status'] == 'SUCCEEDED':
                result = node.get('_result') or {}
                if tool == 'world-act':
                    action_type = str(result.get('action_type') or
                                      (node.get('_arguments') or {}).get('action_type') or '').strip().upper()
                    committed = result.get('accepted') and any(
                        _actor_matches(event, actor, world=True) and
                        ((event.get('payload') or {}).get('structured_payload') or {}).get('action_type') ==
                        action_type
                        for event in frame.get('domain_events') or [])
                else:
                    memory_id = result.get('memory_id') or (node.get('_arguments') or {}).get('memory_id')
                    kinds = {'memory-stream-append': {'CREATED'}, 'memory-stream-supersede': {'CREATED', 'SUPERSEDED'},
                             'memory-stream-invalidate': {'INVALIDATED'}}[tool]
                    committed = bool(memory_id) and any(item.get('agent_key') == actor and
                        item.get('memory_id') == memory_id and item.get('kind') in kinds
                        for item in frame.get('memory_deltas') or [])
                node['commit_status'] = 'COMMITTED' if committed else 'UNKNOWN'
                node['evidence'].append('匹配已提交世界/记忆事实' if committed else '接口已执行，但缺少可匹配的提交事实。')
            elif attempt_state in {'FAILED', 'COMPLETED', 'PAUSED'} and not frame:
                node['commit_status'] = 'UNKNOWN'
        node['elapsed_ms'] = _elapsed(node.get('started_at'), node.get('ended_at'), now)
        for key in list(node):
            if key.startswith('_'):
                del node[key]
    diagnostics = list(dict.fromkeys(diagnostics))
    return {'nodes': nodes, 'root_ids': [node['id'] for node in nodes if node['parent_id'] is None],
            'complete': not diagnostics, 'diagnostics': diagnostics}


def _spans(events):
    spans = OrderedDict()
    for event in sorted(events, key=lambda row: int(row.get('event_seq') or 0)):
        span = spans.setdefault(str(event['span_id']), {})
        span.update({key: value for key, value in event.items() if value is not None})
        if event.get('phase') == 'START':
            span['started_at'] = event.get('timestamp')
        elif event.get('phase') == 'END':
            span['ended_at'] = event.get('timestamp')
        elif event.get('phase') == 'ROLLBACK':
            span['rolled_back'] = True
    return spans


def _audit_tree(calls, *, actor, brain, names, frame, attempt_state, now):
    """Committed recursive Skill events remain useful without model Payloads."""
    flat = _tree([], actor=actor, brain=brain, names=names, frame=frame, attempt_state=attempt_state, now=now)
    interfaces = {node['id']: node for node in flat['nodes'] if node['kind'] == 'interface'}
    events = [event for effect in frame.get('effects') or [] if _actor_matches(effect, actor)
              for event in (effect.get('payload') or {}).get('trace') or []]
    nodes, stack, diagnostics = [], [], []
    pending = None
    for index, event in enumerate(events):
        kind, key = event.get('event'), str(event.get('skill') or brain)
        if kind == 'skill.call':
            child = str(event.get('child') or '')
            pending = _node(f'{actor}:audit-child:{index}', stack[-1]['id'] if stack else None,
                            'skill', names.get(child, child), skill=child)
            pending.update(status='REQUESTED', input_preview=_preview(event.get('input_text') or ''))
            nodes.append(pending)
        elif kind == 'skill.start':
            node = pending if pending and pending['skill_key'] == key else None
            if node is None:
                node = _node(f'{actor}:audit-skill:{index}', stack[-1]['id'] if stack else None,
                             'skill', names.get(key, key), skill=key)
                nodes.append(node)
            node['status'] = 'RUNNING'
            node['evidence'].append('已提交 StepResult 中的 SKILL 开始与调用记录')
            stack.append(node)
            pending = None
        elif kind in {'skill.result', 'script.result'}:
            node = next((node for node in reversed(stack) if node['skill_key'] == key), None)
            if node:
                node.update(status='SUCCEEDED', output_preview=_preview(event.get('output_text') or ''))
                stack = stack[:stack.index(node)]
        elif kind == 'interface.call':
            node = interfaces.get(f'{actor}:audit-interface:{index}')
            if node:
                node['parent_id'] = stack[-1]['id'] if stack else None
                nodes.append(node)
    for call in calls:
        candidates = [node for node in nodes if node['kind'] == 'skill' and node['skill_key'] == call.get('prompt_key')]
        parent = candidates[0]['id'] if len(candidates) == 1 else None
        model = _node(f"{actor}:model:{call['call_id']}", parent, 'model', str(call.get('resolved_model') or '模型调用'),
                      skill=call.get('prompt_key'), at=call.get('started_at'), trace_id=call.get('trace_id'))
        model.update(status=call.get('status') or 'UNKNOWN', model_status=call.get('status') or 'UNKNOWN',
                     ended_at=call.get('ended_at'), elapsed_ms=call.get('latency_ms'))
        nodes.append(model)
        if parent is None:
            diagnostics.append('同名 SKILL 有多个调用或调用证据缺失，模型记录无法唯一归属，保留为独立节点。')
    return {'nodes': nodes, 'root_ids': [node['id'] for node in nodes if node['parent_id'] is None],
            'complete': not diagnostics, 'diagnostics': list(dict.fromkeys(diagnostics))}


def _span_tree(events, calls, *, actor, brain, names, frame, attempt_state, now):
    spans = _spans(events)
    nodes = []
    model_calls = {str(call['call_id']): call for call in calls}
    for identifier, span in spans.items():
        kind = span.get('kind')
        if kind not in {'skill', 'script', 'interface', 'model'}:
            continue
        parent_id = str(span.get('parent_span_id') or '') or None
        visited = set()
        skipped_container = False
        while parent_id in spans and spans[parent_id].get('kind') not in {'skill', 'script', 'interface', 'model'}:
            if parent_id in visited:
                parent_id = None
                break
            visited.add(parent_id)
            skipped_container = True
            parent_id = str(spans[parent_id].get('parent_span_id') or '') or None
        if skipped_container and parent_id not in spans:
            parent_id = None
        call = model_calls.get(str(span.get('call_id') or '')) or {}
        name = str(span.get('name') or span.get('skill_key') or '')
        node = _node(identifier, parent_id, kind, names.get(name, name),
                     skill=span.get('skill_key'), at=span.get('started_at'), trace_id=call.get('trace_id'))
        status = str(span.get('status') or 'UNKNOWN')
        if status == 'CANCELLED':
            status = 'ABORTED'
        if status == 'RUNNING' and attempt_state not in {'RUNNING', 'QUEUED', 'CREATED'}:
            status = 'ABORTED' if attempt_state == 'CANCELLED' else 'UNKNOWN'
        node.update(status=status, ended_at=span.get('ended_at'),
                    elapsed_ms=_elapsed(span.get('started_at'), span.get('ended_at'), now))
        node['evidence'].append('运行时持久化的调用 Span，父子关系与执行状态直接记录。')
        if kind == 'model':
            node['model_status'] = status
            node['output_preview'] = call.get('output_preview') or ''
        if kind == 'interface':
            node['interface_status'] = status
            if name in _MUTATIONS:
                node['commit_status'] = 'PENDING' if status == 'SUCCEEDED' else 'NOT_APPLICABLE' if status == 'FAILED' else 'UNKNOWN'
                if not frame and attempt_state == 'CANCELLED':
                    node['commit_status'] = 'DISCARDED'
        nodes.append(node)
    # Match the ordered committed interface audit to exact spans. Merely being
    # in the same committed Step does not commit a failed or rolled-back call.
    committed_tree = _tree([], actor=actor, brain=brain, names=names, frame=frame,
                           attempt_state=attempt_state, now=now)
    used = set()
    for audit in committed_tree['nodes']:
        if audit['kind'] != 'interface':
            continue
        target = next((node for node in nodes if node['kind'] == 'interface' and node['id'] not in used
                       and node['name'] == audit['name'] and node.get('skill_key') == audit.get('skill_key')), None)
        if target:
            used.add(target['id'])
            for field in ('interface_status', 'commit_status', 'input_preview', 'output_preview'):
                target[field] = audit[field]
            target['evidence'].extend(audit['evidence'])
    rollback = any(span.get('rolled_back') for span in spans.values())
    if rollback:
        for node in nodes:
            if node['kind'] == 'interface' and node['name'] in _MUTATIONS and node['interface_status'] != 'FAILED':
                node['commit_status'] = 'DISCARDED'
                node['evidence'].append('运行时记录本轮回滚；执行成功未成为提交事实。')
    identifiers = {node['id'] for node in nodes}
    diagnostics = []
    for node in nodes:
        if node['parent_id'] and node['parent_id'] not in identifiers:
            node['parent_id'] = None
            diagnostics.append('调用父 Span 缺失，保留为独立节点。')
    return {'nodes': nodes, 'root_ids': [node['id'] for node in nodes if node['parent_id'] is None],
            'complete': not diagnostics, 'diagnostics': list(dict.fromkeys(diagnostics))}


def execution_observation(package, *, attempt_id=None, step_no=None, agent_key=None, section='all'):
    """Observe live audit evidence without controlling a Run or executing code."""
    now = datetime.now(timezone.utc)
    records = _observations(package)
    with ReplayReader(package) as replay:
        summary = metadata(replay)
        definition = definition_for(replay)
        attempts = summary['attempts']
        active = str(replay.status.active_attempt_id or (attempts[-1]['attempt_id'] if attempts else ''))
        active_records = [row for row in records if str(row.get('attempt_id')) == active]
        active_calls = _calls(active_records)
        actor_defs = [item for item in definition.get('agents') or [] if item.get('enabled', True)]
        actor_names = {str(item['agent_key']): str(item.get('name') or item['agent_key']) for item in actor_defs}
        _header, resources, _assembly = read_experiment_documents(replay.root / replay.manifest.experiment.path)
        names = {item.key: item.name for item in resources.resources if item.kind in {'brain', 'skill'}}
        committed = int(summary['committed_step'])
        total = int(summary['requested_steps'])
        live_step = max((int(row.get('step_no') or 0) for row in active_records), default=0)
        current_step = max(live_step, min(committed + 1, total)) if summary['status'] in {'RUNNING', 'QUEUED', 'CREATED'} else max(live_step, committed)
        current = [row for row in active_calls if row.get('step_no') == current_step]
        observed = list(dict.fromkeys(str(row.get('agent_key')) for row in current if row.get('agent_key') in actor_names))
        latest = current[-1] if current else None
        live = latest if latest and latest.get('status') == 'RUNNING' and summary['status'] == 'RUNNING' else None
        actor = str(latest.get('agent_key') or '') if latest else None
        diagnostic = '人物计数表示本步已观察到模型调用；不是动作已完成或世界已提交的人数。'
        if latest and not live:
            diagnostic += '当前没有未结束的模型请求，展示最近活动；接口执行中状态无法从旧审计记录确认。'
        progress = {'committed_step': committed, 'total_steps': total, 'current_step': current_step,
                    'agent_total': len(actor_defs), 'completed_agents': len(actor_defs) if current_step <= committed else None,
                    'observed_agents': len(observed), 'current_actor': {'key': actor, 'name': actor_names.get(actor, actor),
                    'kind': 'agent' if actor in actor_names else 'object' if actor and actor.startswith('game-object:') else 'unknown',
                    'ordinal': observed.index(actor)+1 if actor in observed else None} if actor else None,
                    'current_skill': latest.get('prompt_key') if latest else None, 'current_interface': None,
                    'started_at': live.get('started_at') if live else None,
                    'elapsed_ms': _elapsed(live.get('started_at'), None, now) if live else None,
                    'phase': 'MODEL_RUNNING' if live else 'RECENT_ACTIVITY' if latest else summary['status'],
                    'available': bool(active_records), 'diagnostic': diagnostic, 'attempt_id': active,
                    'run_status': summary['status']}
        active_events = [row for row in active_records if row.get('_execution')]
        if active_events:
            progress.update(_span_progress(active_events, current_step, actor_names, summary['status'], now))
        progress['current_skill_name'] = names.get(progress.get('current_skill'), progress.get('current_skill'))
        response = {'run_id': summary['run_id'], 'generated_at': now.isoformat(), 'progress': progress}
        if section == 'progress':
            return response
        selected_attempt = str(attempt_id or active)
        selected = [row for row in records if str(row.get('attempt_id')) == selected_attempt]
        steps = sorted({int(row['step_no']) for row in selected if row.get('step_no')})
        selected_step = step_no if step_no is not None else max(steps, default=current_step)
        selected = [row for row in selected if row.get('step_no') == selected_step]
        keys = list(dict.fromkeys(str(row.get('agent_key')) for row in selected if row.get('agent_key')))
        selected_actor = agent_key or (keys[-1] if keys else next(iter(actor_names), ''))
        selected_calls = _calls([row for row in selected if row.get('agent_key') == selected_actor])
        frame = replay.read_step(selected_step) if 0 < selected_step <= committed else None
        if frame and str(frame.get('attempt_id')) != selected_attempt:
            frame = None
        attempt = next((item for item in attempts if str(item.get('attempt_id')) == selected_attempt), {})
        state = str(attempt.get('status') or 'UNKNOWN')
        if selected_attempt == active:
            state = summary['status']
        tree_args = dict(actor=selected_actor, brain=(definition.get('engine') or {}).get('brain_skill'),
                         names=names, frame=frame, attempt_state=state, now=now)
        events = [row for row in selected if row.get('_execution') and row.get('agent_key') == selected_actor]
        if events:
            tree = _span_tree(events, selected_calls, **tree_args)
        elif frame and not any(call.get('payload_available') for call in selected_calls):
            tree = _audit_tree(selected_calls, **tree_args)
        else:
            tree = _tree(selected_calls, **tree_args)
        response.update(attempt_id=selected_attempt, step_no=selected_step, agent_key=selected_actor,
                        selected_step=selected_step, selected_actor_key=selected_actor, steps=steps,
                        agents=[{'key': key, 'name': actor_names.get(key, key), 'observed': key in keys}
                                for key in dict.fromkeys([*actor_names, *keys])], tree=tree)
        return response


def _span_progress(events, step_no, actor_names, run_status, now):
    spans = _spans([event for event in events if event.get('step_no') == step_no])
    all_actors = [span for span in spans.values() if span.get('kind') == 'actor']
    actors = [span for span in all_actors if span.get('agent_key') in actor_names]
    unfinished = [span for span in spans.values() if not span.get('ended_at') and span.get('status') == 'RUNNING']
    active = unfinished[-1] if unfinished and run_status == 'RUNNING' else None
    actor = next((span for span in reversed(all_actors) if active and span.get('agent_key') == active.get('agent_key')), None)
    return {'completed_agents': sum(bool(span.get('ended_at')) and span.get('status') == 'SUCCEEDED' for span in actors),
            'observed_agents': len({span['agent_key'] for span in actors}),
            'current_actor': {'key': actor['agent_key'], 'name': actor_names.get(actor['agent_key']) or actor.get('actor_name') or actor['agent_key'],
                              'kind': actor.get('actor_kind') or ('agent' if actor['agent_key'] in actor_names else 'object'),
                              'ordinal': actor.get('actor_index') if actor['agent_key'] in actor_names else None} if actor else None,
            'current_skill': active.get('skill_key') if active else None,
            'current_interface': active.get('name') if active and active.get('kind') == 'interface' else None,
            'started_at': active.get('started_at') if active else None,
            'elapsed_ms': _elapsed(active.get('started_at'), None, now) if active else None,
            'phase': str(active.get('kind')).upper() + '_RUNNING' if active else run_status,
            'available': True, 'diagnostic': '本步人物进度来自运行时开始/结束记录；世界生效仍以已提交 StepResult 为准。'}
