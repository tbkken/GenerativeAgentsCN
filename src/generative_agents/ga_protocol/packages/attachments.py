"""Resource bytes with bounded-memory hashing, copying and optional snapshot ownership."""
from __future__ import annotations

import hashlib
import io
import mmap
import shutil
from collections.abc import MutableMapping
from contextlib import contextmanager
from pathlib import Path

from generative_agents.ga_protocol.packages.io import (
    PackageError, atomic_output, atomic_write_bytes, checked_package_path, open_shared_reader, sha256_file,
)


class AttachmentFiles(MutableMapping):
    """The public mapping returns bytes; package internals stream file-backed entries."""
    def __init__(self, sources=None, *, owner=None):
        self._sources = {}
        self._owners = []
        if isinstance(sources, AttachmentFiles):
            self._sources.update(sources._sources)
            self._owners.extend(sources._owners)
        elif sources:
            self._sources.update(sources)
        if owner is not None:
            self._owners.append(owner)

    def __deepcopy__(self, memo):
        return AttachmentFiles(self)

    def __iter__(self):
        return iter(self._sources)

    def __len__(self):
        return len(self._sources)

    def __contains__(self, key):
        return key in self._sources

    def __setitem__(self, key, value):
        self._sources[key] = value

    def __delitem__(self, key):
        del self._sources[key]

    def source(self, key):
        value = self._sources[key]
        if isinstance(value, Path):
            return checked_package_path(value)
        if not isinstance(value, bytes):
            raise PackageError(f"resource attachment must contain bytes: {key}")
        return value

    def __getitem__(self, key):
        source = self.source(key)
        if isinstance(source, bytes):
            return source
        with open_shared_reader(source) as handle:
            return handle.read()

    def subset(self, paths):
        result = AttachmentFiles()
        result.include(self, {key:key for key in paths})
        return result

    def include(self, other, mapping):
        for source, target in mapping.items():
            self._sources[target] = other.source(source)
        self._owners.extend(other._owners)

    @contextmanager
    def open(self, key):
        source = self.source(key)
        if isinstance(source, bytes):
            with io.BytesIO(source) as stream:
                yield stream
        else:
            with open_shared_reader(source) as stream:
                yield stream

    def size(self, key):
        source = self.source(key)
        return len(source) if isinstance(source, bytes) else source.stat().st_size

    def digest(self, key):
        source = self.source(key)
        return hashlib.sha256(source).hexdigest() if isinstance(source, bytes) else sha256_file(source)

    @contextmanager
    def buffer(self, key):
        source = self.source(key)
        if isinstance(source, bytes):
            yield source
        elif source.stat().st_size == 0:
            yield b""
        else:
            with open_shared_reader(source) as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
                yield data

    def write_to(self, key, target):
        source = self.source(key)
        if isinstance(source, bytes):
            atomic_write_bytes(target, source)
        elif source != target:
            with open_shared_reader(source) as reader, atomic_output(target) as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
