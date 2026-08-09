"""Process entry point for one persistent artifact job."""

from __future__ import annotations

import argparse
import logging

from generative_agents.persistence import create_database

from .artifact_builder import ArtifactBuilder


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="build one experiment artifact")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--var-dir", required=True)
    parser.add_argument("--job-id", required=True)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    database = create_database(args.database_url)
    builder = ArtifactBuilder(database, var_dir=args.var_dir)
    try:
        builder.build(args.job_id)
        return 0
    except Exception as exc:
        logging.exception("artifact job failed")
        builder.fail(args.job_id, exc)
        return 1
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
