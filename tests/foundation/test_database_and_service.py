"""The database contains author resources and a rebuildable catalog only."""
from sqlalchemy import inspect
from generative_agents.ga_studio.storage.schema import BASELINE_REVISION
from generative_agents.ga_studio.storage.schema import STUDIO_TABLE_NAMES
from generative_agents.ga_studio.storage.models import Base


def test_alembic_matches_studio_models_and_sqlite_pragmas(database):
    assert len(STUDIO_TABLE_NAMES) == 11
    assert set(Base.metadata.tables) == STUDIO_TABLE_NAMES
    with database.engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == STUDIO_TABLE_NAMES | {"alembic_version"}
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == BASELINE_REVISION
