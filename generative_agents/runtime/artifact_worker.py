"""Process entry point for one persistent artifact job."""

from __future__ import annotations

import argparse
import logging

from generative_agents.persistence import create_database

from .artifact_builder import ArtifactBuilder


def _parser() -> argparse.ArgumentParser:
    """执行`parser`的内部处理，供当前模块或类复用。

    返回:
        返回 `argparse.ArgumentParser` 类型的处理结果。
    """
    parser = argparse.ArgumentParser(description="build one experiment artifact")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--var-dir", required=True)
    parser.add_argument("--job-id", required=True)
    return parser


def main(argv=None) -> int:
    """解析启动参数并执行当前模块的主流程。

    参数:
        argv: 命令行参数序列；为 `None` 时读取当前进程的命令行。 默认值：`None`。

    返回:
        返回计算得到的整数值或版本号。
    """
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
