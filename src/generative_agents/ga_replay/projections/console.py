"""Read-only views derived exclusively from committed Run files."""
from __future__ import annotations
import hashlib
import json
import mimetypes
from typing import Any
from generative_agents.ga_replay.reader import ReplayReader
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import checked_package_path
from generative_agents.ga_protocol.packages.io import iter_package_files
from generative_agents.ga_protocol.packages.artifacts import read_artifact_provenance
from generative_agents.ga_protocol.packages.definition import _experiment_definition

def read_run_facts_unlocked(run_path, run_id: str) -> dict[str, Any]:
    """Build Web projections exclusively from one Run package."""
    with ReplayReader(run_path) as replay:
        summary = replay.summary()
        definitions = replay.experiment_agents()
        frames = list(replay.iter_steps())
        root, manifest, status = (replay.root, replay.manifest, replay.status)
        if root is None or manifest is None or status is None:
            raise PackageError('Replay reader did not open the Run package')
        _experiment_manifest, definition = _experiment_definition(root / manifest.experiment.path)
        checkpoint_steps = {int(path.name.removeprefix('step-')) for path in (root / 'checkpoints').glob('step-*') if path.name.removeprefix('step-').isdigit() and int(path.name.removeprefix('step-')) <= status.committed_step and (path / 'bundle.json').is_file()}
        artifacts = []
        artifact_root = checked_package_path(root / 'artifacts')
        if artifact_root.is_dir():
            for _relative, path in iter_package_files(artifact_root):
                if not path.is_file() or path.is_symlink() or path.name.startswith('.'):
                    continue
                content = path.read_bytes()
                relative = path.relative_to(artifact_root).as_posix()
                digest = hashlib.sha256(content).hexdigest()
                artifacts.append({'artifact_id': digest[:32], 'type': path.suffix.removeprefix('.').upper() or 'FILE', 'logical_name': relative, 'media_type': mimetypes.guess_type(path.name)[0] or 'application/octet-stream', 'size_bytes': len(content), 'sha256': digest, **read_artifact_provenance(root, run_id=run_id, logical_name=relative, digest=digest, size_bytes=len(content)), 'state': 'READY'})
        log_path = checked_package_path(root / 'logs' / 'runtime-process.log')
        log = {'available': log_path.is_file(), 'size_bytes': log_path.stat().st_size if log_path.is_file() else 0}
        trace_records = []
        trace_root = root / 'traces'
        if trace_root.is_dir():
            for path in sorted(trace_root.glob('*.jsonl')):
                for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        item['trace_id'] = f"{item.get('attempt_id', '')}:{item.get('event_seq', 0)}"
                        trace_records.append(item)
    return {'summary': summary, 'definitions': definitions, 'definition': definition, 'frames': frames, 'status': status.model_dump(mode='json'), 'checkpoint_steps': checkpoint_steps, 'artifacts': artifacts, 'log': log, 'traces': trace_records}

def definition_names(facts: dict[str, Any]) -> dict[str, str]:
    return {str(item.get('agent_key')): str(item.get('name') or item.get('display_name') or item.get('agent_key')) for item in facts['definitions'] if item.get('agent_key')}

def packaged_asset_url(run_id: str, logical_path: object) -> str | None:
    value = str(logical_path or '')
    if not value.startswith('assets/'):
        return None
    return f"/api/studio/runs/{run_id}/replay/assets/{value.removeprefix('assets/')}"

def event_view(self_event: dict[str, Any], *, step_no: int, virtual_time: str, names: dict[str, str]) -> dict[str, Any]:
    payload = dict(self_event.get('payload') or {})
    agent_keys = list(self_event.get('agent_keys') or [])
    primary = agent_keys[0] if agent_keys else None
    structured = payload.get('structured_payload') or {}
    address = structured.get('after_address') or structured.get('address') or []
    title = payload.get('description') or structured.get('description') or ''
    if not title:
        title = ' / '.join((str(payload.get(key) or '') for key in ('subject', 'predicate', 'object') if payload.get(key))) or str(self_event.get('event_type') or '事件')
    return {'event_id': str(self_event.get('event_id') or f"{step_no}:{self_event.get('sequence', 0)}"), 'step_no': step_no, 'virtual_time': virtual_time, 'event_type': str(self_event.get('event_type') or 'DOMAIN_EVENT'), 'primary_agent_key': primary, 'primary_agent_name': names.get(str(primary), str(primary or '')), 'title': title, 'detail': structured.get('description') or payload.get('detail') or '', 'location': ' / '.join((str(item) for item in address)), 'importance_score': payload.get('importance_score'), 'source_type': 'STEP_RESULT', 'source_id': str(self_event.get('event_id') or ''), 'agent_keys': agent_keys, 'payload': payload}

def conversation_views(facts: dict[str, Any], names: dict[str, str]) -> list[dict[str, Any]]:
    conversations: dict[str, dict[str, Any]] = {}
    seen_messages: dict[str, set[str]] = {}
    for frame in facts['frames']:
        for raw in frame.get('conversations') or []:
            conversation_id = str(raw.get('conversation_id') or '')
            if not conversation_id:
                continue
            participants = list(raw.get('participant_agent_keys') or [])
            item = conversations.setdefault(conversation_id, {'conversation_id': conversation_id, 'start_step': frame['step_no'], 'started_at': frame['virtual_time'], 'duration_minutes': raw.get('duration_minutes') or 0, 'duration_source': raw.get('duration_source') or 'RECORDED', 'location': ' / '.join((str(value) for value in raw.get('location') or [])), 'participants': participants, 'participant_names': [names.get(str(key), str(key)) for key in participants], 'message_count': 0, 'summary': raw.get('summary'), 'ended_reason': raw.get('ended_reason'), 'messages': []})
            item['duration_minutes'] = raw.get('duration_minutes') or item['duration_minutes']
            item['summary'] = raw.get('summary') or item['summary']
            item['ended_reason'] = raw.get('ended_reason') or item['ended_reason']
            known = seen_messages.setdefault(conversation_id, set())
            for message in raw.get('messages') or []:
                message_id = str(message.get('message_id') or f"{conversation_id}:{message.get('sequence')}")
                if message_id in known:
                    continue
                known.add(message_id)
                speaker = str(message.get('speaker_agent_key') or '')
                item['messages'].append({**dict(message), 'message_id': message_id, 'speaker_name': names.get(speaker, speaker), 'observed_at': frame['virtual_time']})
            item['message_count'] = len(item['messages'])
    return sorted(conversations.values(), key=lambda item: item['start_step'], reverse=True)

def memory_views(facts: dict[str, Any], names: dict[str, str]) -> list[dict[str, Any]]:
    memories: dict[tuple[str, str], dict[str, Any]] = {}
    states = {'CREATED': 'ACTIVE', 'EXPIRED': 'EXPIRED', 'EVICTED': 'EVICTED', 'SUPERSEDED': 'SUPERSEDED', 'INVALIDATED': 'INVALIDATED'}
    for frame in facts['frames']:
        for raw in frame.get('memory_deltas') or []:
            agent_key = str(raw.get('agent_key') or '')
            memory_id = str(raw.get('memory_id') or '')
            if not agent_key or not memory_id:
                continue
            item = memories.setdefault((agent_key, memory_id), {'memory_id': memory_id, 'agent_key': agent_key, 'agent_name': names.get(agent_key, agent_key), 'type': raw.get('memory_type') or 'EVENT', 'origin': 'STEP_RESULT', 'state': 'ACTIVE', 'description': raw.get('description'), 'poignancy': raw.get('poignancy'), 'created_step': frame['step_no'], 'created_at': raw.get('created_at') or frame['virtual_time'], 'last_accessed_step': None, 'removed_step': None, 'supersedes_memory_id': raw.get('supersedes_memory_id'), 'superseded_by_memory_id': None, 'invalidated_reason': None})
            kind = str(raw.get('kind') or 'CREATED')
            if kind == 'ACCESSED':
                item['last_accessed_step'] = frame['step_no']
            elif kind in states:
                item['state'] = states[kind]
                if kind != 'CREATED':
                    item['removed_step'] = frame['step_no']
            item['description'] = raw.get('description') or item['description']
            item['poignancy'] = raw.get('poignancy') if raw.get('poignancy') is not None else item['poignancy']
            if kind == 'SUPERSEDED':
                item['superseded_by_memory_id'] = raw.get('replacement_memory_id')
            if kind == 'INVALIDATED':
                item['invalidated_reason'] = raw.get('reason')
    return sorted(memories.values(), key=lambda item: (int(item['created_step']), item['agent_key'], item['memory_id']), reverse=True)

def agent_views(facts: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    names = definition_names(facts)
    stride = int(facts['definition'].get('simulation', {}).get('stride_minutes') or 1)
    definitions = {str(item.get('agent_key')): dict(item) for item in facts['definitions'] if item.get('agent_key') and item.get('enabled', True)}
    items: dict[str, dict[str, Any]] = {}
    details: dict[str, dict[str, Any]] = {}
    conversations = conversation_views(facts, names)
    memories = memory_views(facts, names)
    for key, definition in definitions.items():
        coord = list(definition.get('coord') or [0, 0])
        address_parts = ((definition.get('spatial') or {}).get('address') or {}).get('initial_location') or []
        base = {'agent_key': key, 'display_name': names.get(key, key), 'coord': coord, 'address': ' / '.join((str(value) for value in address_parts)), 'currently': definition.get('initial_currently') or '', 'action_count': 0, 'movement_steps': 0, 'conversation_count': 0, 'message_count': 0, 'memory_created_count': 0, 'activity_minutes': {'REST': 0, 'CHAT': 0, 'MOVING': 0, 'OTHER': 0}, 'updated_step': 0, 'definition': {**definition, **(definition.get('scratch') or {}), 'initial_currently': definition.get('currently') or ''}, 'portrait_url': packaged_asset_url(facts['summary']['run_id'], definition.get('portrait_asset')), 'plan_count': 0, 'event_count': 0, 'latest_activity_kind': 'OTHER', 'latest_action': definition.get('initial_currently') or '', 'latest_virtual_time': None, 'run_status': facts['summary']['status']}
        items[key] = base
        details[key] = {'run_id': facts['summary']['run_id'], 'run_status': facts['summary']['status'], 'agent': base, 'steps': [], 'latest_schedule': None, 'plan_revisions': [], 'actions': [], 'events': [], 'conversations': [item for item in conversations if key in item['participants']], 'memories': [item for item in memories if item['agent_key'] == key], 'state_changes': []}
    previous: dict[str, dict[str, Any]] = {}
    for frame in facts['frames']:
        for agent in frame.get('agents') or []:
            key = str(agent.get('agent_key') or '')
            if key not in items:
                continue
            item = items[key]
            action = dict(agent.get('action') or {})
            activity = str(agent.get('activity_kind') or 'OTHER')
            coord = list(agent.get('to_coord') or agent.get('coord') or item['coord'])
            address = ' / '.join((str(value) for value in agent.get('location') or agent.get('address') or []))
            step_view = {'step_no': frame['step_no'], 'virtual_time': frame['virtual_time'], 'coord': coord, 'address': address, 'action': action.get('description') or '', 'emoji': action.get('emoji'), 'activity_kind': activity, 'sample_kind': agent.get('path_source') or 'OBSERVED', 'currently': agent.get('currently'), 'decision_context': agent.get('decision_context') or {}}
            detail = details[key]
            detail['steps'].append(step_view)
            detail['actions'].append(step_view)
            item['coord'] = coord
            item['address'] = address
            item['currently'] = agent.get('currently') or action.get('description') or item['currently']
            item['action_count'] += 1
            if list(agent.get('from_coord') or coord) != coord:
                item['movement_steps'] += 1
            item['activity_minutes'].setdefault(activity, 0)
            item['activity_minutes'][activity] += stride
            item['updated_step'] = frame['step_no']
            item['latest_activity_kind'] = activity
            item['latest_action'] = action.get('description') or ''
            item['latest_virtual_time'] = frame['virtual_time']
            before = previous.get(key)
            if before and before.get('currently') != item['currently']:
                detail['state_changes'].append({'step_no': frame['step_no'], 'title': '当前状态', 'before': before.get('currently'), 'after': item['currently'], 'kind': 'STATE'})
            previous[key] = {'currently': item['currently'], 'coord': coord}
        for schedule in frame.get('schedule_revisions') or []:
            key = str(schedule.get('agent_key') or '')
            if key not in details:
                continue
            view = {'revision_no': len(details[key]['plan_revisions']) + 1, 'effective_step': frame['step_no'], 'effective_at': frame['virtual_time'], 'reason': schedule.get('reason') or '计划更新', 'items': list(schedule.get('schedule') or [])}
            details[key]['plan_revisions'].append(view)
            details[key]['latest_schedule'] = view
        for raw_event in frame.get('domain_events') or []:
            view = event_view(raw_event, step_no=frame['step_no'], virtual_time=frame['virtual_time'], names=names)
            for key in raw_event.get('agent_keys') or []:
                if str(key) in details:
                    details[str(key)]['events'].append(view)
    for key, item in items.items():
        own_conversations = details[key]['conversations']
        item['conversation_count'] = len(own_conversations)
        item['message_count'] = sum((value['message_count'] for value in own_conversations))
        item['memory_created_count'] = len(details[key]['memories'])
        item['plan_count'] = len(details[key]['plan_revisions'])
        item['event_count'] = len(details[key]['events'])
        details[key]['steps'].reverse()
        details[key]['actions'].reverse()
        details[key]['plan_revisions'].reverse()
        details[key]['events'].reverse()
        details[key]['content_counts'] = {'plans': item['plan_count'], 'actions': item['action_count'], 'events': item['event_count'], 'conversations': item['conversation_count'], 'memories': item['memory_created_count'], 'state_changes': len(details[key]['state_changes'])}
    return (sorted(items.values(), key=lambda item: item['agent_key']), details)

def checkpoint_documents(run_path, run_id: str) -> list[dict[str, Any]]:
    with open_package(run_path) as root:
        status = RunStatus.model_validate(read_json(root / 'status.json'))
        documents = []
        checkpoint_root = root / 'checkpoints'
        paths = {p.name: p for p in (root / 'recovery').glob('step-*')}
        paths.update({p.name: p for p in checkpoint_root.glob('step-*')})
        from generative_agents.ga_protocol.facts.recovery import validate_snapshot
        for path in sorted(paths.values(), key=lambda p: p.name, reverse=True):
            bundle_path = path / 'bundle.json'
            bundle = read_json(bundle_path) if bundle_path.is_file() else None
            if not isinstance(bundle, dict):
                continue
            step = int(bundle.get('step_no') or 0)
            try:
                validate_snapshot(root, path, run_id, step)
                valid, error = (True, None)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                valid, error = (False, str(exc))
            reason = error or (f'已提交到 Step {status.committed_step}；该检查点不能重复执行之后的已提交步骤' if step != status.committed_step else '仅暂停或失败的仿真可以恢复' if status.status.value not in {'PAUSED', 'FAILED'} else None)
            declared = list(bundle.get('files') or [])
            size = bundle_path.stat().st_size + sum((int(item.get('size') or 0) for item in declared if isinstance(item, dict)))
            documents.append({'run_id': run_id, 'step_no': int(bundle.get('step_no') or 0), 'status': 'VALID' if valid else 'INVALID', 'attempt_id': bundle.get('attempt_id'), 'bundle_sha256': hashlib.sha256(bundle_path.read_bytes()).hexdigest(), 'virtual_time': bundle.get('virtual_time'), 'size_bytes': size, 'file_count': len(declared) + 1, 'validated': valid, 'snapshot_kind': path.parent.name, 'resumable': valid and reason is None, 'committed_step': status.committed_step, 'active_attempt_id': status.active_attempt_id, 'recovery_reason': reason, 'validation': {'code': 'VALID' if valid else 'INVALID', 'reason': error}, 'files': declared})
    return documents

def replay_web_manifest(run_id: str, facts: dict[str, Any]) -> dict[str, Any]:
    world = json.loads(json.dumps(facts['definition']['world']))
    definition = world.get('definition') or {}
    definition.pop('semantic_index', None)
    definition['tiles'] = [{key: tile[key] for key in ('coord', 'tile', 'palette_key', 'visual_slice_id', 'visual_slice_part') if key in tile} if isinstance(tile, dict) else tile for tile in definition.get('tiles') or []]
    editor = definition.get('editor') or {}
    editor_v2 = definition.get('editor_v2') or {}
    palette_items = definition.get('palette') or editor.get('palette') or []
    palette = {str(item.get('key') or item.get('id')): {'color': str(item.get('color') or '#d9e2df'), 'label': str(item.get('label') or item.get('name') or item.get('key') or 'Tile')} for item in palette_items if isinstance(item, dict) and (item.get('key') or item.get('id'))}
    palette.setdefault('ground', {'color': '#d9e2df', 'label': 'Ground'})
    logical_by_hash = {str(item.get('asset_hash') or '').removeprefix('sha256:'): str(item.get('logical_path') or '') for item in world.get('assets') or [] if isinstance(item, dict)}
    for source in editor_v2.get('material_sources') or []:
        if not isinstance(source, dict):
            continue
        logical = logical_by_hash.get(str(source.get('asset_hash') or ''))
        if logical and logical.startswith('assets/'):
            source['package_url'] = packaged_asset_url(run_id, logical)
    objects = []
    for node in editor_v2.get('hierarchy_nodes') or []:
        if not isinstance(node, dict) or node.get('kind') != 'GAME_OBJECT':
            continue
        bounds = node.get('bounds') or {}
        extensions = node.get('extensions') or {}
        objects.append({'instance_key': str(node.get('id') or 'game-object'), 'x': float(bounds.get('x') or 0), 'y': float(bounds.get('y') or 0), 'appearance': dict(extensions.get('appearance') or {}), 'state': dict(node.get('initial_state') or {})})
    world['render_asset'] = {'status': 'READY', 'source': 'RUN_PACKAGE', 'renderer': 'SPATIAL_GRID', 'pixels_per_tile': max(8, min(int(definition.get('tile_size') or 16), 64)), 'palette': palette, 'objects': objects}
    agents = []
    for item in facts['definitions']:
        if not item.get('enabled', True):
            continue
        key = str(item.get('agent_key') or '')
        sprite_path = item.get('sprite_asset')
        sprite_url = packaged_asset_url(run_id, sprite_path)
        sprite = {'status': 'READY', 'source': 'RUN_PACKAGE', 'texture_url': sprite_url, 'atlas_url': f"/static/console/replay/assets/agent-sprite-{item.get('sprite_layout') or '4x4'}.json", 'layout': item.get('sprite_layout') or '4x4'} if sprite_url else {'status': 'MISSING', 'source': 'NONE', 'error_code': 'AGENT_SPRITE_ASSET_UNRESOLVED'}
        tags = {str(value).casefold() for value in item.get('tags') or []}
        agents.append({'agent_key': key, 'display_name': item.get('name') or item.get('display_name') or key, 'initial_coord': list(item.get('coord') or [0, 0]), 'sprite_asset': sprite, 'sprite_display_tiles': item.get('sprite_display_tiles'), 'role': 'PEDESTRIAN' if any(('pedestrian' in value or '行人' in value for value in tags)) else None})
    summary = facts['summary']
    return {'schema_version': 2, 'generator_version': 'ga-replay-package-v1', 'source_kind': 'RUN_FRAMES', 'run_id': run_id, 'experiment_id': summary['experiment_id'], 'definition_hash': '', 'world': world, 'source_step': summary['committed_step'], 'available_step': summary['committed_step'], 'stride_minutes': int(facts['definition']['simulation'].get('stride_minutes') or 1), 'execution_mode': 'SKILL_BRAIN', 'brain_skill': facts['definition']['engine'].get('brain_skill') or '', 'step_interval_ms': None, 'start_time': facts['definition']['simulation'].get('start_time'), 'requested_steps': summary['requested_steps'], 'timezone': facts['definition'].get('experiment', {}).get('timezone') or 'Asia/Shanghai', 'agents': agents, 'partial': summary['status'] != 'COMPLETED'}

def replay_web_step(frame: dict[str, Any], *, checkpoint: bool, attempt_boundary: bool) -> dict[str, Any]:
    return {'step_no': frame['step_no'], 'virtual_time': frame['virtual_time'], 'attempt_id': frame.get('attempt_id'), 'attempt_boundary': attempt_boundary, 'checkpoint': checkpoint, 'agents': [{'agent_key': item.get('agent_key'), 'from_coord': list(item.get('from_coord') or []), 'coord': list(item.get('to_coord') or item.get('coord') or []), 'path': list(item.get('path') or []), 'path_source': item.get('path_source') or 'OBSERVED', 'action': dict(item.get('action') or {}), 'address': list(item.get('location') or item.get('address') or []), 'currently': item.get('currently'), 'schedule_item_id': item.get('schedule_item_id'), 'decision_context': dict(item.get('decision_context') or {})} for item in frame.get('agents') or []], 'conversations': list(frame.get('conversations') or []), 'memory_deltas': list(frame.get('memory_deltas') or []), 'schedule_revisions': list(frame.get('schedule_revisions') or []), 'domain_events': list(frame.get('domain_events') or []), 'effects': list(frame.get('effects') or [])}
