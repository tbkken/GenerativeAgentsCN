"""The local batch entry point must reject a remote destination before importing the UE launcher."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows batch entry point")
def test_remote_target_in_a_project_path_with_cmd_metacharacters(tmp_path):
    project = tmp_path / "project & (studio)"
    source = Path(__file__).resolve().parents[1]
    for name in ("start-simworld.bat", "services.toml", "pyproject.toml", "src/generative_agents/adapters/service_config.py"):
        destination = project / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    config = tmp_path / "remote services.toml"
    config.write_text('[simworld]\nurl = "https://gpu.example.invalid/studio"\n', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GA_")}
    env.update(GA_SERVICES_CONFIG=str(config), GA_SIMWORLD_PYTHON=sys.executable, GA_SIMWORLD_NO_PAUSE="1")
    result = subprocess.run(
        [env["COMSPEC"], "/d", "/c", "call", str(project / "start-simworld.bat")],
        cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert "Cannot start a local SimWorld service" in result.stdout
    assert "远程服务" in result.stderr
