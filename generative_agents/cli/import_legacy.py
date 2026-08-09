"""Import legacy catalog or historical run directories without modifying them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from generative_agents.persistence import create_database, upgrade_database
from generative_agents.services.legacy_import import LegacyImportService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("bootstrap-catalog", "runs"), help="import scope"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="audit without writes")
    mode.add_argument("--apply", action="store_true", help="persist idempotently")
    parser.add_argument("--database-url", default="sqlite:///var/generative-agents.db")
    parser.add_argument("--var-dir", default="var")
    parser.add_argument("--project-root", default=str(Path.cwd()))
    parser.add_argument("--source-root", help="legacy results root; defaults to package results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply:
        upgrade_database(args.database_url)
    database = create_database(args.database_url)
    try:
        importer = LegacyImportService(
            database, project_root=args.project_root, var_dir=args.var_dir
        )
        if args.command == "bootstrap-catalog":
            report = importer.bootstrap_catalog(apply=args.apply)
        else:
            report = importer.import_runs(
                apply=args.apply, source_root=args.source_root
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("failed") else 0
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
