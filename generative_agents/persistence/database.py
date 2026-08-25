"""SQLite engine construction and Alembic migration entry points."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


@dataclass(slots=True)
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]

    def close(self) -> None:
        """执行 `Database` 的`close`操作。

        返回:
            无返回值。
        """
        self.engine.dispose()


def _ensure_sqlite_parent(database_url: str) -> None:
    """确保`sqlite``parent`。

    参数:
        database_url: 数据库连接地址；格式和驱动要求由持久化层定义。 类型：`str`。

    返回:
        无返回值。
    """
    url = make_url(database_url)
    if (
        url.get_backend_name() != "sqlite"
        or not url.database
        or url.database == ":memory:"
    ):
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
    """执行`configure``sqlite`的内部处理，供当前模块或类复用。

    参数:
        dbapi_connection: 传入当前算法的`dbapi``connection`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`Any`。
        _connection_record: 传入当前算法的`connection``record`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`Any`。

    返回:
        无返回值。
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA busy_timeout = 5000")
    finally:
        cursor.close()


def create_database(
    database_url: str,
    *,
    worker_process: bool = False,
    echo: bool = False,
) -> Database:
    """创建`database`。

    参数:
        database_url: 数据库连接地址；格式和驱动要求由持久化层定义。 类型：`str`。
        worker_process: 当前运行对应的操作系统工作进程对象。 类型：`bool`。 默认值：`False`。
        echo: 是否把 SQL 语句输出到诊断日志。 类型：`bool`。 默认值：`False`。

    返回:
        返回 `Database` 类型的处理结果。
    """

    _ensure_sqlite_parent(database_url)
    kwargs: dict[str, Any] = {"echo": echo, "future": True}
    if make_url(database_url).get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False}
        if worker_process:
            kwargs["poolclass"] = NullPool
    engine = create_engine(database_url, **kwargs)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _configure_sqlite)
    return Database(
        engine=engine,
        session_factory=sessionmaker(
            bind=engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        ),
    )


def upgrade_database(database_url: str, revision: str = "head") -> None:
    """执行 的`upgrade``database`操作。

    参数:
        database_url: 数据库连接地址；格式和驱动要求由持久化层定义。 类型：`str`。
        revision: 当前读取、发布、克隆或校验的修订版本记录。 类型：`str`。 默认值：`'head'`。

    返回:
        无返回值。

    异常:
        RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
    """

    try:
        from alembic import command
        from alembic.config import Config
    except ImportError as exc:  # pragma: no cover - clearer production startup failure
        raise RuntimeError(
            "Alembic is required to initialize the experiment database"
        ) from exc

    package_root = Path(__file__).resolve().parent
    config = Config()
    config.set_main_option("script_location", str(package_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, revision)
