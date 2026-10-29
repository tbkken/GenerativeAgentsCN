"""Exercise the Windows launcher with disposable, uninstalled interpreters."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows batch launcher")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def launcher(tmp_path: Path) -> Path:
    root = tmp_path / "launcher space (test)"
    root.mkdir()
    # Never inspect or stop the real development service.
    script = (ROOT / "restart-web.bat").read_text(encoding="utf-8")
    assert 'set "GA_RESTART_PORT=8000"' in script
    (root / "restart-web.bat").write_text(
        script.replace('set "GA_RESTART_PORT=8000"', 'set "GA_RESTART_PORT=0"'),
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")],
        check=True, capture_output=True, timeout=30,
    )
    return root


def _run_launcher(
    root: Path, *, without_path_python: bool = False,
    var_dir: str | None = None, database_url: str | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("GA_PORT", None)
    env.pop("GA_VAR_DIR", None)
    env.pop("GA_DATABASE_URL", None)
    if var_dir is not None:
        env["GA_VAR_DIR"] = var_dir
    if database_url is not None:
        env["GA_DATABASE_URL"] = database_url
    env["PYTHONNOUSERSITE"] = "1"
    if without_path_python:
        system = Path(env["SYSTEMROOT"]) / "System32"
        env["PATH"] = os.pathsep.join((str(system), str(system / "WindowsPowerShell/v1.0")))
    try:
        return subprocess.run(
            [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(root / "restart-web.bat")],
            cwd=root.parent, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        data_root = root / (var_dir or "var/studio")
        log = data_root / "logs/web.stderr.log"
        detail = log.read_text(encoding="utf-8", errors="replace") if log.exists() else "no stderr log"
        pytest.fail(f"Launcher did not report its failure promptly. stderr log: {detail}; output: {exc.stdout!r}")


def test_uninstalled_interpreter_fails_before_stopping_old_service(launcher: Path):
    old_stdout = launcher / "var/studio/logs/web.stdout.log"
    old_stderr = launcher / "var/studio/logs/web.stderr.log"
    old_stdout.parent.mkdir(parents=True)
    old_stdout.write_text("existing service stdout", encoding="utf-8")
    old_stderr.write_text("existing service stderr", encoding="utf-8")
    completed = _run_launcher(launcher)
    output = completed.stdout + completed.stderr
    python = launcher / ".venv/Scripts/python.exe"
    assert completed.returncode != 0
    assert "ModuleNotFoundError: No module named 'generative_agents'" in output
    assert str(python) in output
    assert '-m pip install -e ".[runtime,studio,web]"' in output
    assert str(launcher / "var/studio/logs/web.preflight.log") in output
    assert "Checking port" not in output
    assert "Starting Web" not in output
    assert not (launcher / "var/studio/web.pid").exists()
    assert old_stdout.read_text(encoding="utf-8") == "existing service stdout"
    assert old_stderr.read_text(encoding="utf-8") == "existing service stderr"


@pytest.mark.parametrize("data_selection", ["default", "relative", "absolute", "database", "both"])
def test_venv_without_path_python_reports_background_exit_immediately(launcher: Path, data_selection: str):
    var_dir = None
    if data_selection in {"relative", "both"}:
        var_dir = "author data (custom)"
    elif data_selection == "absolute":
        var_dir = str(launcher.parent / "external data (custom)")
    data_root = launcher / (var_dir or "var/studio")
    database_url = None
    if data_selection in {"database", "both"}:
        database_url = "sqlite:///selected database (custom).db"
    expected_database_url = database_url or "sqlite:///" + (data_root / "generative-agents.db").as_posix()
    module = launcher / ".venv/Lib/site-packages/generative_agents/adapters/web/main.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "import argparse\n"
        "from pathlib import Path\n"
        "import sys\n"
        "parser = argparse.ArgumentParser()\n"
        "for name in ('database-url', 'var-dir', 'host', 'port', 'max-concurrent-runs'):\n"
        "    parser.add_argument('--' + name, required=True)\n"
        "args = parser.parse_args()\n"
        f"assert args.var_dir == {str(data_root)!r}\n"
        f"assert args.database_url == {expected_database_url!r}\n"
        f"assert Path.cwd() == Path({str(launcher)!r})\n"
        "print('TEST_WEB_CRASH: process stopped before serving', file=sys.stderr)\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )
    completed = _run_launcher(launcher, without_path_python=True, var_dir=var_dir, database_url=database_url)
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "TEST_WEB_CRASH: process stopped before serving" in output
    assert "exited" in output.lower()
    assert str(data_root / "logs/web.stdout.log") in output
    assert str(data_root / "logs/web.stderr.log") in output
    assert (data_root / "logs/web.preflight.log").is_file()
    assert (data_root / "web.pid").read_text(encoding="ascii").strip().isdigit()
    assert not (launcher / "var/generative-agents.db").exists()
    assert not (launcher / "data").exists()
    assert "30 seconds" not in output
    assert "restarted successfully" not in output
