"""Detach a Run as a whole before removing its disposable catalog entry."""
from pathlib import Path
import os
import time
from uuid import uuid4
from generative_agents.ga_protocol import PackageError, atomic_write_json, read_json, open_package


class RunRecycleBusy(PermissionError):
    """The whole-directory move was refused; live contents are untouched."""


def recycle_run(catalog, run_id: str, trash_root: Path) -> None:
    row = catalog.get('run', run_id)
    if row is None:
        return  # An acknowledged DELETE can safely be retried.
    source = Path(row.location).absolute()
    trash_root = trash_root.resolve()
    trash_root.mkdir(parents=True, exist_ok=True)
    # Finish a catalog transaction interrupted after the directory was moved.
    pending = None
    for journal in trash_root.glob('*/deletion.json'):
        record = read_json(journal)
        if record.get('run_id') == run_id and record.get('source') == str(source):
            payload = journal.parent / 'payload'
            if payload.exists() and not source.exists():
                pending = journal
                break
    if pending is not None:
        catalog.delete('run', run_id)
        record = read_json(pending)
        atomic_write_json(pending, {**record, 'state': 'RECYCLED'})
        return
    if source.is_symlink() or source.resolve() != source or source == Path(source.anchor):
        raise PackageError('unsafe Run deletion path')
    if not source.exists():
        raise PackageError('Run 目录已缺失，无法确认删除状态；请检查目录索引')
    with open_package(source) as root:
        manifest = read_json(root / 'run.json')
        status = read_json(root / 'status.json')
    if manifest.get('run_id') != run_id or status.get('run_id') != run_id:
        raise PackageError('Run 包身份与待删除记录不一致')
    if status.get('status') in {'QUEUED', 'RUNNING', 'FINALIZING'}:
        raise PackageError('活动仿真不能删除，请先取消并等待执行进程结束')
    destination = trash_root / uuid4().hex
    destination.mkdir()
    payload = destination / 'payload'
    # Verify both absolute targets before any move; no recursive deletion is
    # performed, so an occupied file cannot leave a half-deleted live Run.
    if not payload.resolve().is_relative_to(trash_root) or trash_root.is_relative_to(source):
        raise PackageError('unsafe Run recycle destination')
    journal = destination / 'deletion.json'
    record = {'run_id': run_id, 'source': str(source), 'state': 'PREPARED', 'is_archive': source.is_file()}
    atomic_write_json(journal, record)
    for delay in (0.02, 0.05, 0.1, 0.2, 0.4, None):
        try:
            os.rename(source, payload)
            break
        except PermissionError as exc:
            if delay is None:
                raise RunRecycleBusy('Run directory is occupied') from exc
            time.sleep(delay)
    # Keep the journal and payload if the database is temporarily unavailable;
    # the next DELETE completes the index update without needing a valid bundle.
    catalog.delete('run', run_id)
    atomic_write_json(journal, {**record, 'state': 'RECYCLED'})
