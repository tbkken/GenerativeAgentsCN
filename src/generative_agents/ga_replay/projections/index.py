"""Incremental list/detail projections over newly committed StepResults."""
from __future__ import annotations

from generative_agents.ga_replay.projections.console import agent_views, definition_names, event_view


def advance_views(facts, previous=None, *, start=0):
    names = definition_names(facts)
    stride = int(facts['definition'].get('simulation', {}).get('stride_minutes') or 1)
    if previous is None:
        initial, details = agent_views({**facts, 'frames': []})
        agents = {item['agent_key']: item for item in initial}
        conversations, memories = {}, {}
    else:
        agents = {item['agent_key']: {**item, 'activity_minutes': dict(item['activity_minutes'])}
                  for item in previous['agents']}
        details = {key: {**item, 'agent': agents[key], **{field: list(item[field]) for field in (
            'steps', 'actions', 'events', 'plan_revisions', 'state_changes')}}
                   for key, item in previous['agent_details'].items()}
        # Sorted public lists have immutable records; replace records on update.
        conversations = {item['conversation_id']: item for item in previous['conversations']}
        memories = {(item['agent_key'], item['memory_id']): item for item in previous['memories']}
    for frame in facts['frames'][start:]:
        step, virtual_time = frame['step_no'], frame['virtual_time']
        for raw in frame.get('conversations') or []:
            key = str(raw.get('conversation_id') or '')
            if not key:
                continue
            participants = list(raw.get('participant_agent_keys') or [])
            old = conversations.get(key)
            item = dict(old) if old else {'conversation_id': key, 'start_step': step, 'started_at': virtual_time,
                'duration_minutes': 0, 'duration_source': raw.get('duration_source') or 'RECORDED',
                'location': ' / '.join(str(value) for value in raw.get('location') or []),
                'participants': participants, 'participant_names': [names.get(str(value), str(value)) for value in participants],
                'message_count': 0, 'summary': None, 'ended_reason': None, 'messages': []}
            messages = list(item['messages'])
            known = {message['message_id'] for message in messages}
            for message in raw.get('messages') or []:
                message_id = str(message.get('message_id') or f"{key}:{message.get('sequence')}")
                if message_id in known:
                    continue
                known.add(message_id)
                speaker = str(message.get('speaker_agent_key') or '')
                messages.append({**message, 'message_id': message_id, 'speaker_name': names.get(speaker, speaker), 'observed_at': virtual_time})
            item.update(messages=messages, message_count=len(messages),
                        duration_minutes=raw.get('duration_minutes') or item['duration_minutes'],
                        summary=raw.get('summary') or item['summary'], ended_reason=raw.get('ended_reason') or item['ended_reason'])
            conversations[key] = item
        for raw in frame.get('memory_deltas') or []:
            agent, key = str(raw.get('agent_key') or ''), str(raw.get('memory_id') or '')
            if not agent or not key:
                continue
            old = memories.get((agent, key))
            item = dict(old) if old else {'memory_id': key, 'agent_key': agent, 'agent_name': names.get(agent, agent),
                'type': raw.get('memory_type') or 'EVENT', 'origin': 'STEP_RESULT', 'state': 'ACTIVE',
                'description': raw.get('description'), 'poignancy': raw.get('poignancy'), 'created_step': step,
                'created_at': raw.get('created_at') or virtual_time, 'last_accessed_step': None, 'removed_step': None,
                'supersedes_memory_id': raw.get('supersedes_memory_id'), 'superseded_by_memory_id': None, 'invalidated_reason': None}
            kind = raw.get('kind') or 'CREATED'
            if kind == 'ACCESSED':
                item['last_accessed_step'] = step
            elif kind in {'CREATED', 'EXPIRED', 'EVICTED', 'SUPERSEDED', 'INVALIDATED'}:
                item['state'] = 'ACTIVE' if kind == 'CREATED' else kind
                if kind != 'CREATED':
                    item['removed_step'] = step
            item['description'] = raw.get('description') or item['description']
            if raw.get('poignancy') is not None:
                item['poignancy'] = raw['poignancy']
            if kind == 'SUPERSEDED':
                item['superseded_by_memory_id'] = raw.get('replacement_memory_id')
            if kind == 'INVALIDATED':
                item['invalidated_reason'] = raw.get('reason')
            memories[(agent, key)] = item
        for raw in frame.get('agents') or []:
            key = str(raw.get('agent_key') or '')
            if key not in agents:
                continue
            item, detail = agents[key], details[key]
            action = dict(raw.get('action') or {})
            activity = raw.get('activity_kind') or 'OTHER'
            coord = list(raw.get('to_coord') or raw.get('coord') or item['coord'])
            address = ' / '.join(str(value) for value in raw.get('location') or raw.get('address') or [])
            view = {'step_no': step, 'virtual_time': virtual_time, 'coord': coord, 'address': address,
                    'action': action.get('description') or '', 'emoji': action.get('emoji'), 'activity_kind': activity,
                    'sample_kind': raw.get('path_source') or 'OBSERVED', 'currently': raw.get('currently')}
            before = item['currently']
            item['currently'] = raw.get('currently') or action.get('description') or before
            if item['updated_step'] and before != item['currently']:
                detail['state_changes'].append({'step_no': step, 'title': '当前状态', 'before': before, 'after': item['currently'], 'kind': 'STATE'})
            detail['steps'].insert(0, view)
            detail['actions'].insert(0, view)
            item.update(coord=coord, address=address, updated_step=step, latest_activity_kind=activity,
                        latest_action=action.get('description') or '', latest_virtual_time=virtual_time,
                        run_status=facts['summary']['status'])
            item['action_count'] += 1
            item['movement_steps'] += int(list(raw.get('from_coord') or coord) != coord)
            item['activity_minutes'][activity] = item['activity_minutes'].get(activity, 0) + stride
        for raw in frame.get('schedule_revisions') or []:
            key = str(raw.get('agent_key') or '')
            if key in details:
                detail = details[key]
                view = {'revision_no': len(detail['plan_revisions'])+1, 'effective_step': step,
                        'effective_at': virtual_time, 'reason': raw.get('reason') or '计划更新', 'items': list(raw.get('schedule') or [])}
                detail['plan_revisions'].insert(0, view)
                detail['latest_schedule'] = view
        for raw in frame.get('domain_events') or []:
            view = event_view(raw, step_no=step, virtual_time=virtual_time, names=names)
            for key in raw.get('agent_keys') or []:
                if str(key) in details:
                    details[str(key)]['events'].insert(0, view)
    conversation_items = sorted(conversations.values(), key=lambda item: item['start_step'], reverse=True)
    memory_items = sorted(memories.values(), key=lambda item: (int(item['created_step']), item['agent_key'], item['memory_id']), reverse=True)
    for key, item in agents.items():
        detail = details[key]
        detail['conversations'] = [value for value in conversation_items if key in value['participants']]
        detail['memories'] = [value for value in memory_items if value['agent_key'] == key]
        item['conversation_count'] = len(detail['conversations'])
        item['message_count'] = sum(value['message_count'] for value in detail['conversations'])
        item['memory_created_count'] = len(detail['memories'])
        item['plan_count'], item['event_count'] = len(detail['plan_revisions']), len(detail['events'])
        detail['content_counts'] = {'plans': item['plan_count'], 'actions': item['action_count'], 'events': item['event_count'],
            'conversations': item['conversation_count'], 'memories': item['memory_created_count'], 'state_changes': len(detail['state_changes'])}
    return {'agents': sorted(agents.values(), key=lambda item: item['agent_key']), 'agent_details': details,
            'conversations': conversation_items, 'memories': memory_items}
