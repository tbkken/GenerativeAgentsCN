"""Bounded, disposable read views; never shared with package writers."""
from __future__ import annotations

import atexit
import json
import os
import stat
import tempfile
import zipfile
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from generative_agents.ga_protocol.packages.io import (
    PackageError, checked_package_path, extract_archive, open_shared_reader, read_json,
)
from generative_agents.ga_protocol.schemas.manifests import validate_package_path


def file_fingerprint(path: Path) -> tuple:
    """Observe identity and modification, including NTFS ChangeTime.

    Windows st_ctime is creation time. ChangeTime also detects an in-place edit
    whose length and LastWriteTime have been restored by an external editor.
    Shared handles do not block the Runtime's atomic file replacement.
    """
    path = checked_package_path(path)
    info = path.stat()
    change_time = info.st_ctime_ns
    if os.name == 'nt' and path.is_file():
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class BasicInfo(ctypes.Structure):
            _fields_ = [('creation', ctypes.c_longlong), ('access', ctypes.c_longlong),
                        ('write', ctypes.c_longlong), ('change', ctypes.c_longlong),
                        ('attributes', wintypes.DWORD)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        query = kernel.GetFileInformationByHandleEx
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        query.restype = wintypes.BOOL
        with open_shared_reader(path) as handle:
            info = os.fstat(handle.fileno())
            basic = BasicInfo()
            if not query(msvcrt.get_osfhandle(handle.fileno()), 0, ctypes.byref(basic), ctypes.sizeof(basic)):
                raise ctypes.WinError(ctypes.get_last_error())
            change_time = basic.change
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, change_time)


def tree_fingerprint(root: Path) -> tuple:
    root = checked_package_path(root)
    return tuple((path.relative_to(root).as_posix(), file_fingerprint(path))
                 for path in [root, *sorted(root.rglob('*'))])


def safe_archive_members(archive: zipfile.ZipFile) -> dict:
    """Validate the entire ZIP namespace even when only one member is read."""
    entries = archive.infolist()
    if len(entries) > 100_000:
        raise PackageError('archive contains too many files')
    if sum(item.file_size for item in entries) > 20 * 1024 ** 3:
        raise PackageError('archive expands beyond the configured safety limit')
    names, members = set(), {}
    for item in entries:
        normalized = item.filename.replace('\\', '/')
        name = normalized[:-1] if item.is_dir() else normalized
        try:
            validate_package_path(name)
        except ValueError as exc:
            raise PackageError(f'unsafe archive member: {item.filename}') from exc
        if name.casefold() in names:
            raise PackageError(f'unsafe or duplicate archive member: {item.filename}')
        if stat.S_ISLNK(item.external_attr >> 16):
            raise PackageError('archive links are not allowed')
        names.add(name.casefold())
        members[name] = item
    # A file cannot also be an ancestor of another member.
    files = {name.casefold() for name, item in members.items() if not item.is_dir()}
    for name in members:
        parts = name.split('/')
        if any('/'.join(parts[:n]).casefold() in files for n in range(1, len(parts))):
            raise PackageError('archive file is also a directory')
    return members


def read_package_bytes(path: Path, relative: str) -> bytes:
    validate_package_path(relative)
    path = checked_package_path(path)
    if path.is_dir():
        target = checked_package_path(path / relative)
        target.relative_to(path)
        with open_shared_reader(target) as handle:
            return handle.read()
    with open_shared_reader(path) as handle, zipfile.ZipFile(handle) as archive:
        members = safe_archive_members(archive)
        if relative not in members or members[relative].is_dir():
            raise PackageError(f'package member is missing: {relative}')
        return archive.read(members[relative])


def read_package_json(path: Path, relative: str):
    try:
        return json.loads(read_package_bytes(path, relative))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError(f'invalid package JSON: {relative}') from exc


def list_package_members(path: Path, prefix: str = '') -> list[str]:
    """List safe member names without extracting an archive or reading bytes."""
    prefix = validate_package_path(prefix.rstrip('/')) + '/' if prefix else ''
    path = checked_package_path(path)
    if path.is_dir():
        start = checked_package_path(path / prefix)
        return [member.relative_to(path).as_posix() for member in sorted(start.rglob('*'))
                if checked_package_path(member).is_file()]
    with open_shared_reader(path) as handle, zipfile.ZipFile(handle) as archive:
        return sorted(name for name, member in safe_archive_members(archive).items()
                      if name.startswith(prefix) and not member.is_dir())


_archives = OrderedDict()
_archive_lock = RLock()


def _trim_archives():
    total = sum(entry['size'] for entry in _archives.values())
    for key, entry in list(_archives.items()):
        if len(_archives) <= 4 and total <= 512 * 1024 ** 2:
            break
        if entry['users']:
            continue
        _archives.pop(key)
        total -= entry['size']
        entry['temporary'].cleanup()


@contextmanager
def open_readonly_package(path: Path):
    """Reuse a safe extracted archive while its source and extracted tree agree.

    Directory callers still own any required snapshot lock. Mutation paths must
    continue to use open_package, which always supplies their own staging tree.
    """
    path = checked_package_path(path)
    if path.is_dir():
        yield path
        return
    signature = file_fingerprint(path)
    key = (str(path), signature)
    with _archive_lock:
        entry = _archives.get(key)
        if entry:
            entry['users'] += 1
            _archives.move_to_end(key)
    if entry is None:
        temporary = tempfile.TemporaryDirectory(prefix='ga-read-package-')
        root = Path(temporary.name) / 'package'
        try:
            extract_archive(path, root)
            if file_fingerprint(path) != signature:
                raise PackageError('archive changed while opening its read view')
            fingerprint = tree_fingerprint(root)
            candidate = dict(temporary=temporary, root=root, signature=fingerprint,
                             size=sum(p.stat().st_size for p in root.rglob('*') if p.is_file()), users=1)
            with _archive_lock:
                entry = _archives.get(key)
                if entry is None:
                    entry = candidate
                    _archives[key] = entry
                else:
                    entry['users'] += 1
                    temporary.cleanup()
                _trim_archives()
        except BaseException:
            temporary.cleanup()
            raise
    try:
        if tree_fingerprint(entry['root']) != entry['signature']:
            raise PackageError('cached package read view has changed')
        yield entry['root']
    finally:
        with _archive_lock:
            entry['users'] -= 1
            _trim_archives()


@atexit.register
def _close_archive_views():
    for entry in list(_archives.values()):
        try:
            entry['temporary'].cleanup()
        except OSError:
            pass
    _archives.clear()


_experiments = OrderedDict()
_experiment_lock = RLock()


def validated_experiment(root: Path):
    """Cache integrity validation, with every content path in the signature."""
    from generative_agents.ga_protocol.packages.validation import validate_experiment_integrity
    root = checked_package_path(root)
    signature = tree_fingerprint(root)
    with _experiment_lock:
        entry = _experiments.get(root)
        if entry and entry[0] == signature:
            _experiments.move_to_end(root)
            return entry[1].model_copy(deep=True)
    manifest = validate_experiment_integrity(root)
    if tree_fingerprint(root) != signature:
        raise PackageError('experiment changed during integrity validation')
    with _experiment_lock:
        _experiments[root] = (signature, manifest)
        _experiments.move_to_end(root)
        while len(_experiments) > 8:
            _experiments.popitem(last=False)
    return manifest.model_copy(deep=True)
