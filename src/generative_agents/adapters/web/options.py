"""Server flags, importable without Web or Studio dependencies."""
import os

def add_server_arguments(parser):
    parser.add_argument(
        "--database-url",
        default=os.environ.get("GA_DATABASE_URL", "sqlite:///var/generative-agents.db"),
    )
    parser.add_argument("--var-dir", default=os.environ.get("GA_VAR_DIR"))
    parser.add_argument("--host", default=os.environ.get("GA_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("GA_PORT", "8000"))
    )
    parser.add_argument(
        "--max-concurrent-runs",
        type=int,
        default=int(os.environ.get("GA_MAX_CONCURRENT_RUNS", "2")),
    )
    parser.add_argument(
        "--log-level", default=os.environ.get("GA_WEB_LOG_LEVEL", "info")
    )
    return parser
