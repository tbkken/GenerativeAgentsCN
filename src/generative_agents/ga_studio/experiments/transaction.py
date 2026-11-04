"""Validate a complete draft edit before publishing its changed files."""
from pathlib import Path
import tempfile
import shutil

from generative_agents.ga_protocol.packages.io import atomic_output, copy_package_tree, read_json, write_integrity_manifest, sha256_file
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
        changed = []
        for relative in previous.keys() | incoming.keys():
            before, after = previous.get(relative), incoming.get(relative)
            if before is None or after is None or before.stat().st_size != after.stat().st_size or sha256_file(before) != sha256_file(after):
                changed.append(relative)
        backups = Path(temporary) / "previous"
        for relative in changed:
            if relative in previous:
                target = backups / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(previous[relative], target)
        def publish(relative, source):
            target = root / relative
            if source is None:
                target.unlink(missing_ok=True)
            else:
                with source.open("rb") as reader, atomic_output(target) as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
        try:
            for relative in sorted(changed):
                publish(relative, incoming.get(relative))
        except Exception:
            for relative in changed:
                publish(relative, backups / relative if relative in previous else None)
            raise
        return manifest, read_json(staged / "integrity/sha256.json")["root_sha256"]
