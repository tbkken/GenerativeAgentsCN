"""Verify wheel contents and run the installed distribution outside the checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def verify(wheel: Path) -> None:
    package = ROOT / "src" / "generative_agents"
    expected = {
        path.relative_to(ROOT / "src").as_posix(): path
        for path in package.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    for relative in ("ga_studio/bundled", "adapters/web/static", "ga_studio/storage/migrations"):
        expected.update({
            path.relative_to(ROOT / "src").as_posix(): path
            for path in (package / relative).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        })
    with zipfile.ZipFile(wheel) as archive:
        actual = {name for name in archive.namelist() if name.startswith("generative_agents/") and not name.endswith("/")}
        assert actual == set(expected), {
            "missing": sorted(set(expected) - actual),
            "unexpected": sorted(actual - set(expected)),
        }
        for name, source in expected.items():
            assert archive.read(name) == source.read_bytes(), name
    print(json.dumps({"wheel": wheel.name, "files": len(expected),
                      "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}), flush=True)
    with tempfile.TemporaryDirectory(prefix="ga-installed-release-") as temporary:
        root = Path(temporary)
        installed = root / "installed"
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-compile",
                        "--target", str(installed), str(wheel)], check=True, cwd=root)
        environment = os.environ.copy()
        environment.update(PYTHONPATH=str(installed), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        subprocess.run([sys.executable, str(ROOT / "tests/release/installed_acceptance.py"),
                        str(installed), str(root / "workspace")],
                       check=True, cwd=root, env=environment, timeout=240)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    verify(parser.parse_args().wheel.resolve())
