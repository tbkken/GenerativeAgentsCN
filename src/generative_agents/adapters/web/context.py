"""Web orchestration: Studio locations, Runtime controls, Replay views."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from fastapi import HTTPException, Response
from generative_agents.ga_replay.api import read_run_overview
from generative_agents.ga_replay.api import read_run_status
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import checked_package_path
from generative_agents.ga_runtime.api import RunService as PortableRunService
from generative_agents.ga_runtime.api import FileRunSupervisor
from generative_agents.ga_studio.api import StudioPackageCatalogService
from generative_agents.ga_studio.api import AssetService
from generative_agents.ga_studio.api import SecretService
from generative_agents.ga_studio.api import StudioResourceError
from generative_agents.ga_studio.api import StudioResourceService
from generative_agents.ga_studio.api import ExperimentWorkspaceService
from functools import wraps
from generative_agents.ga_protocol.packages.locking import package_lock
from generative_agents.ga_protocol.packages.definition import _experiment_definition
from generative_agents.ga_replay.api import read_run_facts_unlocked

def _catalog_item(row) -> dict:
    return {'package_kind': row.package_kind, 'package_id': row.package_id, 'experiment_id': row.experiment_id, 'run_id': row.run_id, 'location': row.location, 'display_name': row.display_name, 'status': row.package_status, 'content_sha256': row.content_sha256, 'is_archive': row.is_archive, 'updated_at': row.updated_at.isoformat()}

class WebContext:

    def __init__(self, database, *, package_root: str | Path, max_concurrent_runs: int=2):
        catalog = StudioPackageCatalogService(database)
        resources = StudioResourceService(database)
        runtime = PortableRunService()
        package_root = Path(package_root).resolve()
        package_root.mkdir(parents=True, exist_ok=True)
        asset_service = AssetService(database, var_dir=package_root.parent)
        secret_service = SecretService(database, var_dir=package_root.parent)
        workspaces = ExperimentWorkspaceService(database, package_root=package_root, var_dir=package_root.parent)
        supervisor = FileRunSupervisor(max_concurrent_runs=max_concurrent_runs, on_complete=lambda path: catalog.upsert(path))
        presentation_path = package_root.parent / 'studio-presentation.json'
        self.asset_service = asset_service
        self.database = database
        self.catalog = catalog
        self.package_root = package_root
        self.presentation_path = presentation_path
        self.resources = resources
        self.runtime = runtime
        self.secret_service = secret_service
        self.supervisor = supervisor
        self.workspaces = workspaces

    def serialized_experiment(self, operation):

        @wraps(operation)
        def wrapped(experiment_id, *args, **kwargs):
            with package_lock(self.package_root / 'experiments' / f'{experiment_id}.identity'):
                row = self.catalog.get('experiment', experiment_id)
                if row is None:
                    return operation(experiment_id, *args, **kwargs)
                with package_lock(Path(row.location)):
                    return operation(experiment_id, *args, **kwargs)
        return wrapped

    def experiment_snapshot(self, experiment_id, *, summary_only=False):
        with package_lock(self.package_root / 'experiments' / f'{experiment_id}.identity'):
            row = self.catalog.get('experiment', experiment_id)
            if row is None:
                raise HTTPException(status_code=404, detail='experiment is not in the Studio package catalog')
            with package_lock(Path(row.location)):
                manifest, definition = _experiment_definition(Path(row.location), summary_only=summary_only)
            return (row, manifest, definition)

    def read_presentation(self) -> dict[str, Any]:
        if not self.presentation_path.is_file():
            return {'schema_version': 1, 'experiments': {}}
        document = read_json(self.presentation_path)
        if not isinstance(document, dict):
            raise PackageError('Studio presentation metadata is invalid')
        document.setdefault('schema_version', 1)
        document.setdefault('experiments', {})
        return document

    def write_presentation(self, document: dict[str, Any]) -> None:
        atomic_write_json(self.presentation_path, document)

    def experiment_presentation(self, experiment_id: str) -> dict[str, Any]:
        document = self.read_presentation()
        values = document.get('experiments') or {}
        item = values.get(experiment_id) if isinstance(values, dict) else None
        return dict(item) if isinstance(item, dict) else {}

    def update_experiment_presentation(self, experiment_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        if self.catalog.get('experiment', experiment_id) is None:
            raise HTTPException(status_code=404, detail='experiment is not in the Studio package catalog')
        document = self.read_presentation()
        values = document.setdefault('experiments', {})
        current = dict(values.get(experiment_id) or {})
        current.update(changes)
        values[experiment_id] = current
        self.write_presentation(document)
        return current

    def studio_call(self, operation):
        try:
            return operation()
        except (StudioResourceError, ValueError) as exc:
            status = 404 if 'does not exist' in str(exc) else 409 if 'changed' in str(exc) else 422
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    def run_summary(self, row) -> dict:
        try:
            summary, status, quality = read_run_overview(Path(row.location))
        except (PackageError, OSError) as exc:
            location = Path(row.location)
            if not location.exists():
                for journal in (self.package_root.parent / 'run-recycle-bin').glob('*/deletion.json'):
                    pending = read_json(journal)
                    if pending.get('run_id') == row.run_id and pending.get('source') == str(location.absolute()):
                        location = journal.parent / 'payload'
                        break
            with open_package(location) as root:
                manifest = read_json(root / 'run.json')
                status = RunStatus.model_validate(read_json(root / 'status.json'))
            if manifest.get('run_id') != row.run_id or status.run_id != row.run_id:
                raise HTTPException(status_code=409, detail='Run 包身份不一致') from exc
            summary = {'run_id': status.run_id, 'experiment_id': manifest['experiment']['experiment_id'], 'status': status.status.value, 'committed_step': status.committed_step, 'requested_steps': status.total_steps, 'recoverable_step': 0, 'package_error': 'Run 已移入回收暂存目录，删除索引待更新；请重试删除。' if location != Path(row.location) else 'Run 包不完整，无法回放或恢复；可重新执行删除以清理残留记录。'}
            quality = None
        attempts = summary.get('attempts') or []
        started_at = attempts[0].get('started_at') if attempts else None
        terminal = summary['status'] in {'COMPLETED', 'FAILED', 'CANCELLED'}
        finished_at = attempts[-1].get('finished_at') if terminal and attempts else None
        return {**summary, 'id': summary['run_id'], 'status': summary['status'], 'completed_steps': summary['committed_step'], 'requested_steps': summary['requested_steps'], 'active_attempt_id': status.active_attempt_id, 'started_at': started_at, 'finished_at': finished_at, 'recoverable': summary['status'] in {'PAUSED', 'FAILED'} and summary.get('recoverable_step', 0) > 0, 'recoverable_step': summary.get('recoverable_step', 0), 'quality': quality, 'updated_at': row.updated_at.isoformat()}

    def run_list_summary(self, row) -> dict:
        manifest, status = read_run_status(Path(row.location))
        if manifest.run_id != row.run_id or manifest.experiment.experiment_id != row.experiment_id:
            raise HTTPException(status_code=409, detail='Run 包身份与目录索引不一致')
        return {'id': manifest.run_id, 'run_id': manifest.run_id, 'experiment_id': manifest.experiment.experiment_id, 'status': status.status.value, 'completed_steps': status.committed_step, 'requested_steps': manifest.requested_steps, 'active_attempt_id': status.active_attempt_id}

    def read_run_facts(self, run_id: str) -> dict[str, Any]:
        try:
            return read_run_facts_unlocked(self.run_location(run_id), run_id)
        except (PackageError, OSError) as exc:
            if self.catalog.get('run', run_id) is None:
                raise HTTPException(status_code=404, detail='Run 已删除，请刷新列表') from exc
            raise HTTPException(status_code=409, detail='Run 包不完整或正在移入回收站，无法读取结果；请查看 Run 状态') from exc

    def trace_records_for(self, run_id):
        with open_package(self.run_location(run_id)) as root:
            records = []
            for path in sorted((root / 'traces').glob('*.jsonl')):
                for line in path.read_text(encoding='utf-8').splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    item['trace_id'] = f"{item.get('attempt_id', '')}:{item.get('event_seq', 0)}"
                    records.append(item)
            return records

    def run_log_bytes(self, run_id: str) -> tuple[bytes, bool]:
        with open_package(self.run_location(run_id)) as root:
            path = checked_package_path(root / 'logs' / 'runtime-process.log')
            content = path.read_bytes() if path.is_file() else b''
            status = RunStatus.model_validate(read_json(root / 'status.json'))
        terminal = status.status.value in {'PAUSED', 'CANCELLED', 'COMPLETED', 'FAILED'}
        return (content, terminal)

    def mutable_run_root(self, run_id: str) -> Path:
        location = self.run_location(run_id).resolve()
        if not location.is_dir():
            raise HTTPException(status_code=409, detail='sealed .garun files are already downloadable complete Run packages')
        return location

    def catalog_location(self, package_kind: str, package_id: str) -> Path:
        if package_kind not in {'experiment', 'run'}:
            raise HTTPException(status_code=404, detail='unknown package kind')
        row = self.catalog.get(package_kind, package_id)
        if row is None:
            raise HTTPException(status_code=404, detail='package is not in the Studio catalog')
        location = checked_package_path(Path(row.location))
        try:
            location.relative_to(self.package_root)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail='catalog package is outside the managed package root') from exc
        return location

    def run_location(self, run_id: str) -> Path:
        location = self.catalog_location('run', run_id)
        with open_package(location) as root:
            if read_json(root / 'run.json').get('run_id') != run_id:
                raise PackageError('catalog Run identity does not match its package')
        return location

    def experiment_location(self, experiment_id: str) -> Path:
        return self.catalog_location('experiment', experiment_id)

    def submit_directory(self, run_root: Path) -> dict:
        record = self.catalog.upsert(run_root)
        try:
            from generative_agents.ga_studio.api import HostModelCredentials
            environment = HostModelCredentials(self.database, self.package_root.parent).run_environment(run_root)
            self.supervisor.submit(run_root, environment=environment)
        except Exception as exc:
            self.catalog.upsert(run_root)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        status = RunStatus.model_validate(read_json(run_root / 'status.json'))
        return {'run_id': record.run_id, 'experiment_id': record.experiment_id, 'status': status.status.value, 'run_directory': str(run_root), 'active_attempt_id': status.active_attempt_id, 'completed_steps': status.committed_step, 'requested_steps': status.total_steps}

    def delete_run(self, run_id: str):
        from generative_agents.ga_studio.api import recycle_run
        from generative_agents.ga_studio.api import RunRecycleBusy
        with package_lock(self.package_root / 'runs' / f'{run_id}.resume-submit.identity'):
            if self.supervisor.process_status(run_id).get('owned_by_this_studio'):
                raise HTTPException(status_code=409, detail='Run 执行进程仍在退出或整理结果，请稍后重试删除')
            try:
                recycle_run(self.catalog, run_id, self.package_root.parent / 'run-recycle-bin')
            except RunRecycleBusy as exc:
                raise HTTPException(status_code=409, detail='Run 文件仍被占用，尚未移入回收站；请关闭相关文件后重试，目录未被逐项删除') from exc
            except (PackageError, OSError) as exc:
                raise HTTPException(status_code=409, detail=f'删除未完成，可重试：{exc}') from exc
            except Exception as exc:
                raise HTTPException(status_code=409, detail='删除索引更新未完成；文件保留在回收暂存目录，请重试删除') from exc
        return Response(status_code=204)
