"""Controlled lookup for a Run-owned replay artifact.

The former Flask debug server accepted a display name and joined it into a
filesystem path. HTTP delivery now belongs to FastAPI after database ownership
checks; this module only resolves an already validated RunPaths instance.
"""

from __future__ import annotations

from pathlib import Path

from generative_agents.runtime.context import RunPaths


def resolve_replay_artifact(paths: RunPaths) -> Path:
    target = (paths.artifacts / "movement.json").resolve()
    artifact_root = paths.artifacts.resolve()
    if target.parent != artifact_root:
        raise ValueError("replay artifact escaped its Run directory")
    if not target.is_file() or target.is_symlink():
        raise FileNotFoundError("Run replay artifact is not ready")
    return target
