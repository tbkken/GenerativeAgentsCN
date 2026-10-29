"""Disposable in-progress UI preview. Replay never reads this file."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from generative_agents.ga_protocol.packages.io import atomic_write_json, read_json, checked_package_path, PackageError


def clear_runtime_preview(root: str | Path) -> None:
    (Path(root) / "runtime-preview.json").unlink(missing_ok=True)


def publish_runtime_preview(context, builder, *, committed_step: int, actor_sequence: int, actor_total: int) -> None:
    conversations = json.loads(json.dumps([asdict(value) for value in builder.conversations], default=str))
    atomic_write_json(context.paths.root / "runtime-preview.json", {
        "run_id": str(context.run_id), "attempt_id": str(context.attempt_id),
        "step_no": builder.step_no, "committed_step": committed_step,
        "actor_sequence": actor_sequence, "actor_total": actor_total,
        "uncommitted": True, "conversations": conversations,
    })


def read_runtime_preview(root: str | Path) -> dict | None:
    """Read only the active Attempt's uncommitted next Step for an operator UI."""
    try:
        root = checked_package_path(Path(root))
        status = read_json(root / "status.json")
        manifest = read_json(root / "run.json")
        preview = read_json(root / "runtime-preview.json")
        latest = read_json(root / "status.json")
    except (FileNotFoundError, ValueError, OSError, PackageError):
        return None
    if not all(isinstance(item, dict) for item in (status, manifest, preview, latest)):
        return None
    if status != latest or status.get("status") != "RUNNING":
        return None
    if (status.get("run_id") != manifest.get("run_id")
            or preview.get("run_id") != manifest.get("run_id")
            or preview.get("attempt_id") != status.get("active_attempt_id")
            or preview.get("committed_step") != status.get("committed_step")
            or not isinstance(status.get("committed_step"), int)
            or preview.get("step_no") != status["committed_step"] + 1
            or preview.get("uncommitted") is not True):
        return None
    return preview
