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
        self.engine.dispose()


def _ensure_sqlite_parent(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
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
    """Create an independent engine; workers use NullPool by design."""

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
    """Upgrade using the checked-in Alembic history, never metadata.create_all."""

    try:
        from alembic import command
        from alembic.config import Config
    except ImportError as exc:  # pragma: no cover - clearer production startup failure
        raise RuntimeError("Alembic is required to initialize the experiment database") from exc

    package_root = Path(__file__).resolve().parent
    config = Config()
    config.set_main_option("script_location", str(package_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, revision)
