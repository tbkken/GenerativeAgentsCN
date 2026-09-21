"""Reentrant, cross-process access to one package; lock files stay outside it."""

import hashlib
import os
import tempfile
from contextlib import contextmanager
from functools import lru_cache, wraps
from pathlib import Path

from filelock import FileLock


@lru_cache(maxsize=256)
def _lock(root):
    directory = Path(tempfile.gettempdir()) / 'ga-package-locks'
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(root.encode()).hexdigest()
    return FileLock(str(directory / f'{key}.lock'), timeout=15)


@contextmanager
def package_lock(root):
    with _lock(os.path.normcase(str(Path(root).resolve()))):
        yield


def locked_package(operation):
    @wraps(operation)
    def wrapped(root, *args, **kwargs):
        with package_lock(root):
            return operation(root, *args, **kwargs)
    return wrapped
