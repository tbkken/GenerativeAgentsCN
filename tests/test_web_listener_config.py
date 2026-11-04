"""The CLI and direct Web entry point share the effective Studio listener."""

import pytest

from generative_agents.adapters.cli import main as cli
from generative_agents.adapters.web import main as web


@pytest.mark.parametrize("entry", ["cli", "module"])
@pytest.mark.parametrize("precedence", ["file", "environment", "arguments"])
def test_web_listens_on_resolved_address(tmp_path, monkeypatch, entry, precedence):
    config = tmp_path / "selected.toml"
    config.write_text('[studio]\nhost = "0.0.0.0"\nport = 18123\n', encoding="utf-8")
    monkeypatch.setenv("GA_SERVICES_CONFIG", str(config))
    for key in ("GA_HOST", "GA_PORT", "GA_SIMWORLD_URL", "GA_SIMWORLD_PORT", "GA_VAR_DIR"):
        monkeypatch.delenv(key, raising=False)
    flags = []
    expected = "0.0.0.0", 18123
    if precedence != "file":
        expected = "127.0.0.1", 18124
        monkeypatch.setenv("GA_HOST", expected[0])
        monkeypatch.setenv("GA_PORT", str(expected[1]))
    if precedence == "arguments":
        expected = "::1", 18125
        flags = ["--host", expected[0], "--port", str(expected[1])]
    actual = []
    monkeypatch.setattr(web, "create_studio_app", lambda **_: object())
    monkeypatch.setattr(web.uvicorn, "run", lambda app, **kw: actual.append((kw["host"], kw["port"])))
    result = cli.main(["studio", "serve", *flags]) if entry == "cli" else web.main(flags)
    assert result == 0
    assert actual == [expected]


@pytest.mark.parametrize("command,action", [("run", "status"), ("replay", "summary")])
def test_other_commands_ignore_broken_service_config(tmp_path, monkeypatch, command, action):
    monkeypatch.setenv("GA_SERVICES_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("GA_PORT", "invalid")
    monkeypatch.setattr(cli, "_" + command, lambda args: {})
    assert cli.main([command, action, "example.garun"]) == 0


def test_cli_rejects_local_port_collision_before_creating_web_app(tmp_path, monkeypatch, capsys):
    config = tmp_path / "collision.toml"
    config.write_text('[studio]\nhost = "127.0.0.1"\n[simworld]\nurl = "http://127.0.0.1:18542"\n', encoding="utf-8")
    monkeypatch.setenv("GA_SERVICES_CONFIG", str(config))
    for key in ("GA_HOST", "GA_SIMWORLD_URL", "GA_SIMWORLD_PORT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(web, "create_studio_app", lambda **_: pytest.fail("unexpected Web startup"))
    assert cli.main(["studio", "serve", "--port", "18542"]) == 2
    assert "SimWorld" in capsys.readouterr().err
