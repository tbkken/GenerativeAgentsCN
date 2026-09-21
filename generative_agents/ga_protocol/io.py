"""Safe, deterministic package IO shared by all four modules."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .constants import INTEGRITY_MANIFEST


class PackageError(ValueError):
    """Raised when a package violates the portable file protocol."""


def checked_package_path(path: Path) -> Path:
    """Reject links in every component before resolving a package-owned path.

    Windows junctions are reparse points, not symbolic links. Checking only the
    final file, or checking after resolve(), silently loses this boundary.
    """
    path = Path(path).absolute()
    for component in (*reversed(path.parents), path):
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise PackageError("links and reparse points are not allowed in package paths")
    return path.resolve()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def read_json(path: Path) -> object:
    path = checked_package_path(path)
    try:
        with open_shared_reader(path) as handle:
            content = handle.read()
        return json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"invalid JSON file: {path}") from exc


@contextmanager
def open_shared_reader(path: Path):
    """A snapshot reader must not prevent atomic replacement on Windows.

    The CRT's ordinary open() omits FILE_SHARE_DELETE. An overlapping Web
    reader can then deny the Runtime writer its rename even though it is only
    reading. A shared handle keeps reading the old file after replacement.
    """
    if os.name != "nt":
        with path.open("rb") as handle:
            yield handle
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    raw = create(str(path.resolve()), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if raw == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(raw, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(raw)
        raise
    with os.fdopen(descriptor, "rb") as handle:
        yield handle


def _replace_file(source: Path, target: Path) -> None:
    if os.name != "nt" or not target.is_file():
        os.replace(source, target)
        return
    # MoveFileEx (used by os.replace) can still reject an open destination.
    # ReplaceFileW supports replacing it while FILE_SHARE_DELETE readers keep
    # their original snapshot. Never unlink/truncate the live target first.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID]
    replace.restype = wintypes.BOOL
    if not replace(str(target.resolve()), str(source.resolve()), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        with open_shared_reader(path) as handle:
            if handle.read() == content:
                return
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        delays = iter((0.02, 0.05, 0.1, 0.2, 0.4, 0.8))
        while True:
            try:
                _replace_file(temporary, path)
                break
            except PermissionError as exc:
                delay = next(delays, None)
                if getattr(exc, 'winerror', None) not in (5, 32, 33) or delay is None:
                    raise
                time.sleep(delay)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: object) -> None:
    atomic_write_bytes(path, canonical_json_bytes(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise PackageError(f"unsafe package path: {relative}")
    return relative


def iter_package_files(root: Path, *, exclude: set[str] | None = None) -> Iterator[tuple[str, Path]]:
    root = checked_package_path(root)
    excluded = exclude or set()
    if not root.is_dir():
        raise PackageError(f"package root is not a directory: {root}")
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        checked_package_path(path)
        if path.is_dir():
            continue
        relative = _safe_relative_path(path.resolve(), root)
        if relative not in excluded:
            yield relative, path


def build_integrity_document(root: Path) -> dict:
    files = {
        relative: sha256_file(path)
        for relative, path in iter_package_files(root, exclude={INTEGRITY_MANIFEST})
    }
    root_digest = hashlib.sha256()
    for relative, digest in sorted(files.items()):
        root_digest.update(relative.encode("utf-8"))
        root_digest.update(b"\0")
        root_digest.update(digest.encode("ascii"))
        root_digest.update(b"\n")
    return {
        "schema_version": 1,
        "algorithm": "sha256",
        "files": files,
        "root_sha256": root_digest.hexdigest(),
    }


def write_integrity_manifest(root: Path) -> dict:
    document = build_integrity_document(root)
    atomic_write_json(root / INTEGRITY_MANIFEST, document)
    return document


def verify_integrity(root: Path, *, require_exact_files: bool = True) -> dict:
    document = read_json(root / INTEGRITY_MANIFEST)
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise PackageError("unsupported integrity manifest")
    declared = document.get("files")
    if not isinstance(declared, dict) or not all(
        isinstance(path, str) and isinstance(digest, str)
        for path, digest in declared.items()
    ):
        raise PackageError("integrity file map is invalid")
    actual = build_integrity_document(root)
    if require_exact_files and actual["files"] != declared:
        missing = sorted(set(declared) - set(actual["files"]))
        extra = sorted(set(actual["files"]) - set(declared))
        changed = sorted(
            path
            for path in set(declared) & set(actual["files"])
            if declared[path] != actual["files"][path]
        )
        raise PackageError(
            f"package integrity mismatch (missing={missing}, extra={extra}, changed={changed})"
        )
    if actual["root_sha256"] != document.get("root_sha256"):
        raise PackageError("package root hash mismatch")
    return document


def copy_package_tree(source: Path, destination: Path) -> None:
    """Copy a package while rejecting links and pre-existing destinations."""

    source = checked_package_path(source)
    destination = checked_package_path(destination)
    if destination.exists():
        raise PackageError(f"destination already exists: {destination}")
    destination.mkdir(parents=True)
    try:
        for relative, path in iter_package_files(source):
            target = destination / PurePosixPath(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def seal_directory(source: Path, archive: Path) -> Path:
    """Create a deterministic ZIP representation of a package directory."""

    source = checked_package_path(source)
    archive = checked_package_path(archive)
    if archive.exists():
        raise PackageError(f"archive already exists: {archive}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_name(f".{archive.name}.{uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as package:
            for relative, path in iter_package_files(source):
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                package.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
    return archive


def extract_archive(
    archive: Path,
    destination: Path,
    *,
    max_files: int = 100_000,
    max_uncompressed_bytes: int = 20 * 1024 * 1024 * 1024,
) -> Path:
    """Safely extract an archive without trusting ZIP member paths or links."""

    archive = checked_package_path(archive)
    destination = checked_package_path(destination)
    if destination.exists():
        raise PackageError(f"destination already exists: {destination}")
    destination.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive, "r") as package:
            members = package.infolist()
            if len(members) > max_files:
                raise PackageError("archive contains too many files")
            if sum(member.file_size for member in members) > max_uncompressed_bytes:
                raise PackageError("archive expands beyond the configured safety limit")
            seen: set[str] = set()
            for member in members:
                normalized = member.filename.replace("\\", "/")
                pure = PurePosixPath(normalized)
                if (
                    not normalized
                    or pure.is_absolute()
                    or any(part in {"", ".", ".."} for part in pure.parts)
                    or normalized in seen
                ):
                    raise PackageError(f"unsafe or duplicate archive member: {member.filename}")
                seen.add(normalized)
                unix_mode = member.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    raise PackageError(f"archive links are not allowed: {member.filename}")
                target = destination.joinpath(*pure.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        return destination
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


@contextmanager
def open_package(path: Path) -> Iterator[Path]:
    """Yield a package directory for either directory or ZIP input."""

    path = checked_package_path(path)
    if path.is_dir():
        # Readers of immutable Run/SEALED content must not serialize one another.
        # Studio owns locking across mutations of editable workspaces.
        yield path
        return
    if not path.is_file():
        raise PackageError(f"package does not exist: {path}")
    temporary_root = Path(tempfile.mkdtemp(prefix="ga-package-"))
    extracted = temporary_root / "package"
    try:
        extract_archive(path, extracted)
        yield extracted
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
