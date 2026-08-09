"""Production-style single-worker entry point for the local experiment console."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .app import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GenerativeAgentsCN experiment Web service")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("GA_DATABASE_URL", "sqlite:///var/generative-agents.db"),
    )
    parser.add_argument("--var-dir", default=os.environ.get("GA_VAR_DIR"))
    parser.add_argument("--host", default=os.environ.get("GA_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("GA_PORT", "8000")))
    parser.add_argument(
        "--max-concurrent-runs",
        type=int,
        default=int(os.environ.get("GA_MAX_CONCURRENT_RUNS", "2")),
    )
    parser.add_argument("--log-level", default=os.environ.get("GA_WEB_LOG_LEVEL", "info"))
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_concurrent_runs < 1:
        raise SystemExit("--max-concurrent-runs must be positive")
    if args.var_dir:
        Path(args.var_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)
    app = create_app(
        database_url=args.database_url,
        var_dir=args.var_dir,
        max_concurrent_runs=args.max_concurrent_runs,
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        workers=1,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
