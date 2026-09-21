"""Studio-only database index rebuilt from authoritative package manifests."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from contextlib import nullcontext
from generative_agents.ga_protocol.packages.locking import package_lock
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import delete, select

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.validation import validate_experiment_integrity
from generative_agents.ga_protocol.packages.validation import validate_run_integrity
from generative_agents.ga_protocol.packages.io import verify_integrity
from generative_agents.ga_studio.storage.models import StudioPackageCatalog


@dataclass(frozen=True, slots=True)
class PackageCatalogRecord:
    package_kind: str
    package_id: str
    experiment_id: str
    run_id: str | None
    location: str
    display_name: str
    package_status: str | None
    content_sha256: str | None
    is_archive: bool


class StudioPackageCatalogService:
    """Maintain a disposable Web navigation index, never Runtime truth."""

    def __init__(self, database) -> None:
        self.database = database

    def inspect(self, location: str | Path) -> PackageCatalogRecord:
        location = Path(location).resolve()
        guard = package_lock(location) if location.is_dir() and not (location / 'run.json').exists() else nullcontext()
        with guard, open_package(location) as root:
            if (root / "run.json").is_file():
                manifest = validate_run_integrity(root, sealed=location.is_file())
                status = RunStatus.model_validate(read_json(root / "status.json"))
                experiment_document = read_json(root / manifest.experiment.path / "manifest.json")
                identity = experiment_document.get("experiment") if isinstance(experiment_document, dict) else {}
                content_hash = (
                    str(verify_integrity(root)["root_sha256"])
                    if location.is_file()
                    else None
                )
                return PackageCatalogRecord(
                    package_kind="run",
                    package_id=manifest.run_id,
                    experiment_id=manifest.experiment.experiment_id,
                    run_id=manifest.run_id,
                    location=str(location),
                    display_name=str((identity or {}).get("name") or manifest.run_id),
                    package_status=status.status.value,
                    content_sha256=content_hash,
                    is_archive=location.is_file(),
                )
            manifest = validate_experiment_integrity(root)
            integrity = verify_integrity(root)
            return PackageCatalogRecord(
                package_kind="experiment",
                package_id=manifest.experiment.experiment_id,
                experiment_id=manifest.experiment.experiment_id,
                run_id=None,
                location=str(location),
                display_name=manifest.experiment.name,
                package_status="SEALED" if location.is_file() else "DRAFT",
                content_sha256=str(integrity["root_sha256"]),
                is_archive=location.is_file(),
            )

    def upsert(self, location: str | Path) -> PackageCatalogRecord:
        record = self.inspect(location)
        return self._store_record(record)

    def record_validated_experiment(self, root, manifest, content_sha256):
        """Index an experiment just validated by the caller under its package lock."""
        return self._store_record(PackageCatalogRecord(
            package_kind='experiment', package_id=manifest.experiment.experiment_id,
            experiment_id=manifest.experiment.experiment_id, run_id=None,
            location=str(Path(root).resolve()), display_name=manifest.experiment.name,
            package_status='DRAFT', content_sha256=content_sha256, is_archive=False,
        ))

    def _store_record(self, record):
        with self.database.session_factory.begin() as session:
            row = session.get(
                StudioPackageCatalog,
                {"package_kind": record.package_kind, "package_id": record.package_id},
            )
            if row is None:
                row = StudioPackageCatalog(
                    package_kind=record.package_kind,
                    package_id=record.package_id,
                    discovered_at=datetime.now(UTC),
                )
                session.add(row)
            for name, value in asdict(record).items():
                setattr(row, name, value)
            row.updated_at = datetime.now(UTC)
        return record

    def rebuild(self, roots: Iterable[str | Path]) -> list[PackageCatalogRecord]:
        locations = list(self._discover(roots))
        records = [self.inspect(location) for location in locations]
        identities = [(record.package_kind, record.package_id) for record in records]
        if len(identities) != len(set(identities)):
            duplicates = sorted(identity for identity in set(identities) if identities.count(identity) > 1)
            raise PackageError(f"duplicate package identities in catalog scan: {duplicates}")
        with self.database.session_factory.begin() as session:
            session.execute(delete(StudioPackageCatalog))
            for record in records:
                session.add(
                    StudioPackageCatalog(
                        **asdict(record),
                        discovered_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )
        return records

    def list(self, *, package_kind: str | None = None) -> list[StudioPackageCatalog]:
        with self.database.session_factory() as session:
            statement = select(StudioPackageCatalog)
            if package_kind:
                statement = statement.where(StudioPackageCatalog.package_kind == package_kind)
            return list(session.scalars(statement.order_by(StudioPackageCatalog.updated_at.desc())))

    def get(self, package_kind: str, package_id: str) -> StudioPackageCatalog | None:
        with self.database.session_factory() as session:
            return session.get(
                StudioPackageCatalog,
                {"package_kind": package_kind, "package_id": package_id},
            )

    def delete(self, package_kind: str, package_id: str) -> bool:
        """Remove only the disposable navigation record for one package."""

        with self.database.session_factory.begin() as session:
            row = session.get(
                StudioPackageCatalog,
                {"package_kind": package_kind, "package_id": package_id},
            )
            if row is None:
                return False
            session.delete(row)
            return True

    @staticmethod
    def _discover(roots: Iterable[str | Path]):
        for raw_root in roots:
            root = Path(raw_root).resolve()
            if root.is_file():
                if root.suffix.casefold() in {".gaexp", ".garun"}:
                    yield root
                continue
            for current, directories, files in os.walk(root):
                current_path = Path(current)
                if "run.json" in files or "manifest.json" in files:
                    yield current_path
                    directories[:] = []
                    continue
                for name in sorted(files):
                    path = current_path / name
                    if path.suffix.casefold() in {".gaexp", ".garun"}:
                        yield path
