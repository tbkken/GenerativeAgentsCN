"""Bounded, disposable read indexes for the console's independently loaded views.

Only committed frames are indexed. An index is never a recovery source and may
be evicted at any time; source fingerprints are checked before it is reused.
"""
from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from weakref import WeakValueDictionary

from generative_agents.ga_protocol.packages.io import checked_package_path, read_json, iter_package_files
from generative_agents.ga_protocol.packages.reading import file_fingerprint, open_readonly_package, read_package_json, list_package_members
from generative_agents.ga_protocol.packages.definition import _experiment_definition
from generative_agents.ga_protocol.packages.artifacts import read_artifact_provenance, provenance_path
from generative_agents.ga_protocol.schemas.manifests import validate_package_path
from generative_agents.ga_replay.reader import ReplayReader, read_run_status

_lock = RLock()
_definitions = OrderedDict()
_indexes = OrderedDict()
_traces = OrderedDict()
_artifacts = OrderedDict()
_states = OrderedDict()
_trace_pages = OrderedDict()
_trace_locations = OrderedDict()
_read_locks = WeakValueDictionary()


def _key_lock(kind, key):
    with _lock:
        lock = _read_locks.get((kind, key))
        if lock is None:
            lock = RLock()
            _read_locks[(kind, key)] = lock
        return lock


def _remember(cache, key, value, maximum=4):
    with _lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > maximum:
            cache.popitem(last=False)
    return value


def _signature(path):
    return file_fingerprint(checked_package_path(path))


def metadata(replay, *, attempts=True):
    root, manifest, status = replay.root, replay.manifest, replay.status
    experiment = read_json(root / manifest.experiment.path / 'manifest.json')
    documents = []
    if attempts:
        for path in sorted((root / 'attempts').glob('*/attempt.json')):
            document = read_json(checked_package_path(path))
            if isinstance(document, dict):
                documents.append(document)
        documents.sort(key=lambda item: item.get('ordinal') or 0)
    return {'run_id': manifest.run_id, 'experiment_id': manifest.experiment.experiment_id,
            'experiment': experiment.get('experiment'), 'status': status.status.value,
            'committed_step': status.committed_step, 'requested_steps': manifest.requested_steps,
            'attempts': documents, 'lineage': manifest.lineage.model_dump(mode='json')}


def read_metadata(package, *, attempts=True):
    package = Path(package)
    manifest, status = read_run_status(package)
    experiment = read_package_json(package, f'{manifest.experiment.path}/manifest.json')
    documents = []
    if attempts:
        paths = ([path.relative_to(package).as_posix() for path in sorted((package / 'attempts').glob('*/attempt.json'))]
                 if package.is_dir() else [name for name in list_package_members(package, prefix='attempts')
                                          if name.count('/') == 2 and name.endswith('/attempt.json')])
        documents = [read_package_json(package, name) for name in paths]
        documents.sort(key=lambda item: item.get('ordinal') or 0)
    summary = {'run_id': manifest.run_id, 'experiment_id': manifest.experiment.experiment_id,
               'experiment': experiment.get('experiment'), 'status': status.status.value,
               'committed_step': status.committed_step, 'requested_steps': manifest.requested_steps,
               'attempts': documents, 'lineage': manifest.lineage.model_dump(mode='json')}
    return summary, status


def definition_for(replay):
    root = replay.root / replay.manifest.experiment.path
    manifest = read_json(root / 'manifest.json')
    signature = tuple(_signature(root / name) for name in (
        'manifest.json', manifest['entrypoints']['resources'], manifest['entrypoints']['assembly']))
    key = str(replay.package.absolute())
    with _lock:
        cached = _definitions.get(key)
        if cached and cached[0] == signature:
            _definitions.move_to_end(key)
            return cached[1]
    definition = _experiment_definition(root)[1]
    with _lock:
        _remember(_definitions, key, (signature, definition))
    return definition


def read_slice(package, *, start=1, end=None, include_frames=True, include_definition=True,
               attempts=False):
    """Read precisely one frame window; never visit traces or exported artifacts."""
    with ReplayReader(package) as replay:
        definition = definition_for(replay) if include_definition else {}
        frames = list(replay.iter_steps(start=start, end=end)) if include_frames else []
        checkpoints = {frame['step_no'] for frame in frames
                       if (replay.root / 'checkpoints' / f"step-{frame['step_no']:06d}" / 'bundle.json').is_file()}
        return {'summary': metadata(replay, attempts=attempts), 'definition': definition,
                'definitions': definition.get('agents') or [], 'frames': frames,
                'status': replay.status.model_dump(mode='json'), 'checkpoint_steps': checkpoints}


def result_index(package):
    """Reuse validated frames and derived views; read only new/changed frame bytes.

    Existing fingerprints include the commit records, so deleting a projection,
    corrupting a frame, replacing an archive or changing a commit cannot preserve
    a stale result. The dictionaries below are private immutable read snapshots.
    """
    from generative_agents.ga_replay.projections.index import advance_views
    with ReplayReader(package) as replay:
        key = str(replay.package.absolute())
        definition = definition_for(replay)
        summary = metadata(replay)
        signatures = [(_signature(replay.root / 'frames' / f'step-{step:06d}.json.gz'),
                       _signature(replay.root / 'commits' / f'step-{step:06d}.json'))
                      for step in range(1, replay.status.committed_step + 1)]
        with _key_lock('results', key):
            cached = _indexes.get(key)
            reusable = cached and cached['definition'] is definition
            if reusable and cached['signatures'] == signatures:
                _indexes.move_to_end(key)
                return {**cached, 'summary': summary, 'status': replay.status.model_dump(mode='json')}
            frames = []
            for position, signature in enumerate(signatures):
                if reusable and position < len(cached['signatures']) and cached['signatures'][position] == signature:
                    frames.append(cached['frames'][position])
                else:
                    frame = replay.read_step(position + 1)
                    # Execution traces and payloads do not belong in list indexes.
                    frames.append({name: value for name, value in frame.items()
                                   if name not in {'effects', 'committed_model_usage'}})
            facts = {'definition': definition, 'definitions': definition.get('agents') or [],
                     'frames': frames, 'summary': summary, 'signatures': signatures,
                     'status': replay.status.model_dump(mode='json')}
            append = reusable and signatures[:len(cached['signatures'])] == cached['signatures']
            facts.update(advance_views(facts, cached if append else None, start=len(cached['frames']) if append else 0))
            return _remember(_indexes, key, facts)


def read_state(package, step_no):
    """Seek from the closest reusable state, reducing only the missing prefix."""
    with ReplayReader(package) as replay:
        if step_no < 0 or step_no > replay.status.committed_step:
            raise IndexError('Step is outside the committed Replay boundary')
        key = str(Path(package).absolute())
        signatures = [(_signature(replay.root / 'frames' / f'step-{step:06d}.json.gz'),
                       _signature(replay.root / 'commits' / f'step-{step:06d}.json'))
                      for step in range(1, step_no+1)]
        with _key_lock('states', key):
            snapshots = _states.get(key, OrderedDict())
            usable = [(step, value) for step, value in snapshots.items()
                      if step <= step_no and value[0] == signatures[:step]]
            if usable:
                start, (_signatures, old) = max(usable, key=lambda item: item[0])
                state = copy.deepcopy(old)
            else:
                start = 0
                state = {'step_no': 0, 'virtual_time': None, 'agents': {}, 'object_states': {}, 'conversations': {}}
            for frame in replay.iter_steps(start=start+1, end=step_no) if step_no > start else []:
                state['step_no'] = frame['step_no']
                state['virtual_time'] = frame.get('virtual_time')
                state['attempt_id'] = frame.get('attempt_id')
                for agent in frame.get('agents') or []:
                    state['agents'][agent['agent_key']] = {'coord': agent.get('to_coord'), 'location': agent.get('location'),
                        'currently': agent.get('currently'), 'action': agent.get('action'), 'path': agent.get('path')}
                for event in frame.get('domain_events') or []:
                    if event.get('event_type') == 'GAME_OBJECT_STATE_CHANGED':
                        payload = (event.get('payload') or {}).get('structured_payload') or {}
                        object_key = payload.get('object_key') or payload.get('object_id')
                        if object_key:
                            state['object_states'][str(object_key)] = payload.get('after') or payload.get('state') or payload
                for item in frame.get('conversations') or []:
                    state['conversations'][str(item['conversation_id'])] = item
            _remember(snapshots, step_no, (signatures, copy.deepcopy(state)), maximum=8)
            _remember(_states, key, snapshots)
            return state


def trace_records(package):
    """Incrementally parse append-only audit files, without touching frame data."""
    with open_readonly_package(Path(package)) as root:
        paths = sorted(checked_package_path(root / 'traces').glob('*.jsonl'))
        key = str(Path(package).absolute())
        with _key_lock('traces', key):
            previous = _traces.get(key, {})
            files = {}
            for path in paths:
                path = checked_package_path(path)
                signature = _signature(path)
                cached = previous.get(path.name)
                if cached and cached['signature'] == signature:
                    files[path.name] = cached
                    continue
                stat = path.stat()
                append = cached and cached['inode'] == stat.st_ino and stat.st_size > cached['size']
                offset = cached['offset'] if append else 0
                records = list(cached['records']) if append else []
                with path.open('rb') as handle:
                    handle.seek(offset)
                    while line := handle.readline():
                        # A writer may be in the middle of one JSONL record.
                        if not line.endswith(b'\n'):
                            try:
                                item = json.loads(line)
                            except (ValueError, UnicodeError):
                                break
                        else:
                            try:
                                item = json.loads(line)
                            except (ValueError, UnicodeError):
                                offset = handle.tell()
                                continue
                        if isinstance(item, dict):
                            item = {key: value for key, value in item.items() if key != 'payload'}
                            item['trace_id'] = f"{item.get('attempt_id', '')}:{item.get('event_seq', 0)}"
                            records.append(item)
                        offset = handle.tell()
                files[path.name] = {'signature': signature, 'inode': stat.st_ino,
                                    'size': stat.st_size, 'offset': offset, 'records': records}
            _remember(_traces, key, files)
            return [record for item in files.values() for record in item['records']]


def trace_page(package, *, attempt_id=None, event_type=None, purpose='', cursor=0, limit=200):
    """Read just the selected audit page and one lookahead record.

    Generated cursors have byte checkpoints in a disposable index. A missing
    checkpoint can be reconstructed by scanning to that cursor, without loading
    the remainder of the Run or retaining payloads in list responses.
    """
    with open_readonly_package(Path(package)) as root:
        paths = sorted(checked_package_path(root / 'traces').glob('*.jsonl'))
        source = str(Path(package).absolute())
        key = (source, attempt_id, event_type, purpose.casefold())
        signatures = {path.name: _signature(path) for path in paths}
        with _key_lock('trace-pages', key):
            cached = _trace_pages.get(key)
            compatible = cached is not None and all(
                name in signatures and (signature == signatures[name] or
                    (signature[:3] == signatures[name][:3] and signatures[name][3] > signature[3]))
                for name, signature in cached['signatures'].items())
            offsets = dict(cached['offsets']) if compatible else {}
            saved = offsets.get(cursor)
            start_name, start_byte, matched = (saved[0], saved[1], cursor) if saved else (None, 0, 0)
            selected, eof = [], True
            locations = dict(_trace_locations.get(source, {}))
            for path in paths:
                if start_name is not None and path.name < start_name:
                    continue
                path = checked_package_path(path)
                with path.open('rb') as handle:
                    if path.name == start_name:
                        handle.seek(start_byte)
                    while True:
                        position = handle.tell()
                        line = handle.readline()
                        if not line:
                            break
                        try:
                            item = json.loads(line)
                        except (ValueError, UnicodeError):
                            continue
                        if not isinstance(item, dict):
                            continue
                        trace_id = f"{item.get('attempt_id', '')}:{item.get('event_seq', 0)}"
                        locations[trace_id] = (path.name, position, signatures[path.name])
                        if attempt_id and item.get('attempt_id') != attempt_id:
                            continue
                        if event_type == 'PHYSICAL' and not str(item.get('event_type') or '').startswith('PHYSICAL'):
                            continue
                        if event_type and event_type != 'PHYSICAL' and item.get('event_type') != event_type:
                            continue
                        if purpose and purpose.casefold() not in str(item.get('purpose') or '').casefold():
                            continue
                        matched += 1
                        if matched <= cursor:
                            continue
                        if len(selected) == limit:
                            offsets[cursor+len(selected)] = (path.name, position)
                            eof = False
                            break
                        selected.append({**{name: value for name, value in item.items() if name != 'payload'}, 'trace_id': trace_id})
                        offsets[cursor+len(selected)] = (path.name, handle.tell())
                    if not eof:
                        break
            # Bound navigation metadata independently from payload byte size.
            if len(locations) > 10000:
                locations = dict(list(locations.items())[-10000:])
            _remember(_trace_locations, source, locations)
            _remember(_trace_pages, key, {'signatures': signatures, 'offsets': dict(list(offsets.items())[-500:])}, maximum=16)
            return {'items': selected, 'next_cursor': cursor+len(selected), 'eof': eof}


def trace_record(package, trace_id):
    source = str(Path(package).absolute())
    with open_readonly_package(Path(package)) as root:
        saved = _trace_locations.get(source, {}).get(trace_id)
        if saved:
            filename, offset, old = saved
            path = checked_package_path(root / 'traces' / filename)
            current = _signature(path) if path.is_file() else None
            if current and (current == old or (current[:3] == old[:3] and current[3] > old[3])):
                try:
                    with path.open('rb') as handle:
                        handle.seek(offset)
                        item = json.loads(handle.readline())
                except (ValueError, UnicodeError):
                    item = None
                if isinstance(item, dict) and f"{item.get('attempt_id', '')}:{item.get('event_seq', 0)}" == trace_id:
                    return {**item, 'trace_id': trace_id}
        for path in sorted(checked_package_path(root / 'traces').glob('*.jsonl')):
            with checked_package_path(path).open('rb') as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(item, dict) and f"{item.get('attempt_id', '')}:{item.get('event_seq', 0)}" == trace_id:
                        return {**item, 'trace_id': trace_id}
    return None


def artifact_page(package, run_id, *, offset=0, limit=50):
    """Hash only the requested exports; reuse unchanged content identities."""
    with open_readonly_package(Path(package)) as root:
        artifact_root = checked_package_path(root / 'artifacts')
        key = str(Path(package).absolute())
        with _key_lock('artifacts', key):
            previous = _artifacts.get(key, {})
            paths = [(relative, path) for relative, path in iter_package_files(artifact_root) if not path.name.startswith('.')] if artifact_root.is_dir() else []
            present = {relative for relative, _path in paths}
            files = {relative: value for relative, value in previous.items() if relative in present}
            selected = paths[offset:offset+limit] if limit is not None else paths[offset:]
            items = []
            for relative, path in selected:
                signature = _signature(path)
                cached = previous.get(relative)
                if cached and cached[0] == signature:
                    item = cached[1]
                    # Provenance can appear after an export is atomically
                    # published; it has a separate lifetime from its bytes.
                    item = {**item, **read_artifact_provenance(root, run_id=run_id, logical_name=relative,
                                                             digest=item['sha256'], size_bytes=item['size_bytes'])}
                    files[relative] = (signature, item)
                    items.append(copy.deepcopy(item))
                    continue
                hasher = hashlib.sha256()
                with path.open('rb') as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
                size = path.stat().st_size
                item = {'artifact_id': digest[:32], 'type': path.suffix.removeprefix('.').upper() or 'FILE',
                        'logical_name': relative, 'media_type': mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                        'size_bytes': size, 'sha256': digest,
                        **read_artifact_provenance(root, run_id=run_id, logical_name=relative, digest=digest, size_bytes=size),
                        'state': 'READY'}
                files[relative] = (signature, item)
                items.append(copy.deepcopy(item))
            _remember(_artifacts, key, files)
            return {'items': items, 'total': len(paths),
                    'next_offset': offset+len(items) if offset+len(items) < len(paths) else None}


def artifact_records(package, run_id):
    return artifact_page(package, run_id, limit=None)['items']


def artifact_for_id(package, run_id, artifact_id, *, logical_name=None, verify=True):
    """Resolve a download using its explicit name or small provenance records."""
    with open_readonly_package(Path(package)) as root:
        if logical_name is None:
            with _lock:
                cached = _artifacts.get(str(Path(package).absolute()), {})
                logical_name = next((item[1]['logical_name'] for item in cached.values()
                                     if item[1]['artifact_id'] == artifact_id), None)
            if logical_name is None:
                for path in checked_package_path(root / 'artifact-metadata').glob('*.json'):
                    document = read_json(checked_package_path(path))
                    if document.get('run_id') == run_id and str(document.get('sha256') or '')[:32] == artifact_id:
                        logical_name = document.get('logical_name')
                        break
        if logical_name is not None:
            relative = validate_package_path(logical_name)
            asset_root = checked_package_path(root / 'artifacts')
            path = checked_package_path(asset_root / relative)
            path.relative_to(asset_root)
            if not path.is_file():
                return None
            digest = None
            if verify:
                with path.open('rb') as handle:
                    digest = hashlib.file_digest(handle, 'sha256').hexdigest()
                if digest[:32] != artifact_id:
                    return None
            return {'logical_name': relative, 'sha256': digest,
                    'media_type': mimetypes.guess_type(path.name)[0] or 'application/octet-stream'}
    # Old unrecorded exports can still be resolved from their actual bytes.
    return next((item for item in artifact_records(package, run_id) if item['artifact_id'] == artifact_id), None)
