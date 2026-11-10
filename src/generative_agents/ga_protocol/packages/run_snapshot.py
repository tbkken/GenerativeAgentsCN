"""The authoritative file selection for a portable, stopped Run snapshot."""
from pathlib import Path

from generative_agents.ga_protocol.packages.constants import INTEGRITY_MANIFEST
from generative_agents.ga_protocol.packages.io import iter_package_files, read_json


def iter_run_snapshot_files(root: Path, *, committed_step: int):
    """Yield evidence files, excluding only host locks and transient previews."""
    excluded = {"worker.lock", "checkpoint.lock", "artifact.lock", "runtime-preview.json", INTEGRITY_MANIFEST}
    for relative, path in iter_package_files(root):
        if relative in excluded or relative.endswith(".lock"):
            continue
        if relative == "simworld-pending-step.json" and read_json(path)["step"] <= committed_step:
            continue
        yield relative, path
