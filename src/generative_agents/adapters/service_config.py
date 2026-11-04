"""Host connections for Studio and the local launchers. No Studio/SDK dependencies."""
from __future__ import annotations

import ipaddress
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import distribution
from pathlib import Path
from urllib.parse import urlsplit


_KEYS = {"studio": {"host", "port"}, "simworld": {"url"}, "models": {"chat", "embedding", "decision"},
         "decision_compiler": {"base_url", "model", "credential_env", "timeout_seconds", "max_tokens",
                               "temperature", "enable_thinking"}}


@lru_cache(maxsize=1)
def _defaults_path() -> Path:
    base = Path(__file__).resolve().parents[2]
    if base.name == "src" and (base.parent / "pyproject.toml").is_file():
        return base.parent / "services.toml"
    # pip --target moves the wheel's share directory beside the package.
    target_file = base / "share" / "generative-agents-cn" / "services.toml"
    if target_file.is_file():
        return target_file
    # A wheel installs the same project file under its host's data prefix.
    dist = distribution("generative-agents-cn")
    for item in dist.files or ():
        if item.parts[-3:] == ("share", "generative-agents-cn", "services.toml"):
            return Path(dist.locate_file(item)).resolve()
    raise ValueError("安装包缺少 services.toml，请重新安装项目。")


@lru_cache(maxsize=16)
def _read(path: Path) -> dict:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取配置 {path}：{exc}") from exc
    for name, section in data.items():
        if name not in _KEYS or not isinstance(section, dict):
            raise ValueError(f"{path}：无法识别配置节 [{name}]")
        unknown = section.keys() - _KEYS[name]
        if unknown:
            raise ValueError(f"{path}：[{name}] 中存在未知配置项 {', '.join(sorted(unknown))}")
    return data


def _port(value, key) -> int:
    if type(value) not in (int, str) or not re.fullmatch(r"[0-9]{1,5}", str(value)) or not 1 <= int(value) <= 65535:
        raise ValueError(f"{key} 需要 1 到 65535 之间的端口号")
    return int(value)


def _host(value) -> str:
    # The restart launcher consumes a whitespace-delimited, shell-safe bind host.
    if isinstance(value, str) and value.isascii() and "%" not in value:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            if len(value) <= 253 and all(re.fullmatch(r"[a-zA-Z0-9](?:[-a-zA-Z0-9]{0,61}[a-zA-Z0-9])?", label)
                                         for label in value.rstrip(".").split(".")):
                return value.lower()
    raise ValueError("studio.host 需要 IP 地址或主机名")


def _url(value, key: str) -> str:
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError(f"{key} 需要完整的 HTTP(S) 地址")
    try:
        url = urlsplit(value)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None
                or "?" in value or "#" in value or url.netloc.endswith(":") or url.port == 0):
            raise ValueError()
        _host(url.hostname)
    except ValueError:
        raise ValueError(f"{key} 需要有效的 HTTP(S) 地址，不含凭据、查询参数或片段") from None
    return value.rstrip("/")


def _local_scene(url: str) -> bool:
    target = urlsplit(url)
    return target.scheme == "http" and target.hostname in {"localhost", "127.0.0.1"} and not target.path


@dataclass(frozen=True)
class HostServices:
    studio_host: str
    studio_port: int
    simworld_url: str
    models: dict[str, str]
    decision_compiler: dict

    @property
    def studio_url(self) -> str:
        host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(self.studio_host, self.studio_host)
        return f"http://{'[' + host + ']' if ':' in host else host}:{self.studio_port}"

    def local_scene_port(self) -> int:
        if not _local_scene(self.simworld_url):
            raise ValueError("SimWorld 配置指向远程服务、代理或非本机监听地址；请直接从 Studio 连接。"
                             "启动本机 UE 请将 simworld.url 设为 http://127.0.0.1:<端口>。")
        return urlsplit(self.simworld_url).port or 80

    def check_local_ports(self):
        if (_local_scene(self.simworld_url) and self.studio_host in {"127.0.0.1", "localhost", "0.0.0.0", "::"}
                and self.studio_port == self.local_scene_port()):
            raise ValueError("Studio 与本机 SimWorld 使用了同一端口，请修改 services.toml 或临时覆盖配置。")


def _settings():
    default = _defaults_path()
    data = _read(default)
    configured = os.environ.get("GA_SERVICES_CONFIG")
    path = Path(configured).expanduser().resolve() if configured else default
    overlay = _read(path) if path != default else {}
    settings = {key: {**data.get(key, {}), **overlay.get(key, {})} for key in _KEYS}
    # An override pointing to another service must never inherit the first service's secret.
    compiler = overlay.get("decision_compiler", {})
    if ("base_url" in compiler and compiler["base_url"] != data.get("decision_compiler", {}).get("base_url")
            and "credential_env" not in compiler):
        settings["decision_compiler"].pop("credential_env", None)
    for key, fields in _KEYS.items():
        if key == "decision_compiler":
            fields = fields - {"credential_env"}
        if missing := fields - settings[key].keys():
            raise ValueError(f"{path}：[{key}] 缺少 {', '.join(sorted(missing))}")
    return settings


def scene_url() -> str:
    remote = os.environ.get("GA_SIMWORLD_URL")
    scene = _url(remote if remote is not None else _settings()["simworld"]["url"], "simworld.url")
    if remote is None and _local_scene(scene) and "GA_SIMWORLD_PORT" in os.environ:
        scene = f"http://{urlsplit(scene).hostname}:{_port(os.environ['GA_SIMWORLD_PORT'], 'GA_SIMWORLD_PORT')}"
    return scene


def model_suggestions() -> dict[str, str]:
    return {k: _url(v, f"models.{k}") for k, v in _settings()["models"].items()}


def decision_compiler_config() -> dict:
    """Authoring-only OpenAI-compatible chat connection; never read secret values here."""
    item = _settings()["decision_compiler"]
    model = item["model"]
    if (not isinstance(model, str) or not 1 <= len(model.strip()) <= 200 or model.strip().casefold() == "auto"
            or not model.isprintable()):
        raise ValueError("decision_compiler.model 需要明确的生成式模型 ID")
    options = {"provider": "vllm", "model": model.strip(), "base_url": _url(item["base_url"], "decision_compiler.base_url")}
    for key, maximum in (("max_tokens", 131072), ("timeout_seconds", 600)):
        value = item[key]
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"decision_compiler.{key} 需要 1 到 {maximum} 之间的整数")
        options[key] = value
    temp, thinking = item["temperature"], item["enable_thinking"]
    if type(temp) not in (float, int) or not 0 <= temp <= 2:
        raise ValueError("decision_compiler.temperature 需要 0 到 2 之间的数值")
    if type(thinking) is not bool:
        raise ValueError("decision_compiler.enable_thinking 需要 true 或 false")
    alias = item.get("credential_env", "")
    if not isinstance(alias, str) or (alias and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias)):
        raise ValueError("decision_compiler.credential_env 只填凭据环境变量名称，无需认证时留空")
    if alias:
        options["credential_env"] = alias
    return {**options, "temperature": temp, "enable_thinking": thinking}


def load_services(*, studio_host=None, studio_port=None) -> HostServices:
    """Resolve a launch; CLI flags take precedence over environment and file settings."""
    studio = _settings()["studio"]
    host = _host(studio_host if studio_host is not None else os.environ.get("GA_HOST", studio["host"]))
    port = _port(studio_port if studio_port is not None else os.environ.get("GA_PORT", studio["port"]), "studio.port")
    return HostServices(host, port, scene_url(), model_suggestions(), decision_compiler_config())


def main() -> int:
    if sys.argv[1:] not in (["web"], ["simworld"]):
        print("Usage: service_config.py web|simworld", file=sys.stderr)
        return 2
    try:
        settings = load_services()
        settings.check_local_ports()
        if sys.argv[1] == "web":
            print(settings.studio_host, settings.studio_port, settings.studio_url)
        else:
            print(settings.local_scene_port())
        return 0
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
