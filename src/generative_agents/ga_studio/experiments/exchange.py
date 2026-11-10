"""Exchange complete experiments and recorded Runs without author-resource lookup."""
from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, replace
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from filelock import FileLock, Timeout

from generative_agents.ga_protocol.packages.io import (
    PackageError, atomic_output, checked_package_path, extract_archive, open_package,
    read_json, seal_directory, sha256_file, verify_integrity,
)
from generative_agents.ga_protocol.packages.locking import package_lock
from generative_agents.ga_protocol.packages.reading import read_package_json
from generative_agents.ga_protocol.packages.run_snapshot import iter_run_snapshot_files
from generative_agents.ga_protocol.packages.validation import (
    validate_experiment_directory, validate_experiment_integrity, validate_run_archive,
)
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_studio.catalog.packages import PackageCatalogRecord, StudioPackageCatalogService
from generative_agents.ga_studio.storage.models import StudioPackageCatalog


class ExperimentExchangeConflict(PackageError):
    """An imported identity already exists with different authoritative content."""


@dataclass
class _ExtractionBudget:
    files: int = 100_000
    bytes: int = 20 * 1024 * 1024 * 1024

    def extract(self, source: Path, destination: Path) -> Path:
        # Charge the envelope and all nested archives to one request budget.
        with ZipFile(source) as archive:
            members = archive.infolist()
            count, size = len(members), sum(item.file_size for item in members)
        extract_archive(source, destination, max_files=self.files, max_uncompressed_bytes=self.bytes)
        self.files -= count
        self.bytes -= size
        return destination


_RUN_MEMBER = re.compile(r"runs/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.garun\Z")
_ACTIVE = {"QUEUED", "RUNNING", "FINALIZING"}


class ExperimentExchangeService:
    def __init__(self, database, *, package_root: str | Path):
        self.database = database
        self.package_root = checked_package_path(Path(package_root))
        self.catalog = StudioPackageCatalogService(database)

    def export_experiment(self, experiment_id: str, destination: str | Path) -> Path:
        """Snapshot saved content without changing the experiment lifecycle."""
        with package_lock(self.package_root / "experiments" / f"{experiment_id}.identity"):
            return self._export_experiment(experiment_id, destination)

    def _export_experiment(self, experiment_id, destination):
        destination = checked_package_path(Path(destination))
        row = self.catalog.get("experiment", experiment_id)
        if row is None:
            raise PackageError("实验不存在")
        location = checked_package_path(Path(row.location))
        with package_lock(location), open_package(location) as root:
            # A saved draft must be runnable before becoming an exchange archive.
            # Sealed evidence keeps its original bytes, including historical
            # resource fields which today's execution contracts no longer accept.
            manifest = (validate_experiment_directory(root) if location.is_dir()
                        else validate_experiment_integrity(root))
            if manifest.experiment.experiment_id != experiment_id:
                raise PackageError("实验身份与目录索引不一致")
            if destination.exists() or (location.is_dir() and destination.is_relative_to(location)):
                raise PackageError("实验导出目标必须是包目录外的新文件")
            if location.is_dir():
                return seal_directory(root, destination)
            self._copy_archive(location, destination)
        return destination

    def import_package(self, source: str | Path) -> dict:
        """Validate every package before installing archives and their catalog."""
        source = checked_package_path(Path(source))
        with tempfile.TemporaryDirectory(prefix="ga-experiment-import-") as temporary:
            staging = Path(temporary)
            budget = _ExtractionBudget()
            unpacked = budget.extract(source, staging / "input")
            runs: list[tuple[Path, Path]] = []
            if (unpacked / "run.json").is_file():
                run_manifest = self._validate_run(unpacked)
                experiment_root = unpacked / run_manifest.experiment.path
                experiment_archive = seal_directory(experiment_root, staging / "experiment.gaexp")
                runs.append((source, unpacked))
            elif (unpacked / "manifest.json").is_file():
                experiment_root, experiment_archive = unpacked, source
            else:
                members = {path.relative_to(unpacked).as_posix() for path in unpacked.rglob("*") if path.is_file()}
                required = {"experiment.gaexp", "integrity/sha256.json"}
                if not required.issubset(members) or any(
                    item not in required and not _RUN_MEMBER.fullmatch(item) for item in members
                ):
                    raise PackageError("实验资源包必须包含 experiment.gaexp、完整性清单及可选的 runs/<Run ID>.garun")
                if any(path.is_dir() and path.relative_to(unpacked).as_posix() not in {"runs", "integrity"} for path in unpacked.rglob("*")):
                    raise PackageError("实验资源包包含未知目录")
                verify_integrity(unpacked)
                experiment_archive = unpacked / "experiment.gaexp"
                experiment_root = budget.extract(experiment_archive, staging / "experiment")
                for index, member in enumerate(sorted(members - required)):
                    archive = unpacked / member
                    run_root = budget.extract(archive, staging / f"run-{index}")
                    manifest = self._validate_run(run_root)
                    if manifest.run_id != _RUN_MEMBER.fullmatch(member).group(1):
                        raise PackageError("Run 文件名与包内身份不一致")
                    runs.append((archive, run_root))
            experiment = validate_experiment_integrity(experiment_root)
            experiment_id = experiment.experiment.experiment_id
            digest = verify_integrity(experiment_root)["root_sha256"]
            records = [(experiment_archive, PackageCatalogRecord(
                "experiment", experiment_id, experiment_id, None, "", experiment.experiment.name,
                "SEALED", digest, True,
            ))]
            run_ids = []
            for archive, root in runs:
                manifest = self._validate_run(root)
                if manifest.experiment.experiment_id != experiment_id or manifest.experiment.root_sha256 != digest:
                    raise PackageError("Run 内嵌实验与实验资源包内容不一致")
                if manifest.run_id in run_ids:
                    raise PackageError("实验资源包包含重复 Run 身份")
                run_ids.append(manifest.run_id)
                status = RunStatus.model_validate(read_json(root / "status.json"))
                records.append((archive, PackageCatalogRecord(
                    "run", manifest.run_id, experiment_id, manifest.run_id, "", experiment.experiment.name,
                    status.status.value, verify_integrity(root)["root_sha256"], True,
                )))
            with ExitStack() as locks:
                locks.enter_context(package_lock(self.package_root / "experiment-import.identity"))
                locks.enter_context(package_lock(self.package_root / "experiments" / f"{experiment_id}.identity"))
                for run_id in sorted(run_ids):
                    locks.enter_context(package_lock(self.package_root / "runs" / f"{run_id}.resume-submit.identity"))
                imported, existing = self._install(records)
            return {"experiment_id": experiment_id, "run_ids": run_ids,
                    "imported_count": imported, "existing_count": existing}

    @staticmethod
    def _validate_run(root):
        manifest = validate_run_archive(root, sealed=True)
        status = RunStatus.model_validate(read_json(root / "status.json"))
        if status.status.value in _ACTIVE:
            raise PackageError("运行中的 Run 不能导入；请在来源系统暂停或完成运行后重新导出")
        return manifest

    @staticmethod
    def _copy_archive(source, target):
        with source.open("rb") as reader, atomic_output(target) as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)

    def _install(self, incoming):
        identities = {(record.package_kind, record.package_id) for _, record in incoming}
        existing = {}
        for kind, identity in identities:
            row = self.catalog.get(kind, identity)
            if row is not None:
                existing[(kind, identity)] = checked_package_path(Path(row.location))
        # A missing disposable index must not permit duplicate identities on disk.
        for location in self.catalog._discover([self.package_root]):
            if location in existing.values():
                continue
            kind = "run" if location.suffix.casefold() == ".garun" or (location.is_dir() and (location / "run.json").exists()) else "experiment"
            try:
                header = read_package_json(location, "run.json" if kind == "run" else "manifest.json")
                identity = header.get("run_id") if kind == "run" else header.get("experiment", {}).get("experiment_id")
            except (ValueError, OSError, AttributeError, TypeError, BadZipFile):
                continue
            key = kind, identity
            if key not in identities:
                continue
            if key in existing and existing[key] != location:
                raise ExperimentExchangeConflict(f"本机存在重复包身份：{identity}，请先整理包目录")
            existing[key] = checked_package_path(location)
        planned, skipped, catalog_records = [], 0, []
        for archive, record in incoming:
            key = record.package_kind, record.package_id
            if key in existing:
                location = existing[key]
                with package_lock(location):
                    current = self.catalog.inspect(location)
                    if record.package_kind == "run" and location.is_dir():
                        identical = self._same_run_snapshot(location, archive)
                    else:
                        identical = current.content_sha256 == record.content_sha256
                if not identical:
                    raise ExperimentExchangeConflict(f"本机已有同一身份但内容不同的{record.package_kind}：{record.package_id}；未覆盖任何内容")
                catalog_records.append(current)
                skipped += 1
                continue
            folder, suffix = ("experiments", ".gaexp") if record.package_kind == "experiment" else ("runs", ".garun")
            destination = checked_package_path(self.package_root / folder / f"imported-{record.package_id}{suffix}")
            if destination.exists():
                raise ExperimentExchangeConflict(f"导入目标文件已存在：{destination.name}")
            planned.append((archive, destination))
            catalog_records.append(replace(record, location=str(destination)))
        created = []
        try:
            with self.database.session_factory.begin() as session:
                for source, destination in planned:
                    self._copy_archive(source, destination)
                    created.append(destination)
                for record in catalog_records:
                    row = session.get(StudioPackageCatalog, {"package_kind": record.package_kind, "package_id": record.package_id})
                    if row is not None:
                        if Path(row.location) != Path(record.location):
                            raise ExperimentExchangeConflict("导入期间包索引发生变化，请重试")
                        continue
                    session.add(StudioPackageCatalog(**asdict(record), discovered_at=datetime.now(UTC), updated_at=datetime.now(UTC)))
        except Exception:
            for destination in created:
                destination.unlink(missing_ok=True)
            raise
        return len(planned), skipped

    @staticmethod
    def _same_run_snapshot(location, archive):
        try:
            with FileLock(str(location / "worker.lock"), timeout=0):
                validate_run_archive(location)
                status = RunStatus.model_validate(read_json(location / "status.json"))
                if status.status.value in _ACTIVE:
                    raise ExperimentExchangeConflict("同一身份的本机 Run 正在运行，不能导入")
                current_files = {relative: sha256_file(path) for relative, path in
                                 iter_run_snapshot_files(location, committed_step=status.committed_step)}
                return current_files == read_package_json(archive, "integrity/sha256.json")["files"]
        except Timeout as exc:
            raise ExperimentExchangeConflict("同一身份的本机 Run 正在使用，不能导入") from exc
