"""Bounded, disposable validation and quality caches derived from Run files."""
from __future__ import annotations
import copy
from collections import OrderedDict
from threading import RLock
from pathlib import Path
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.schemas.manifests import RunManifest
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.validation import validate_run_integrity
from generative_agents.ga_protocol.facts.quality import project_run_quality
_validated_runs = OrderedDict()
_validation_lock = RLock()
_quality_reports = OrderedDict()
_quality_lock = RLock()

def _validated_directory(root: Path) -> RunManifest:
    """Reuse validation only while every immutable package file is unchanged.

    Mutable status, projections and frames are always read afresh. The cache is
    bounded and disposable; neither it nor the Studio catalog is a Run fact.
    """
    manifest = RunManifest.model_validate(read_json(root / 'run.json'))
    experiment_root = root / manifest.experiment.path
    try:
        experiment_root.resolve().relative_to(root)
    except ValueError as exc:
        raise PackageError('embedded experiment path escapes Run package') from exc
    if experiment_root.is_symlink():
        raise PackageError('embedded experiment cannot be a symbolic link')
    paths = [root / 'run.json', *sorted(experiment_root.rglob('*'))]
    signature = tuple(((str(path), stat.st_mode, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino) for path in paths for stat in [path.lstat()]))
    with _validation_lock:
        cached = _validated_runs.get(root)
        if cached is not None and cached[0] == signature:
            _validated_runs.move_to_end(root)
            return cached[1]
        verified = validate_run_integrity(root)
        _validated_runs[root] = (signature, verified)
        _validated_runs.move_to_end(root)
        while len(_validated_runs) > 8:
            _validated_runs.popitem(last=False)
        return verified

def read_run_quality(root: Path, manifest: RunManifest, status: RunStatus):
    """Rebuild disposable diagnostics from every committed Step, without writes."""
    quality_path = root / 'artifacts/quality-report.json'
    experiment_root = root / manifest.experiment.path
    experiment_manifest = read_json(experiment_root / 'manifest.json')
    engine_path = experiment_root / experiment_manifest['entrypoints']['engine']
    frame_paths = [root / 'frames' / f'step-{step:06d}.json.gz' for step in range(1, status.committed_step + 1)]
    observed_paths = [engine_path, *frame_paths]
    if quality_path.is_file():
        observed_paths.append(quality_path)
    signature = (manifest.run_id, status.committed_step, status.updated_at.isoformat(), tuple(((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino) for path in observed_paths for stat in [path.stat()])))
    with _quality_lock:
        cached = _quality_reports.get(root)
        if cached is not None and cached[0] == signature:
            _quality_reports.move_to_end(root)
            return copy.deepcopy(cached[1])
    previous = read_json(quality_path) if quality_path.is_file() else None
    engine = read_json(engine_path)
    quality = project_run_quality(root, run_id=manifest.run_id, committed_step=status.committed_step, brain_skill=engine['brain_skill'], evaluated_at=status.updated_at.isoformat(), previous_report=previous if isinstance(previous, dict) else None)
    with _quality_lock:
        _quality_reports[root] = (signature, copy.deepcopy(quality))
        _quality_reports.move_to_end(root)
        while len(_quality_reports) > 8:
            _quality_reports.popitem(last=False)
    return quality
