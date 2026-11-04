from pathlib import Path
import subprocess
import sys

import pytest

from generative_agents.adapters.service_config import decision_compiler_config, load_services


@pytest.fixture
def config(tmp_path, monkeypatch):
    for name in ("GA_HOST", "GA_PORT", "GA_SIMWORLD_PORT", "GA_SIMWORLD_URL"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "端口 配置.toml"
    monkeypatch.setenv("GA_SERVICES_CONFIG", str(path))
    return path


def test_host_precedence_and_health_address(config, monkeypatch):
    config.write_text('[studio]\nhost = "::"\nport = 41301\n[simworld]\nurl = "http://localhost:41302/"',
                      encoding="utf-8-sig")
    host = load_services()
    assert host.studio_url == "http://[::1]:41301"
    assert host.local_scene_port() == 41302
    monkeypatch.setenv("GA_SIMWORLD_PORT", "41303")
    monkeypatch.setenv("GA_PORT", "41304")
    assert load_services().simworld_url == "http://localhost:41303"
    assert load_services().studio_port == 41304
    assert load_services(studio_host="0.0.0.0", studio_port=41305).studio_url == "http://127.0.0.1:41305"
    monkeypatch.setenv("GA_SIMWORLD_URL", "https://gpu.example/proxy/")
    host = load_services()
    assert host.simworld_url == "https://gpu.example/proxy"
    with pytest.raises(ValueError, match="远程"):
        host.local_scene_port()


def test_remote_setting_is_not_replaced_by_a_local_port(config, monkeypatch):
    config.write_text('[simworld]\nurl = "https://gpu.example:443/scene/"', encoding="utf-8")
    monkeypatch.setenv("GA_SIMWORLD_PORT", "41303")
    assert load_services().simworld_url == "https://gpu.example:443/scene"


def test_compiler_endpoint_override_cannot_inherit_another_services_key(config):
    config.write_text('[decision_compiler]\nbase_url="https://other.example/api/plan/v3/"\nmodel="other-llm"',
                      encoding="utf-8")
    selected = decision_compiler_config()
    assert selected["base_url"] == "https://other.example/api/plan/v3"
    assert selected["model"] == "other-llm"
    assert "credential_env" not in selected
    assert load_services().decision_compiler == selected


def test_compiler_config_carries_a_name_but_never_the_key(config, monkeypatch):
    config.write_text('[decision_compiler]\ncredential_env="TEST_COMPILER_KEY"\ntimeout_seconds=17\n'
                      'temperature=0.3\nmax_tokens=750\nenable_thinking=true', encoding="utf-8")
    monkeypatch.setenv("TEST_COMPILER_KEY", "secret-only-at-call-time")
    selected = decision_compiler_config()
    assert selected["credential_env"] == "TEST_COMPILER_KEY" and "secret-only-at-call-time" not in repr(selected)
    assert (selected["timeout_seconds"], selected["temperature"], selected["max_tokens"],
            selected["enable_thinking"]) == (17, 0.3, 750, True)


@pytest.mark.parametrize("invalid", ['api_key="literal-secret"', 'model="auto"', 'temperature=nan',
                                     'credential_env="not-an-env-name"', 'timeout_seconds=0', 'max_tokens=true',
                                     'enable_thinking="false"', 'base_url="https://user:literal-secret@host/path"'])
def test_invalid_compiler_settings_report_the_field_without_revealing_secrets(config, invalid):
    config.write_text("[decision_compiler]\n" + invalid, encoding="utf-8")
    with pytest.raises(ValueError) as info:
        decision_compiler_config()
    assert invalid.split("=")[0] in str(info.value)
    assert "literal-secret" not in str(info.value)


def test_duplicate_bind_detected_before_launch(config):
    config.write_text('[studio]\nhost="0.0.0.0"\nport=41322\n[simworld]\nurl="http://localhost:41322"',
                      encoding="utf-8")
    with pytest.raises(ValueError, match="同一端口"):
        load_services().check_local_ports()
    load_services(studio_port=41323).check_local_ports()


@pytest.mark.parametrize("bad", ['[studio]\nport=true', '[simworld]\nurl="http://localhost:65536"',
                                 '[models]\nchat="http://user:password@example.org"',
                                 '[studio]\nhost="a&cmd"', '[simworld]\nport=41111'])
def test_unusable_host_config_is_rejected(config, bad):
    config.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError):
        load_services()


def test_unrelated_cli_commands_do_not_load_deployment_settings(config):
    # Bad host settings cannot prevent offline access to packages.
    config.write_text("[broken", encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", "-m", "generative_agents.adapters.cli.main", "replay", "--help"],
                            cwd=Path(config).parent, capture_output=True)
    assert result.returncode == 0
