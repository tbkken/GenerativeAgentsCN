"""Durable frame identities, independent of disposable Replay projections."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from generative_agents.ga_protocol.packages.io import PackageError, checked_package_path, read_json


def read_committed_frame(root: Path, run_id: str, step: int) -> dict:
    """Verify one immutable frame against its package-owned commit record."""
    record = read_json(checked_package_path(root / "commits" / f"step-{step:06d}.json"))
    relative = f"frames/step-{step:06d}.json.gz"
    if (not isinstance(record, dict) or record.get("schema_version") != 1
            or record.get("run_id") != str(run_id) or record.get("step_no") != step
            or record.get("frame") != relative):
        raise PackageError(f"frame commit identity mismatch at step {step}")
    data = checked_package_path(root / relative).read_bytes()
    if len(data) != record.get("size") or hashlib.sha256(data).hexdigest() != record.get("sha256"):
        raise PackageError(f"Replay frame hash mismatch at step {step}")
    try:
        document = json.loads(gzip.decompress(data).decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PackageError(f"invalid Replay frame at step {step}") from exc
    result = document.get("result") if isinstance(document, dict) else None
    if not isinstance(result, dict) or document.get("schema_version") != 1:
        raise PackageError(f"Replay frame has no supported StepResult at step {step}")
    for key in ("run_id", "attempt_id", "step_no", "virtual_time"):
        if result.get(key) != record.get(key):
            raise PackageError(f"Replay frame {key} mismatch at step {step}")
    return result


def validate_committed_frames(root: Path, run_id: str, committed_step: int) -> None:
    """Validate every visible frame; later staged frames remain invisible."""
    for step in range(1, committed_step + 1):
        read_committed_frame(root, run_id, step)
