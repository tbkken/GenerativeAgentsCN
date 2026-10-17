"""Link this checkout's editable install to src using a relative .pth entry."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
from pathlib import Path
import sys
import sysconfig
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def _replace(path: Path, content: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    environment = (ROOT / ".venv").resolve()
    if Path(sys.prefix).resolve() != environment:
        raise SystemExit("Run this tool with this checkout's .venv Python.")
    site_packages = Path(sysconfig.get_path("purelib")).resolve()
    if not site_packages.is_relative_to(environment):
        raise SystemExit("The interpreter's site-packages is outside this checkout's .venv.")
    source = ROOT / "src"
    if not (source / "generative_agents").is_dir():
        raise SystemExit("This checkout has no src/generative_agents directory.")

    candidates = list(site_packages.glob("__editable__.generative_agents_cn-*.pth"))
    if len(candidates) != 1:
        raise SystemExit(
            f"Expected exactly one generative-agents-cn editable .pth; found {len(candidates)}. "
            "Install the project in this checkout's .venv first."
        )
    pth = candidates[0]
    distribution = pth.name.removeprefix("__editable__.").removesuffix(".pth")
    record = site_packages / f"{distribution}.dist-info" / "RECORD"
    if pth.is_symlink() or not pth.is_file():
        raise SystemExit("The editable .pth must be a regular file.")
    if not record.is_file() or record.is_symlink() or not record.resolve().is_relative_to(site_packages):
        raise SystemExit("The editable install has no local, regular dist-info/RECORD file.")

    original_pth = pth.read_bytes()
    rows = list(csv.reader(io.StringIO(record.read_text(encoding="utf-8"), newline="")))
    if any(len(row) != 3 for row in rows):
        raise SystemExit("The editable install's RECORD contains malformed rows.")
    matches = [row for row in rows if row[0] == pth.name]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one RECORD entry for {pth.name}; found {len(matches)}.")

    relative = Path(os.path.relpath(source, site_packages)).as_posix()
    content = (relative + "\n").encode("utf-8")
    digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
    matches[0][1:] = [f"sha256={digest}", str(len(content))]
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    updated_record = output.getvalue().encode("utf-8")

    _replace(pth, content)
    try:
        _replace(record, updated_record)
    except OSError:
        _replace(pth, original_pth)
        raise
    print(f"Linked {pth.name} -> {relative}; updated RECORD.")


if __name__ == "__main__":
    main()
