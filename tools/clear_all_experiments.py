"""Remove every experiment and run owned by this workspace.

This maintenance command intentionally preserves shared maps, brains, secrets,
and schema history. It refuses to operate outside the repository-local var
directory and validates SQLite foreign keys before committing.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VAR_DIR = (ROOT / "var").resolve()
DATABASE = (VAR_DIR / "generative-agents.db").resolve()
RUNS_DIR = (VAR_DIR / "runs").resolve()


def _assert_safe_targets() -> None:
    """确认数据库和 Run 目录都位于预期 var 目录后才允许清理。"""

    if DATABASE.parent != VAR_DIR or DATABASE.name != "generative-agents.db":
        raise RuntimeError(f"unsafe database target: {DATABASE}")
    if not DATABASE.is_file():
        raise RuntimeError(f"database does not exist: {DATABASE}")
    if RUNS_DIR.parent != VAR_DIR or RUNS_DIR.name != "runs":
        raise RuntimeError(f"unsafe runs target: {RUNS_DIR}")


def _clear_database() -> dict[str, int]:
    """按外键依赖顺序清空实验相关表，并返回各表原记录数。"""

    connection = sqlite3.connect(DATABASE)
    connection.execute("PRAGMA foreign_keys=ON")
    before = {
        table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in ("experiments", "experiment_revisions", "runs")
    }
    try:
        connection.execute("BEGIN IMMEDIATE")
        # Published experiment snapshots are normally immutable. Full workspace
        # reset is the one maintenance operation that may remove them, so keep
        # the exact trigger definitions and restore them before commit.
        delete_trigger_names = (
            "trg_published_revision_no_delete",
            "trg_published_workflow_no_delete",
            "trg_workflow_version_no_delete",
        )
        trigger_sql = {
            name: connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                (name,),
            ).fetchone()
            for name in delete_trigger_names
        }
        if any(row is None or not row[0] for row in trigger_sql.values()):
            raise RuntimeError("expected experiment immutability triggers are missing")
        for name in delete_trigger_names:
            connection.execute(f'DROP TRIGGER "{name}"')
        connection.execute("PRAGMA defer_foreign_keys=ON")
        connection.execute("DELETE FROM runs")
        connection.execute("DELETE FROM experiment_workflow_versions")
        connection.execute("DELETE FROM experiment_workflows")
        connection.execute("DELETE FROM model_probe_statuses")
        connection.execute("DELETE FROM experiment_revisions")
        connection.execute("DELETE FROM experiments")
        for name in delete_trigger_names:
            connection.execute(trigger_sql[name][0])
        violations = list(connection.execute("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError(f"foreign-key violations: {violations[:5]}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return before


def _clear_run_directories() -> int:
    """删除 runs 目录下已确认安全的直接子目录并返回数量。"""

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    children = [child.resolve() for child in RUNS_DIR.iterdir()]
    for child in children:
        if child.parent != RUNS_DIR:
            raise RuntimeError(f"unsafe run child: {child}")
    for child in children:
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    return len(children)


def main() -> None:
    """执行安全检查、数据库清理和 Run 目录清理，并打印摘要。"""

    _assert_safe_targets()
    before = _clear_database()
    deleted_paths = _clear_run_directories()
    print(
        "Cleared experiments={experiments}, revisions={experiment_revisions}, "
        "runs={runs}, run_paths={run_paths}.".format(
            **before,
            run_paths=deleted_paths,
        )
    )


if __name__ == "__main__":
    main()
