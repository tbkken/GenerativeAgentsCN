"""Results HTTP routes."""
from __future__ import annotations
import hashlib
import json
from typing import Any
from fastapi import HTTPException, Query, Response
from generative_agents.ga_replay.api import ReplayReader
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import checked_package_path
from generative_agents.ga_replay.api import definition_names, event_view, conversation_views, memory_views, agent_views, checkpoint_documents

def install_routes(router, ctx):

    @router.get('/runs/{run_id}/results/timeline')
    def result_timeline(run_id: str, from_step: int=Query(default=1, ge=1), to_step: int | None=Query(default=None, ge=1), limit: int=Query(default=200, ge=1, le=500)):
        facts = ctx.read_run_facts(run_id)
        names = definition_names(facts)
        frames = [frame for frame in facts['frames'] if frame['step_no'] >= from_step and (to_step is None or frame['step_no'] <= to_step)][:limit]
        events = [event_view(raw, step_no=frame['step_no'], virtual_time=frame['virtual_time'], names=names) for frame in frames for raw in frame.get('domain_events') or []]
        agent_steps = []
        steps = []
        for frame in frames:
            agents = list(frame.get('agents') or [])
            conversations = list(frame.get('conversations') or [])
            memories = list(frame.get('memory_deltas') or [])
            usage = list(frame.get('committed_model_usage') or [])
            steps.append({'step_no': frame['step_no'], 'virtual_time': frame['virtual_time'], 'actions': len(agents), 'movements': sum((list(item.get('from_coord') or []) != list(item.get('to_coord') or []) for item in agents)), 'conversations': len(conversations), 'messages': sum((len(item.get('messages') or []) for item in conversations)), 'memories_created': sum((str(item.get('kind')) == 'CREATED' for item in memories)), 'model_calls': len(usage), 'checkpoint': frame['step_no'] in facts['checkpoint_steps'], 'sample_kind': 'OBSERVED'})
            for agent in agents:
                key = str(agent.get('agent_key') or '')
                action = agent.get('action') or {}
                agent_steps.append({'step_no': frame['step_no'], 'agent_key': key, 'agent_name': names.get(key, key), 'coord': list(agent.get('to_coord') or []), 'address': ' / '.join((str(value) for value in agent.get('location') or [])), 'action': action.get('description') or '', 'emoji': action.get('emoji'), 'activity_kind': agent.get('activity_kind') or 'OTHER', 'sample_kind': agent.get('path_source') or 'OBSERVED'})
        return {'run_id': run_id, 'available_step': facts['summary']['committed_step'], 'requested_steps': facts['summary']['requested_steps'], 'steps': steps, 'events': events, 'agent_steps': agent_steps}

    @router.get('/runs/{run_id}/results/agents')
    def result_agents(run_id: str):
        facts = ctx.read_run_facts(run_id)
        items, _details = agent_views(facts)
        return {'run_id': run_id, 'items': items}

    @router.get('/runs/{run_id}/results/agents/{agent_key}')
    def result_agent(run_id: str, agent_key: str):
        facts = ctx.read_run_facts(run_id)
        _items, details = agent_views(facts)
        if agent_key not in details:
            raise HTTPException(status_code=404, detail='Agent is not present in this Run package')
        return details[agent_key]

    @router.get('/runs/{run_id}/results/conversations')
    def result_conversations(run_id: str, agent_key: str | None=None, q: str='', offset: int=Query(default=0, ge=0), limit: int=Query(default=50, ge=1, le=100)):
        facts = ctx.read_run_facts(run_id)
        items = conversation_views(facts, definition_names(facts))
        if agent_key:
            items = [item for item in items if agent_key in item['participants']]
        if q:
            needle = q.casefold()
            items = [item for item in items if needle in json.dumps(item, ensure_ascii=False).casefold()]
        selected = items[offset:offset + limit]
        return {'run_id': run_id, 'items': [{key: value for key, value in item.items() if key != 'messages'} for item in selected], 'next_offset': offset + limit if offset + limit < len(items) else None}

    @router.get('/runs/{run_id}/results/conversations/{conversation_id}')
    def result_conversation(run_id: str, conversation_id: str):
        facts = ctx.read_run_facts(run_id)
        for item in conversation_views(facts, definition_names(facts)):
            if item['conversation_id'] == conversation_id:
                return {'run_id': run_id, **item}
        raise HTTPException(status_code=404, detail='Conversation is not present in this Run package')

    @router.get('/runs/{run_id}/results/memories')
    def result_memories(run_id: str, agent_key: str | None=None, memory_type: str | None=None, state: str | None=None, q: str='', offset: int=Query(default=0, ge=0), limit: int=Query(default=50, ge=1, le=100)):
        facts = ctx.read_run_facts(run_id)
        items = memory_views(facts, definition_names(facts))
        if agent_key:
            items = [item for item in items if item['agent_key'] == agent_key]
        if memory_type:
            items = [item for item in items if item['type'] == memory_type]
        if state:
            items = [item for item in items if item['state'] == state]
        if q:
            needle = q.casefold()
            items = [item for item in items if needle in str(item.get('description') or '').casefold()]
        return {'run_id': run_id, 'items': items[offset:offset + limit], 'next_offset': offset + limit if offset + limit < len(items) else None}

    @router.get('/runs/{run_id}/results/operations')
    def result_operations(run_id: str):
        facts = ctx.read_run_facts(run_id)
        usage: dict[tuple[str, str, str], dict[str, Any]] = {}
        for raw in facts['traces']:
            event_type = raw.get('event_type')
            if event_type not in {'LOGICAL_END', 'PHYSICAL_START', 'PHYSICAL_ATTEMPT'}:
                continue
            key = (str(raw.get('purpose') or 'unknown'), str(raw.get('provider') or 'unknown'), str(raw.get('resolved_model') or 'unknown'))
            item = usage.setdefault(key, dict(purpose=key[0], provider=key[1], model=key[2], logical_calls=0, physical_attempts=0, retries=0, input_tokens=0, output_tokens=0, max_latency_ms=0))
            if event_type == 'LOGICAL_END':
                item['logical_calls'] += 1
            elif event_type == 'PHYSICAL_ATTEMPT':
                item['physical_attempts'] += 1
                item['retries'] += int(int(raw.get('attempt_no') or 1) > 1)
                item['input_tokens'] += int(raw.get('prompt_tokens') or 0)
                item['output_tokens'] += int(raw.get('completion_tokens') or 0)
                item['max_latency_ms'] = max(item['max_latency_ms'], int(raw.get('latency_ms') or 0))
        return {'run_id': run_id, 'run_status': facts['summary']['status'], 'usage_consistency': 'RUN_TRACE_EVENTS', 'usage_committed_through_step': facts['summary']['committed_step'], 'attempts': facts['summary'].get('attempts') or [], 'model_usage': list(usage.values()), 'artifacts': facts['artifacts'], 'artifact_jobs': []}

    @router.get('/runs/{run_id}/events')
    def run_events(run_id: str, after_id: int=Query(default=0, ge=0), limit: int=Query(default=200, ge=1, le=500)):
        facts = ctx.read_run_facts(run_id)
        names = definition_names(facts)
        events = []
        sequence = 0
        for frame in facts['frames']:
            for raw in frame.get('domain_events') or []:
                sequence += 1
                if sequence <= after_id:
                    continue
                view = event_view(raw, step_no=frame['step_no'], virtual_time=frame['virtual_time'], names=names)
                events.append({'id': sequence, 'event_type': view['event_type'], 'created_at': frame['virtual_time'], 'payload': {'step_no': frame['step_no'], 'event_id': view['event_id'], **view['payload']}})
                if len(events) >= limit:
                    break
            if len(events) >= limit:
                break
        return {'items': events, 'next_after_id': events[-1]['id'] if events else after_id}

    @router.get('/runs/{run_id}/attempts')
    def run_attempts(run_id: str):
        facts = ctx.read_run_facts(run_id)
        frame_steps: dict[str, list[int]] = {}
        for frame in facts['frames']:
            frame_steps.setdefault(str(frame.get('attempt_id') or ''), []).append(frame['step_no'])
        items = []
        for raw in facts['summary'].get('attempts') or []:
            attempt_id = str(raw.get('attempt_id') or '')
            steps = frame_steps.get(attempt_id) or []
            items.append({'attempt_id': attempt_id, 'attempt_no': raw.get('ordinal') or len(items) + 1, 'status': raw.get('status') or 'UNKNOWN', 'start_step': int(raw.get('resumed_from_step') or 0) + 1, 'end_step': max(steps) if steps else raw.get('resumed_from_step'), 'stop_reason': raw.get('failure') or raw.get('status'), 'started_at': raw.get('started_at'), 'ended_at': raw.get('finished_at'), 'error_message': raw.get('failure'), 'log': facts['log']})
        return {'run_id': run_id, 'items': items, 'default_attempt_id': facts['status'].get('active_attempt_id') or (items[-1]['attempt_id'] if items else None)}

    @router.get('/runs/{run_id}/model-traces')
    def run_model_traces(run_id: str, attempt_id: str | None=None, event_type: str | None=None, purpose: str='', cursor: int=Query(default=0, ge=0), limit: int=Query(default=200, ge=1, le=500)):
        records = ctx.trace_records_for(run_id)
        if attempt_id:
            records = [item for item in records if item.get('attempt_id') == attempt_id]
        if event_type == 'PHYSICAL':
            records = [item for item in records if str(item.get('event_type') or '').startswith('PHYSICAL')]
        elif event_type:
            records = [item for item in records if item.get('event_type') == event_type]
        if purpose:
            records = [item for item in records if purpose.casefold() in str(item.get('purpose') or '').casefold()]
        selected = records[cursor:cursor + limit]
        next_cursor = cursor + len(selected)
        return {'items': selected, 'next_cursor': next_cursor, 'eof': next_cursor >= len(records)}

    @router.get('/runs/{run_id}/model-traces/{trace_id}')
    def run_model_trace_detail(run_id: str, trace_id: str, cursor: int=Query(default=0, ge=0), limit_bytes: int=Query(default=16384, ge=1, le=1048576)):
        record = next((item for item in ctx.trace_records_for(run_id) if item['trace_id'] == trace_id), None)
        if record is None:
            raise HTTPException(status_code=404, detail='Model trace is not present in this Run package')
        iteration_tools = []
        with ReplayReader(ctx.run_location(run_id)) as replay:
            for frame in replay.iter_steps(start=int(record.get('step_no') or 1), end=int(record.get('step_no') or 1)):
                if frame.get('step_no') != record.get('step_no') or frame.get('attempt_id') != record.get('attempt_id'):
                    continue
                for effect in frame.get('effects') or []:
                    if record.get('agent_key') not in (effect.get('agent_keys') or []):
                        continue
                    iteration_tools.extend((item for item in (effect.get('payload') or {}).get('trace', []) if item.get('event') == 'mcp.call'))
        payload = record.get('payload')
        content = json.dumps(payload, ensure_ascii=False, indent=2) if payload is not None else ''
        chunk = content[cursor:cursor + limit_bytes]
        next_cursor = cursor + len(chunk)
        return {'trace': record, 'iteration_tools': iteration_tools, 'payload_diagnostic': '该 Run 未保存此请求的模型 Payload，无法还原历史请求全文；下方展示已有的同轮 MCP 事实记录。', 'payload_available': payload is not None, 'content': chunk, 'next_cursor': next_cursor if next_cursor < len(content) else None, 'file_id': record.get('payload_sha256')}

    @router.get('/runs/{run_id}/attempts/{attempt_id}/log')
    def run_attempt_log(run_id: str, attempt_id: str, cursor: int=Query(default=0, ge=0), limit_bytes: int=Query(default=65536, ge=1, le=262144)):
        facts = ctx.read_run_facts(run_id)
        if attempt_id not in {str(item.get('attempt_id')) for item in facts['summary'].get('attempts') or []}:
            raise HTTPException(status_code=404, detail='Attempt is not present in this Run package')
        from generative_agents.ga_protocol.packages.byte_windows import read_utf8_window
        from generative_agents.ga_studio.api import ServiceError
        terminal = facts['status']['status'] in {'PAUSED', 'CANCELLED', 'COMPLETED', 'FAILED'}
        with open_package(ctx.run_location(run_id)) as root:
            path = checked_package_path(root / 'logs' / 'runtime-process.log')
            if not path.is_file():
                return {'content': '', 'next_cursor': 0, 'file_id': None, 'starts_mid_line': False, 'eof': True, 'terminal': terminal}
            try:
                window = read_utf8_window(path, cursor=cursor, limit_bytes=limit_bytes)
            except ServiceError as exc:
                raise HTTPException(status_code=exc.status_code, detail={'code': exc.code, 'message': exc.message}) from exc
            starts_mid_line = False
            if cursor:
                with path.open('rb') as handle:
                    handle.seek(cursor - 1)
                    starts_mid_line = handle.read(1) not in {b'\n', b'\r'}
        return {'content': window.content, 'next_cursor': window.next_cursor, 'file_id': window.file_id, 'starts_mid_line': starts_mid_line, 'eof': window.eof, 'terminal': terminal}

    @router.get('/runs/{run_id}/attempts/{attempt_id}/log/download')
    def download_run_attempt_log(run_id: str, attempt_id: str):
        facts = ctx.read_run_facts(run_id)
        if attempt_id not in {str(item.get('attempt_id')) for item in facts['summary'].get('attempts') or []}:
            raise HTTPException(status_code=404, detail='Attempt is not present in this Run package')
        content, _terminal = ctx.run_log_bytes(run_id)
        return Response(content=content, media_type='text/plain; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="run-{run_id}-attempt-{attempt_id}.log"'})

    @router.get('/runs/{run_id}/checkpoints')
    def run_checkpoints(run_id: str):
        return {'run_id': run_id, 'items': checkpoint_documents(ctx.run_location(run_id), run_id)}

    @router.get('/runs/{run_id}/checkpoints/{step_no}')
    def run_checkpoint(run_id: str, step_no: int):
        summary = next((item for item in checkpoint_documents(ctx.run_location(run_id), run_id) if item['step_no'] == step_no), None)
        if summary is None:
            raise HTTPException(status_code=404, detail='Checkpoint is not present in this Run package')
        with open_package(ctx.run_location(run_id)) as root:
            checkpoint = root / summary['snapshot_kind'] / f'step-{step_no:06d}'
            state = read_json(checkpoint / 'state.json') if summary['validated'] else {}
            conversation = read_json(checkpoint / 'conversation.json') if summary['validated'] else {}
        state_agents = state.get('agents') if isinstance(state, dict) else {}
        agent_items = []
        if isinstance(state_agents, dict):
            for key, value in sorted(state_agents.items()):
                value = value if isinstance(value, dict) else {}
                agent_items.append({'agent_key': key, 'coord': value.get('coord'), 'currently': value.get('currently'), 'action': value.get('action') or {}, 'schedule_item_count': len((value.get('schedule') or {}).get('daily_schedule') or []) if isinstance(value.get('schedule'), dict) else len(value.get('schedule') or [])})
        conversation_items = []
        if isinstance(conversation, dict):
            candidate = conversation.get('items') or conversation.get('conversations') or []
            if isinstance(candidate, list):
                conversation_items = candidate
        storage_groups: dict[tuple[str, str], dict[str, Any]] = {}
        for item in summary['files']:
            relative = str(item.get('path') or '')
            parts = relative.split('/')
            if len(parts) < 3 or parts[0] not in {'storage', 'runtime-storage'}:
                continue
            key = (parts[1], parts[2] if len(parts) > 2 else parts[0])
            group = storage_groups.setdefault(key, {'agent_key': key[0], 'index_type': key[1], 'file_count': 0, 'size_bytes': 0})
            group['file_count'] += 1
            group['size_bytes'] += int(item.get('size') or 0)
        return {**summary, 'agent_state': {'count': len(agent_items), 'items': agent_items}, 'conversations': {'count': len(conversation_items), 'items': conversation_items}, 'storage': {'group_count': len(storage_groups), 'groups': list(storage_groups.values())}, 'files': [{'path': 'bundle.json', 'size_bytes': 0, 'sha256': summary['bundle_sha256']}, *[{'path': item.get('path'), 'size_bytes': item.get('size') or 0, 'sha256': item.get('sha256') or ''} for item in summary['files']]]}

    @router.get('/runs/{run_id}/checkpoints/{step_no}/preview')
    def run_checkpoint_preview(run_id: str, step_no: int, section: str=Query(pattern='^(state|conversation)$'), cursor: int=Query(default=0, ge=0), limit_bytes: int=Query(default=32768, ge=1, le=1048576), file_id: str | None=None):
        del file_id
        with open_package(ctx.run_location(run_id)) as root:
            path = root / 'checkpoints' / f'step-{step_no:06d}' / f'{section}.json'
            if not path.is_file():
                path = root / 'recovery' / f'step-{step_no:06d}' / f'{section}.json'
            if not path.is_file():
                raise HTTPException(status_code=404, detail='Checkpoint preview is not present')
            content = path.read_bytes()
        chunk = content[cursor:cursor + limit_bytes]
        next_cursor = cursor + len(chunk)
        return {'content': chunk.decode('utf-8', errors='replace'), 'next_cursor': next_cursor if next_cursor < len(content) else None, 'file_id': hashlib.sha256(content).hexdigest()}
