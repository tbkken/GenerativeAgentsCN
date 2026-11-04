"""Exercise the Windows launcher with disposable, uninstalled interpreters."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows batch launcher")
ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def launcher(tmp_path: Path) -> Path:
    root = tmp_path / "launcher space (test)"
    root.mkdir()
    shutil.copyfile(ROOT / "restart-web.bat", root / "restart-web.bat")
    (root / "tools").mkdir()
    shutil.copyfile(ROOT / "tools/watch_web_logs.ps1", root / "tools/watch_web_logs.ps1")
    (root / "services.toml").write_text(f'[studio]\nport = {_free_port()}\n', encoding="utf-8")
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")],
        check=True, capture_output=True, timeout=30,
    )
    return root


def _install_web_stub(root: Path, body: str):
    source = root / "test-install/src"
    adapters = source / "generative_agents/adapters"
    web = adapters / "web"
    web.mkdir(parents=True)
    shutil.copyfile(ROOT / "src/generative_agents/adapters/service_config.py", adapters / "service_config.py")
    shutil.copyfile(ROOT / "services.toml", source.parent / "services.toml")
    (source.parent / "pyproject.toml").touch()
    site = root / ".venv/Lib/site-packages"
    (site / "test-install.pth").write_text(os.path.relpath(source, site), encoding="ascii")
    (web / "main.py").write_text(
        "import argparse\n"
        "from pathlib import Path\n"
        "import sys\n"
        "parser = argparse.ArgumentParser()\n"
        "for name in ('database-url', 'var-dir', 'host', 'port', 'max-concurrent-runs'):\n"
        "    parser.add_argument('--' + name, required=True)\n"
        "args = parser.parse_args()\n" + body,
        encoding="utf-8",
    )


def _launcher_invocation(
    root: Path, *arguments: str, without_path_python: bool = False,
    var_dir: str | None = None, database_url: str | None = None,
    overrides: dict | None = None,
):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    for name in ("GA_PORT", "GA_HOST", "GA_SERVICES_CONFIG", "GA_SIMWORLD_URL", "GA_SIMWORLD_PORT"):
        env.pop(name, None)
    env.pop("GA_VAR_DIR", None)
    env.pop("GA_DATABASE_URL", None)
    if var_dir is not None:
        env["GA_VAR_DIR"] = var_dir
    if database_url is not None:
        env["GA_DATABASE_URL"] = database_url
    env.update(overrides or {})
    env["PYTHONNOUSERSITE"] = "1"
    if without_path_python:
        system = Path(env["SYSTEMROOT"]) / "System32"
        env["PATH"] = os.pathsep.join((str(system), str(system / "WindowsPowerShell/v1.0")))
    command = [env.get("COMSPEC", "cmd.exe"), "/d", "/c", "call", str(root / "restart-web.bat"), *arguments]
    return command, env


def _run_launcher(root: Path, *arguments: str, **settings) -> subprocess.CompletedProcess:
    command, env = _launcher_invocation(root, *arguments, **settings)
    # A detached Windows child can retain inherited pipe handles beyond cmd's exit.
    # Capture to files so completion measures cmd.exe, independently of Web's lifetime.
    output = root / "launcher-output.txt"
    try:
        with output.open("wb") as capture:
            result = subprocess.run(command, cwd=root.parent, env=env, stdout=capture,
                                    stderr=subprocess.STDOUT, timeout=45)
        return subprocess.CompletedProcess(command, result.returncode,
                                          output.read_text(encoding="utf-8", errors="replace"), "")
    except subprocess.TimeoutExpired as exc:
        data_root = root / (env.get("GA_VAR_DIR") or "var/studio")
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
    _install_web_stub(launcher,
        f"assert args.var_dir == {str(data_root)!r}\n"
        f"assert args.database_url == {expected_database_url!r}\n"
        f"assert Path.cwd() == Path({str(launcher)!r})\n"
        "print('TEST_WEB_CRASH: process stopped before serving', file=sys.stderr)\n"
        "sys.exit(7)\n",
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


def _wait_for_display(process: subprocess.Popen, output: Path, *expected: str) -> str:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        displayed = output.read_text(encoding="utf-8", errors="replace")
        assert process.poll() is None, displayed
        if all(message in displayed for message in expected):
            return displayed
        time.sleep(0.1)
    pytest.fail(f"Timed out waiting for {expected!r}. Launcher output: {displayed}")


def test_restart_uses_config_replaces_service_and_follows_live_logs(launcher: Path):
    port = _free_port()
    config = launcher.parent / "selected services.toml"
    config.write_text(f'[studio]\nhost = "0.0.0.0"\nport = {port}\n', encoding="utf-8")
    overrides = {"GA_SERVICES_CONFIG": str(config)}
    _install_web_stub(launcher, """
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import time
assert args.host == '0.0.0.0'
root = Path(args.var_dir)
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == '/api/studio/health'
        if self.headers.get('X-Test-Log'):
            print('实时消息 stdout')
            print('实时消息 stderr', file=sys.stderr)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('X-Listener-Pid', str(os.getpid()))
        self.end_headers()
        self.wfile.write(b'{"status":"ok","runtime_truth":"files","database_owner":"ga_studio"}')
with HTTPServer((args.host, int(args.port)), Health) as server:
    (root / 'listener.pid').write_text(str(os.getpid()))
    print('startup message on stdout')
    print('startup message on stderr', file=sys.stderr)
    server.timeout = 0.2
    deadline = time.monotonic() + 65
    while not (root / 'stop').exists() and time.monotonic() < deadline:
        server.handle_request()
""")
    data = launcher / "var/studio"
    follower = None
    try:
        first = _run_launcher(launcher, "--no-follow", overrides=overrides)
        assert first.returncode == 0, first.stdout + first.stderr
        assert f"Health URL: http://127.0.0.1:{port}/api/studio/health" in first.stdout
        assert f"restarted successfully: http://127.0.0.1:{port}/" in first.stdout
        first_listener = (data / "listener.pid").read_text()
        command, env = _launcher_invocation(launcher, overrides=overrides)
        output = launcher / "live-output.txt"
        with output.open("wb") as capture:
            follower = subprocess.Popen(command, cwd=launcher.parent, env=env, stdout=capture,
                                        stderr=subprocess.STDOUT)
        _wait_for_display(follower, output, f"Stopping old Web service. PID={first_listener}",
                          "restarted successfully", "startup message on stdout", "startup message on stderr")
        listener = (data / "listener.pid").read_text()
        assert listener != first_listener
        health_url = f"http://127.0.0.1:{port}/api/studio/health"
        with urlopen(Request(health_url, headers={"X-Test-Log": "1"}), timeout=5) as response:
            assert response.headers["X-Listener-Pid"] == listener
        displayed = _wait_for_display(follower, output, "实时消息 stdout", "实时消息 stderr")
        assert displayed.count("实时消息 stdout") == displayed.count("实时消息 stderr") == 1

        # Close only this launcher's foreground log viewer, leaving the service running.
        subprocess.run([
            "powershell", "-NoProfile", "-Command",
            f"$viewer = @(Get-CimInstance Win32_Process -Filter 'ParentProcessId = {follower.pid}' | "
            "Where-Object { $_.Name -eq 'powershell.exe' -and $_.CommandLine -like '*watch_web_logs.ps1*' }); "
            "if ($viewer.Count -ne 1) { exit 1 }; Stop-Process -Id $viewer[0].ProcessId -ErrorAction Stop",
        ], check=True, timeout=10)
        follower.wait(timeout=10)
        with urlopen(health_url, timeout=5) as response:
            assert response.headers["X-Listener-Pid"] == listener
    finally:
        (data / "stop").touch()
        if follower is not None and follower.poll() is None:
            subprocess.run(["taskkill", "/PID", str(follower.pid), "/T", "/F"], capture_output=True, timeout=10)
            follower.wait(timeout=10)
        # Allow the server to release its Windows log handles before pytest cleanup.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as sock:
                sock.settimeout(0.2)
                if sock.connect_ex(("127.0.0.1", port)) != 0:
                    break
            time.sleep(0.2)


def test_conflicting_listener_is_protected_and_bad_config_fails_before_stop(launcher: Path):
    _install_web_stub(launcher, "raise AssertionError('must not start the Web service')\n")
    command = (
        "import socket, sys; s = socket.socket(); s.bind(('127.0.0.1', 0)); "
        "s.listen(); print(s.getsockname()[1], flush=True); sys.stdin.readline()"
    )
    with subprocess.Popen([sys.executable, "-c", command], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as owner:
        try:
            port = int(owner.stdout.readline())
            config = launcher / "services.toml"
            config.write_text(f'[studio]\nport = {port}\n', encoding="utf-8")
            result = _run_launcher(launcher)
            assert result.returncode != 0
            assert "occupied by another program" in result.stdout + result.stderr
            assert owner.poll() is None
            config.write_text(f'[studio]\nport = {port}\nhost = "x & echo injected>marker"\n', encoding="utf-8")
            result = _run_launcher(launcher)
            assert result.returncode != 0
            assert "Checking port" not in result.stdout
            assert not (launcher / "marker").exists()
            assert owner.poll() is None
        finally:
            owner.communicate(b"\n", timeout=5)
