"""Create bounded Run exports from a captured committed snapshot."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from filelock import FileLock
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.io import atomic_write_bytes
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import canonical_json_bytes
from generative_agents.ga_protocol.packages.io import checked_package_path
from generative_agents.ga_protocol.packages.artifacts import record_artifact_provenance
import zipfile
from generative_agents.ga_protocol.schemas.errors import ServiceError

class ArtifactExportError(ServiceError):

    def __init__(self, status_code, detail):
        super().__init__('ARTIFACT_EXPORT_ERROR', detail, status_code=status_code)

def write_zip(target: Path, root: Path, files: list[Path], *, overrides: dict[str, bytes] | None=None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'.{target.name}.{uuid4().hex}.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(files):
                checked_package_path(path)
                if path.is_file() and (not path.is_symlink()):
                    relative = path.relative_to(root).as_posix()
                    if relative not in (overrides or {}):
                        archive.write(path, relative)
            for relative, content in sorted((overrides or {}).items()):
                archive.writestr(relative, content)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)

def export_run_artifact(root, facts, *, job_type, parameters, quality=None, memories=None, conversations=None):
    run_id = facts['summary']['run_id']
    artifact_root = root / 'artifacts'
    artifact_root.mkdir(parents=True, exist_ok=True)
    step = int(facts['summary']['committed_step'])
    source_status = RunStatus.model_validate(facts['status'])
    if job_type == 'RESULT_BUNDLE':
        quality_content = canonical_json_bytes(quality)
        quality_digest = hashlib.sha256(quality_content).hexdigest()
        quality_path = artifact_root / f'quality-report-step-{step:06d}-{quality_digest[:16]}-{uuid4().hex[:8]}.json'
        atomic_write_bytes(quality_path, quality_content)
        quality_metadata = record_artifact_provenance(root, quality_path, source_status)
        target = artifact_root / f'result-bundle-step-{step:06d}-{uuid4().hex[:8]}.zip'
        overrides = {'status.json': canonical_json_bytes(facts['status'])}
        overrides.update({f"attempts/{item['attempt_id']}/attempt.json": canonical_json_bytes(item) for item in facts['summary'].get('attempts', [])})
        with FileLock(str(root / 'checkpoint.lock'), timeout=15), FileLock(str(root / 'recovery.lock'), timeout=15):
            files = []
            for path in root.rglob('*'):
                relative = path.relative_to(root)
                if not path.is_file() or path.is_symlink() or relative.parts[0] in {'artifacts', 'artifact-metadata', 'orphaned'} or any((part.startswith('.') for part in relative.parts)) or (relative.as_posix() in {'projection.json', 'integrity/sha256.json'}) or path.name.endswith('.lock'):
                    continue
                if relative.parts[0] in {'frames', 'checkpoints', 'recovery'}:
                    boundary = relative.parts[1].removeprefix('step-').removesuffix('.json.gz')
                    if not boundary.isdigit() or int(boundary) > step:
                        continue
                if relative.parts[0] == 'attempts':
                    if relative.parts[1] not in {item['attempt_id'] for item in facts['summary'].get('attempts', [])}:
                        continue
                    if len(relative.parts) > 2 and relative.parts[2] in {'storage', 'runtime-storage'}:
                        continue
                files.append(path)
            files.extend([quality_path, quality_metadata])
            write_zip(target, root, files, overrides=overrides)
    elif job_type == 'FILTERED_MEMORIES':
        items = memories
        agent_key = parameters.get('agent_key')
        memory_type = parameters.get('memory_type')
        query = str(parameters.get('q') or '').casefold()
        if agent_key:
            items = [item for item in items if item['agent_key'] == agent_key]
        if memory_type:
            items = [item for item in items if item['type'] == memory_type]
        if query:
            items = [item for item in items if query in str(item.get('description') or '').casefold()]
        target = artifact_root / f'memories-step-{step:06d}-{uuid4().hex[:8]}.ndjson'
        atomic_write_bytes(target, b''.join((json.dumps(item, ensure_ascii=False, sort_keys=True).encode('utf-8') + b'\n' for item in items)))
    elif job_type == 'FILTERED_CONVERSATIONS':
        items = conversations
        agent_key = parameters.get('agent_key')
        query = str(parameters.get('q') or '').casefold()
        if agent_key:
            items = [item for item in items if agent_key in item['participants']]
        if query:
            items = [item for item in items if query in json.dumps(item, ensure_ascii=False).casefold()]
        target = artifact_root / f'conversations-step-{step:06d}-{uuid4().hex[:8]}.json'
        atomic_write_bytes(target, (json.dumps({'run_id': run_id, 'items': items}, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    else:
        raise ArtifactExportError(status_code=422, detail='unknown file-backed artifact type')
    record_artifact_provenance(root, target, source_status)
    return {'job_id': str(uuid4()), 'run_id': run_id, 'status': 'SUCCEEDED', 'artifact_name': target.name}

def export_checkpoint_artifact(root, run_id, step_no):
    source_status = RunStatus.model_validate(read_json(root / 'status.json'))
    if step_no < 1 or step_no > source_status.committed_step:
        raise ArtifactExportError(status_code=422, detail='Checkpoint is outside the committed Run boundary')
    checkpoint = root / 'checkpoints' / f'step-{step_no:06d}'
    if not checkpoint.is_dir():
        checkpoint = root / 'recovery' / f'step-{step_no:06d}'
    if not checkpoint.is_dir():
        raise ArtifactExportError(status_code=404, detail='Checkpoint is not present in this Run package')
    target = root / 'artifacts' / f'checkpoint-step-{step_no:06d}-{uuid4().hex[:8]}.zip'
    lock = 'recovery.lock' if checkpoint.parent.name == 'recovery' else 'checkpoint.lock'
    with FileLock(str(root / lock), timeout=15):
        if not checkpoint.is_dir() or not (checkpoint / 'bundle.json').is_file():
            raise ArtifactExportError(status_code=404, detail='Checkpoint was removed before export; refresh the checkpoint list')
        write_zip(target, checkpoint, [path for path in checkpoint.rglob('*') if path.is_file()])
    record_artifact_provenance(root, target, source_status, source_step=step_no)
    return {'job_id': str(uuid4()), 'run_id': run_id, 'status': 'SUCCEEDED', 'artifact_name': target.name}
