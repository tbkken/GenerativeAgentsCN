"""Portable experiment transfer, with Runtime-owned snapshots of its Runs."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

from fastapi import File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from filelock import Timeout
from starlette.background import BackgroundTask

from generative_agents.ga_protocol.packages.io import open_package, read_json, seal_directory, verify_integrity, write_integrity_manifest
from generative_agents.ga_protocol.packages.locking import package_lock
from generative_agents.ga_protocol.packages.validation import validate_run_archive
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_studio.api import ExperimentExchangeService, ExperimentExchangeConflict


MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
ACTIVE = {'QUEUED', 'RUNNING', 'FINALIZING'}


def install_routes(router, ctx):
    exchange = ExperimentExchangeService(ctx.database, package_root=ctx.package_root)

    @router.get('/experiments/{experiment_id}/export')
    @ctx.serialized_experiment
    def export_experiment(experiment_id: str, include_runs: bool = True):
        ctx.catalog_location('experiment', experiment_id)
        directory = TemporaryDirectory(prefix='ga-experiment-export-')
        temporary = Path(directory.name)
        try:
            collection = temporary / 'collection'
            collection.mkdir()
            experiment = exchange.export_experiment(experiment_id, collection / 'experiment.gaexp')
            if not include_runs:
                result = experiment
                filename = f'experiment-{experiment_id}.gaexp'
            else:
                rows = []
                page = 1
                while True:
                    listing = ctx.catalog.page(package_kind='run', experiment_id=experiment_id,
                                               page=page, page_size=100)
                    rows.extend(listing['items'])
                    if page >= listing['total_pages']:
                        break
                    page += 1
                with open_package(experiment) as root:
                    experiment_hash = verify_integrity(root)['root_sha256']
                # Hold admission locks until every snapshot is complete. Resume,
                # deletion and sealing cannot swap the selected package paths.
                with ExitStack() as guards:
                    for row in sorted(rows, key=lambda item: item.run_id):
                        guards.enter_context(package_lock(ctx.package_root / 'runs' / f'{row.run_id}.resume-submit.identity'))
                    for row in sorted(rows, key=lambda item: item.run_id):
                        location = ctx.catalog_location('run', row.run_id)
                        if ctx.artifact_jobs.active(row.run_id):
                            raise HTTPException(409, f'运行 {row.run_id} 正在生成结果文件，请完成后再导出资源包。')
                        with open_package(location) as root:
                            status = RunStatus.model_validate(read_json(root / 'status.json'))
                            if status.status.value in ACTIVE:
                                raise HTTPException(409, f'运行 {row.run_id} 尚未停止，请先暂停或等待完成，再导出含运行结果的资源包。')
                            manifest = validate_run_archive(root, sealed=location.is_file())
                            if (manifest.run_id != row.run_id
                                    or manifest.experiment.experiment_id != experiment_id
                                    or manifest.experiment.root_sha256 != experiment_hash):
                                raise HTTPException(409, '运行内嵌实验与当前实验不一致，不能合并导出。')
                        destination = collection / 'runs' / f'{row.run_id}.garun'
                        destination.parent.mkdir(exist_ok=True)
                        if location.is_file():
                            shutil.copyfile(location, destination)
                        else:
                            ctx.runtime.seal(location, destination)
                write_integrity_manifest(collection)
                result = seal_directory(collection, temporary / 'experiment-resources.zip')
                filename = f'experiment-{experiment_id}-resources.zip'
            return FileResponse(result, media_type='application/zip', filename=filename,
                                background=BackgroundTask(directory.cleanup),
                                headers={'Cache-Control': 'no-store'})
        except Exception as exc:
            directory.cleanup()
            if isinstance(exc, HTTPException):
                raise
            if isinstance(exc, (ExperimentExchangeConflict, Timeout)):
                raise HTTPException(409, f'实验或运行正在使用中，无法导出：{exc}') from exc
            if isinstance(exc, (ValueError, OSError, BadZipFile)):
                raise HTTPException(422, f'实验资源包导出失败：{exc}') from exc
            raise

    @router.post('/experiment-import')
    def import_experiment(file: UploadFile = File(...)):
        with TemporaryDirectory(prefix='ga-experiment-import-upload-') as temporary:
            upload = Path(temporary) / 'upload.zip'
            try:
                size = 0
                with upload.open('wb') as stream:
                    while block := file.file.read(1024 * 1024):
                        size += len(block)
                        if size > MAX_UPLOAD_BYTES:
                            raise HTTPException(413, '实验资源包不能超过 2 GiB。')
                        stream.write(block)
                if not size:
                    raise HTTPException(422, '实验资源包为空。')
                return exchange.import_package(upload)
            except ExperimentExchangeConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except Timeout as exc:
                raise HTTPException(409, '实验或运行正在使用中，请稍后重试导入。') from exc
            except (ValueError, OSError, BadZipFile) as exc:
                raise HTTPException(422, f'实验资源包导入失败：{exc}') from exc
