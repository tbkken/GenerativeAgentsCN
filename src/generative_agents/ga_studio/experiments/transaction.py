"""Validate a complete draft edit before publishing its changed files."""
from pathlib import Path
import tempfile

from generative_agents.ga_protocol.packages.io import atomic_write_bytes, copy_package_tree, read_json, write_integrity_manifest
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory


def edit_experiment(root: Path, operation):
    """Caller holds the package lock; failed validation leaves the draft intact."""
    with tempfile.TemporaryDirectory(prefix="ga-draft-edit-") as temporary:
        staged = Path(temporary) / "experiment"
        copy_package_tree(root, staged)
        operation(staged)
        write_integrity_manifest(staged)
        manifest = validate_experiment_directory(staged)
        previous = {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}
        incoming = {p.relative_to(staged).as_posix(): p for p in staged.rglob("*") if p.is_file()}
        changes = {}
        for relative in previous.keys() | incoming.keys():
            before = previous[relative].read_bytes() if relative in previous else None
            after = incoming[relative].read_bytes() if relative in incoming else None
            if before != after:
                changes[relative] = (before, after)
        try:
            for relative, (_, content) in changes.items():
                if content is None:
                    (root / relative).unlink(missing_ok=True)
                else:
                    atomic_write_bytes(root / relative, content)
        except Exception:
            for relative, (content, _) in changes.items():
                if content is None:
                    (root / relative).unlink(missing_ok=True)
                else:
                    atomic_write_bytes(root / relative, content)
            raise
        return manifest, read_json(staged / "integrity/sha256.json")["root_sha256"]
