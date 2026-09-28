"""File-backed progress for explicit, potentially long Studio operations."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4
from filelock import FileLock, Timeout
from generative_agents.ga_protocol.packages.io import atomic_write_json, checked_package_path, read_json
from generative_agents.ga_protocol.packages.locking import package_lock


class OperationJobs:
    def __init__(self, directory):
        self.root = checked_package_path(Path(directory))
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ga-studio-operation')
        self._pending = set()
        self._closed = False
        self._owner_id = str(uuid4())
        self._owner = self._owner_lock(self._owner_id)
        # A UUID lease, held for this manager's entire lifetime, avoids PID reuse
        # and is released by the OS even if a Web worker crashes.
        self._owner.acquire(timeout=0)
        try:
            for path in self.root.glob('*.json'):
                self.get(path.stem)
        except BaseException:
            self._owner.release()
            self._pool.shutdown(wait=True)
            raise

    def _owner_lock(self, owner_id):
        directory = checked_package_path(self.root / 'owners')
        directory.mkdir(parents=True, exist_ok=True)
        path = checked_package_path(directory / f'{UUID(str(owner_id))}.lock')
        # Startup, HTTP handlers and lifespan shutdown may use different threads.
        return FileLock(path, thread_local=False)

    def _owner_alive(self, owner_id):
        if not owner_id:
            return False
        probe = self._owner_lock(owner_id)
        try:
            probe.acquire(timeout=0)
        except Timeout:
            return True
        else:
            probe.release()
            return False

    def _path(self, operation_id):
        return checked_package_path(self.root / f'{UUID(str(operation_id))}.json')

    def _write(self, item):
        item['updated_at'] = datetime.now(UTC).isoformat()
        atomic_write_json(self._path(item['operation_id']), item)
        return item

    def get(self, operation_id):
        path = self._path(operation_id)
        item = read_json(path)
        if item.get('status') in {'QUEUED', 'RUNNING'} and not self._owner_alive(item.get('owner_id')):
            # A surviving Web worker can observe another worker's crash without
            # requiring its own restart. Recheck under the transition lock.
            with package_lock(path):
                item = read_json(path)
                if item.get('status') in {'QUEUED', 'RUNNING'} and not self._owner_alive(item.get('owner_id')):
                    item = self._write({**item, 'status': 'FAILED', 'error': '服务重启中断了操作，请核对结果后重试。'})
        return item

    def submit(self, kind, operation, *, scope=None):
        with self._lock:
            if self._closed:
                raise ValueError('服务正在关闭，无法接收新的后台操作。')
            if len(self._pending) >= 16:
                raise ValueError('后台操作队列已满，请等待当前操作完成。')
            identity = str(uuid4())
            item = self._write({'operation_id': identity, 'kind': kind, 'status': 'QUEUED',
                                'owner_id': self._owner_id,
                                'scope': scope or {}, 'created_at': datetime.now(UTC).isoformat(),
                                'status_url': f'/api/studio/operations/{identity}'})
            self._pending.add(identity)
            self._pool.submit(self._execute, identity, operation)
            return item

    def _execute(self, identity, operation):
        try:
            with package_lock(self._path(identity)):
                item = self.get(identity)
                if item['status'] == 'CANCELLED':
                    return
                self._write({**item, 'status': 'RUNNING'})
            result = operation()
            with package_lock(self._path(identity)):
                self._write({**item, 'status': 'SUCCEEDED', 'result': result})
        except Exception as exc:
            with package_lock(self._path(identity)):
                self._write({**self.get(identity), 'status': 'FAILED', 'error': str(exc)})
        finally:
            with self._lock:
                self._pending.discard(identity)

    def cancel(self, identity):
        with package_lock(self._path(identity)):
            item = self.get(identity)
            if item['status'] == 'RUNNING':
                raise ValueError('操作已经开始，需等待当前原子操作完成。')
            if item['status'] == 'QUEUED':
                item = self._write({**item, 'status': 'CANCELLED'})
            return item

    def close(self):
        with self._lock:
            self._closed = True
        try:
            self._pool.shutdown(wait=True)
        finally:
            self._owner.release()
