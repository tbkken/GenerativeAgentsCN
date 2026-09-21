"""Native Windows junction boundaries for portable Run readers."""
import os
import subprocess
from pathlib import Path
import pytest
from tests.architecture.test_portable_storage_security import (
    portable_storage, substitute_link, assert_rejected, artifact_urls, replay_urls,
)
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows junctions")

def _make_junction(link: Path, target: Path) -> None:
    """Create the strongest unprivileged Windows reparse primitive available."""

    if not hasattr(Path, "is_junction"):
        pytest.skip("this Python runtime cannot identify Windows junctions")
    command = (
        "New-Item -ItemType Junction "
        f"-Path '{str(link).replace("'", "''")}' "
        f"-Target '{str(target).replace("'", "''")}' | Out-Null"
    )
    created = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert created.returncode == 0, created.stderr or created.stdout
    assert link.is_junction() and not link.is_symlink()


def _remove_only_junction(link: Path, target_file: Path, expected: bytes) -> None:
    """Prove cleanup removes the link itself and never traverses into its target."""

    assert link.is_junction()
    link.rmdir()
    assert not link.exists()
    assert target_file.is_file()
    assert target_file.read_bytes() == expected


@pytest.mark.parametrize("kind", ["artifacts", "frames"])
@pytest.mark.parametrize("position", ["parent", "intermediate", "leaf", "cross_run"])
def test_portable_readers_reject_junctions(portable_storage, kind, position):
    client, root, run_id, _ = portable_storage
    filename = "report.txt" if kind == "artifacts" else "step-000001.json.gz"
    link = root if position == "parent" else root / kind
    target = None
    if position in {"leaf", "cross_run"}:
        target = root.parent / ("external-target" if position == "cross_run" else "nested-target") / kind
        target.mkdir(parents=True)
        (target / filename).write_bytes((root / kind / filename).read_bytes())
    physical = substitute_link(link, _make_junction, target=target)
    target_file = (target / filename if target else physical / kind / filename if position == "parent" else physical / filename)
    content = target_file.read_bytes()
    try:
        urls = artifact_urls(run_id) if kind == "artifacts" else replay_urls(run_id)
        assert_rejected(client, urls, root)
    finally:
        _remove_only_junction(link, target_file, content)
