"""Bounded host workers with file-backed export operation status."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import BoundedSemaphore, RLock
from uuid import UUID, uuid4

from generative_agents.ga_protocol.packages.io import atomic_write_json, read_json, checked_package_path
from generative_agents.ga_protocol.schemas.errors import ServiceError


class ArtifactJobs:
    def __init__(self, *, workers=2, pending=8):
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='ga-export')
        self._slots = BoundedSemaphore(pending)
        self._lock = RLock()
        self._active = {}

    def active(self, run_id):
        with self._lock:
            return self._active.get(run_id, 0) > 0

    def _write(self, path, document):
        with self._lock:
            atomic_write_json(path, document)

    def submit(self, root, run_id, operation, *, parameters=None):
        if not self._slots.acquire(blocking=False):
            raise ServiceError('EXPORT_QUEUE_FULL', '导出队列已满，请等待已有任务完成', status_code=429)
        job_id = str(uuid4())
        path = checked_package_path(root / 'artifact-jobs' / f'{job_id}.json')
        document = {'job_id': job_id, 'run_id': run_id, 'status': 'QUEUED', 'process_id': os.getpid(),
                    'created_at': datetime.now(UTC).isoformat(), 'parameters': parameters or {}}
        registered = False
        try:
            self._write(path, document)
            with self._lock:
                self._active[run_id] = self._active.get(run_id, 0) + 1
                registered = True
            self._executor.submit(self._execute, path, document, operation)
        except BaseException:
            if registered:
                with self._lock:
                    self._active[run_id] = max(0, self._active.get(run_id, 1) - 1)
            self._slots.release()
            raise
        return document

    def _execute(self, path, document, operation):
        try:
            document = {**document, 'status': 'RUNNING', 'started_at': datetime.now(UTC).isoformat()}
            self._write(path, document)
            try:
                result = operation()
                document = {**document, **result, 'job_id': document['job_id'], 'status': 'SUCCEEDED'}
            except Exception as exc:
                document = {**document, 'status': 'FAILED', 'error': str(exc) or type(exc).__name__}
            self._write(path, {**document, 'finished_at': datetime.now(UTC).isoformat()})
        finally:
            with self._lock:
                self._active[document['run_id']] = max(0, self._active.get(document['run_id'], 1) - 1)
            self._slots.release()

    def status(self, root, run_id, job_id):
        try:
            job_id = str(UUID(job_id))
        except ValueError as exc:
            raise ServiceError('EXPORT_JOB_NOT_FOUND', '导出任务不存在', status_code=404) from exc
        path = checked_package_path(root / 'artifact-jobs' / f'{job_id}.json')
        with self._lock:
            if not path.is_file():
                raise ServiceError('EXPORT_JOB_NOT_FOUND', '导出任务不存在', status_code=404)
            document = read_json(path)
        if document.get('run_id') != run_id:
            raise ServiceError('EXPORT_JOB_IDENTITY_MISMATCH', '导出任务不属于当前 Run', status_code=409)
        if document.get('status') in {'QUEUED', 'RUNNING'} and document.get('process_id') != os.getpid():
            # A restarted host cannot resume a Python callback; report that
            # explicitly instead of leaving the UI polling a nonexistent job.
            return {**document, 'status': 'INTERRUPTED', 'error': '导出工作进程已更换，请重新提交导出任务'}
        return document
