"""Production-style single-worker entry point for the local experiment console."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from generative_agents.adapters.service_config import load_services
from generative_agents.adapters.web.app import create_studio_app


def build_parser() -> argparse.ArgumentParser:
    """构建`parser`。

    返回:
        返回 `argparse.ArgumentParser` 类型的处理结果。
    """
    parser = argparse.ArgumentParser(
        description="GenerativeAgentsCN experiment Web service"
    )
    from generative_agents.adapters.web.options import add_server_arguments
    add_server_arguments(parser)
    return parser


def main(argv=None) -> int:
    """解析启动参数并执行当前模块的主流程。

    参数:
        argv: 命令行参数序列；为 `None` 时读取当前进程的命令行。 默认值：`None`。

    返回:
        返回计算得到的整数值或版本号。

    异常:
        SystemExit: 当底层操作报告该异常条件时抛出。
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return serve(args)
    except ValueError as exc:
        parser.error(str(exc))


def serve(args) -> int:
    """Run the sole Web application for both CLI entry points."""
    services = load_services(studio_host=args.host, studio_port=args.port)
    services.check_local_ports()
    if args.max_concurrent_runs < 1:
        raise SystemExit("--max-concurrent-runs must be positive")
    if args.var_dir:
        Path(args.var_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)
    app = create_studio_app(
        database_url=args.database_url,
        var_dir=args.var_dir or "var",
        max_concurrent_runs=args.max_concurrent_runs,
    )
    uvicorn.run(
        app,
        host=services.studio_host,
        port=services.studio_port,
        workers=1,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
