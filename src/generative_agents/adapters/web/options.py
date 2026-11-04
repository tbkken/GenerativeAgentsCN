"""Server flags, importable without Web or Studio dependencies."""
import os

def add_server_arguments(parser):
    parser.add_argument(
        "--database-url",
        default=os.environ.get("GA_DATABASE_URL", "sqlite:///var/generative-agents.db"),
    )
    parser.add_argument("--var-dir", default=os.environ.get("GA_VAR_DIR"))
    parser.add_argument("--host", help="Override the configured Studio listen host")
    parser.add_argument("--port", type=int, help="Override the configured Studio listen port")
    parser.add_argument(
        "--max-concurrent-runs",
        type=int,
        default=os.environ.get("GA_MAX_CONCURRENT_RUNS", "2"),
    )
    parser.add_argument(
        "--log-level", default=os.environ.get("GA_WEB_LOG_LEVEL", "info")
    )
    return parser
