"""Bootstrap the Studio-only database from the current clean baseline.

The package-first architecture deliberately has no migration path from the old
experiment/Run database.  For local SQLite deployments we preserve an
incompatible database as a timestamped backup and create a clean Studio author
database in its place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import shutil
import sqlite3

from sqlalchemy.engine import make_url

from generative_agents.ga_studio.storage.database import upgrade_database
from generative_agents.ga_studio.storage.models import Asset
from generative_agents.ga_studio.storage.models import Secret
from generative_agents.ga_studio.storage.models import SeedResourceTombstone
from generative_agents.ga_studio.storage.models import SpatialAssetDefinition
from generative_agents.ga_studio.storage.models import StudioAgent
from generative_agents.ga_studio.storage.models import StudioCrowd
from generative_agents.ga_studio.storage.models import StudioEvaluator
from generative_agents.ga_studio.storage.models import StudioModelPreset
from generative_agents.ga_studio.storage.models import StudioPackageCatalog
from generative_agents.ga_studio.storage.models import StudioSkill
from generative_agents.ga_studio.storage.models import WorldMap


LOGGER = logging.getLogger(__name__)
BASELINE_REVISION = "0001_mutable_resource_baseline"
STUDIO_TABLE_NAMES = frozenset(
    model.__tablename__
    for model in (
        SeedResourceTombstone,
        StudioSkill,
        StudioAgent,
        StudioCrowd,
        StudioModelPreset,
        StudioEvaluator,
        WorldMap,
        SpatialAssetDefinition,
        Secret,
        Asset,
        StudioPackageCatalog,
    )
)
_SQLITE_ALLOWED_TABLE_NAMES = STUDIO_TABLE_NAMES | {
    "alembic_version",
    "sqlite_sequence",
}


@dataclass(frozen=True, slots=True)
class StudioSchemaPreparation:
    """Result of preparing the Studio author database."""

    rebuilt: bool
    backup_path: Path | None = None


def _sqlite_database_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        return None
    if url.database == ":memory:":
        return None
    return Path(url.database).expanduser().resolve()


def _sqlite_schema(path: Path) -> tuple[set[str], set[str]]:
    connection = sqlite3.connect(path)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        versions: set[str] = set()
        if "alembic_version" in tables:
            versions = {
                str(row[0])
                for row in connection.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchall()
            }
        return tables, versions
    finally:
        connection.close()


def _is_current_sqlite_schema(tables: set[str], versions: set[str]) -> bool:
    return (
        STUDIO_TABLE_NAMES <= tables
        and not tables - _SQLITE_ALLOWED_TABLE_NAMES
        and versions == {BASELINE_REVISION}
    )


def _unique_backup_path(database_path: Path, backup_dir: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    candidate = backup_dir / f"{database_path.stem}.pre-package-studio-{timestamp}.sqlite"
    counter = 1
    while candidate.exists():
        candidate = backup_dir / (
            f"{database_path.stem}.pre-package-studio-{timestamp}-{counter}.sqlite"
        )
        counter += 1
    return candidate


def _backup_sqlite_database(database_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = _unique_backup_path(database_path, backup_dir)
    try:
        source = sqlite3.connect(database_path)
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
    except sqlite3.DatabaseError:
        # A corrupt database cannot be copied through SQLite's online-backup
        # API, but preserving its bytes is still better than discarding it.
        shutil.copy2(database_path, backup_path)
    return backup_path


def _remove_sqlite_database(database_path: Path) -> None:
    database_path.unlink(missing_ok=True)
    Path(f"{database_path}-wal").unlink(missing_ok=True)
    Path(f"{database_path}-shm").unlink(missing_ok=True)


def prepare_studio_database(
    database_url: str,
    *,
    backup_dir: str | Path,
) -> StudioSchemaPreparation:
    """Prepare a clean Studio schema and rebuild incompatible local SQLite.

    Non-SQLite databases are never replaced automatically. Alembic remains
    responsible for creating or upgrading those schemas.
    """

    database_path = _sqlite_database_path(database_url)
    backup_path: Path | None = None
    if database_path is not None and database_path.exists():
        try:
            tables, versions = _sqlite_schema(database_path)
        except sqlite3.DatabaseError:
            tables, versions = {"<unreadable>"}, set()
        if tables and not _is_current_sqlite_schema(tables, versions):
            backup_path = _backup_sqlite_database(
                database_path,
                Path(backup_dir).expanduser().resolve(),
            )
            _remove_sqlite_database(database_path)
            LOGGER.warning(
                "Rebuilt incompatible Studio database; previous bytes are preserved at %s",
                backup_path,
            )

    upgrade_database(database_url)

    if database_path is not None:
        tables, versions = _sqlite_schema(database_path)
        if not _is_current_sqlite_schema(tables, versions):
            raise RuntimeError(
                "Studio database initialization did not produce the required clean schema"
            )
    return StudioSchemaPreparation(
        rebuilt=backup_path is not None,
        backup_path=backup_path,
    )


__all__ = [
    "BASELINE_REVISION",
    "STUDIO_TABLE_NAMES",
    "StudioSchemaPreparation",
    "prepare_studio_database",
]
