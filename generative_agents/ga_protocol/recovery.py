"""Read-only validation and selection of exact-boundary recovery snapshots."""
from pathlib import Path
import gzip
import json
from .io import PackageError, read_json, sha256_file


def validate_snapshot(root: Path, path: Path, run_id: str, step: int) -> dict:
    root = root.resolve()
    if path.is_symlink() or path.resolve().parent not in {root / 'checkpoints', root / 'recovery'}:
        raise PackageError('unsafe recovery snapshot path')
    bundle = read_json(path / 'bundle.json')
    if bundle.get('bundle_schema_version') != 1 or bundle.get('run_id') != run_id or bundle.get('step_no') != step:
        raise PackageError('recovery snapshot identity mismatch')
    declared = set()
    for item in bundle.get('files') or []:
        relative = Path(item['path'])
        target = path / relative
        if relative.is_absolute() or '..' in relative.parts or relative.as_posix() in declared:
            raise PackageError('unsafe recovery snapshot member')
        if target.is_symlink() or not target.resolve().is_relative_to(path.resolve()) or not target.is_file():
            raise PackageError('missing recovery snapshot member')
        declared.add(relative.as_posix())
        if target.stat().st_size != item['size'] or sha256_file(target) != item['sha256']:
            raise PackageError(f'recovery snapshot hash mismatch: {relative}')
    if not {'state.json', 'conversation.json', 'frame.json.gz'} <= declared:
        raise PackageError('recovery snapshot is incomplete')
    members = list(path.rglob('*'))
    if any(member.is_symlink() for member in members):
        raise PackageError('recovery snapshot contains symbolic links')
    if {member.relative_to(path).as_posix() for member in members if member.is_file() and member.name != 'bundle.json'} != declared:
        raise PackageError('recovery snapshot contains undeclared members')
    frame = root / 'frames' / f'step-{step:06d}.json.gz'
    if sha256_file(frame) != sha256_file(path / 'frame.json.gz') or sha256_file(frame) != bundle.get('frame_sha256'):
        raise PackageError('recovery snapshot disagrees with committed frame')
    result = json.loads(gzip.decompress(frame.read_bytes()))['result']
    if any(result.get(key) != bundle.get(key) for key in ('run_id', 'step_no', 'attempt_id', 'virtual_time')):
        raise PackageError('recovery snapshot frame identity mismatch')
    state = read_json(path / 'state.json')
    if state.get('virtual_time', bundle['virtual_time']) != bundle['virtual_time']:
        raise PackageError('recovery snapshot state time mismatch')
    return bundle


def boundary_snapshot(root: Path, run_id: str, step: int) -> Path:
    for directory in ('recovery', 'checkpoints'):
        path = root / directory / f'step-{step:06d}'
        if path.is_dir():
            try:
                validate_snapshot(root, path, run_id, step)
                return path
            except (OSError, ValueError, KeyError, TypeError):
                continue
    raise PackageError(f'已提交到 Step {step}，但缺少该边界的完整恢复快照（complete checkpoint）；不能从更早检查点重复执行已提交步骤')
