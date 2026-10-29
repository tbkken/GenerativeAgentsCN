"""Reduce conversation facts without consulting mutable runtime state."""
from __future__ import annotations

from generative_agents.ga_protocol.packages.io import PackageError


def merge_conversation(previous, raw, *, step_no, virtual_time, names=None):
    """Append message deltas while retaining their original identity and order."""
    names = names or {}
    conversation_id = str(raw['conversation_id'])
    participants = list(raw.get('participant_agent_keys') or [])
    visibility = raw.get('visibility') or 'private'
    kind = raw.get('kind') or 'direct'
    if previous is not None:
        for field, value in (('participants', participants), ('visibility', visibility), ('kind', kind)):
            if previous.get(field, value) != value:
                raise PackageError(f'Conversation {conversation_id} changed {field}')
        item = {**previous, 'messages': list(previous['messages'])}
    else:
        item = {
            'conversation_id': conversation_id, 'start_step': step_no,
            'started_at': virtual_time, 'duration_minutes': 0,
            'duration_source': raw.get('duration_source') or 'RECORDED',
            'location': ' / '.join(str(value) for value in raw.get('location') or []),
            'participants': participants, 'participant_agent_keys': participants,
            'participant_names': [names.get(str(key), str(key)) for key in participants],
            'visibility': visibility, 'kind': kind, 'space_id': raw.get('space_id'),
            'message_count': 0, 'summary': None, 'ended_reason': None, 'messages': [],
        }
    known = {message['message_id']: message for message in item['messages']}
    for message in raw.get('messages') or []:
        message_id = str(message.get('message_id') or f"{conversation_id}:{message.get('sequence')}")
        if message_id in known:
            if any(known[message_id].get(field) != value for field, value in message.items()):
                raise PackageError(f'Message {message_id} has conflicting facts')
            continue
        parent_id = message.get('reply_to_message_id')
        if parent_id and str(parent_id) not in known:
            raise PackageError(f'Message {message_id} replies to an unavailable message')
        speaker = str(message.get('speaker_agent_key') or '')
        view = {
            **message, 'message_id': message_id,
            'speaker_name': names.get(speaker, speaker),
            'observed_at': virtual_time, 'step_no': message.get('step_no') or step_no,
        }
        item['messages'].append(view)
        known[message_id] = view
    item['messages'].sort(key=lambda message: (
        int(message.get('step_no') or 0), int(message.get('actor_sequence') or 0),
        int(message.get('sequence') or 0),
    ))
    item.update(
        message_count=len(item['messages']),
        duration_minutes=raw.get('duration_minutes') or item['duration_minutes'],
        summary=raw.get('summary') or item['summary'],
        ended_reason=raw.get('ended_reason') or item['ended_reason'],
    )
    return item
