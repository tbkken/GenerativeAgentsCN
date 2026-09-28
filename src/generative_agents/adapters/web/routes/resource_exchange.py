"""Resource exchange HTTP adapter; Studio owns all resource conversion and writes."""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic
from typing import Any, Literal
from uuid import uuid4
from zipfile import BadZipFile

from fastapi import File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from generative_agents.ga_studio.api import ResourceExchangeService, ServiceError


ResourceKind = Literal['map', 'agent', 'crowd', 'skill', 'brain', 'model', 'spatial_asset', 'evaluator']
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
PREVIEW_LIFETIME_SECONDS = 30 * 60
MAX_PENDING_PREVIEWS = 32


class ResourceSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: ResourceKind
    key: str = Field(min_length=1, max_length=256)


class ImportResources(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[a-f0-9]{32}$')
    selected: list[ResourceSelection] = Field(min_length=1, max_length=1000)
    include_dependencies: bool | None = None


@dataclass
class PreviewTicket:
    directory: TemporaryDirectory
    path: Path
    digest: str
    expires: float
    lock: Any = field(default_factory=Lock)
    committed_selection: tuple | None = None
    result: dict | None = None
    release_requested: bool = False


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def install_routes(router, ctx):
    service = ResourceExchangeService(ctx.database, asset_store=ctx.asset_service.store)
    tickets: dict[str, PreviewTicket] = {}
    registry_lock = Lock()
    staging = ctx.package_root.parent / 'resource-exchange'
    staging.mkdir(parents=True, exist_ok=True)

    def cleanup():
        now = monotonic()
        with registry_lock:
            for token, ticket in list(tickets.items()):
                if ticket.expires <= now and not ticket.lock.locked():
                    tickets.pop(token)
                    ticket.directory.cleanup()

    def call(operation):
        try:
            return operation()
        except HTTPException:
            raise
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        except BadZipFile as exc:
            raise HTTPException(status_code=422, detail='文件不是有效的 ZIP 资源包，或归档内容已损坏。') from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def release_ticket(token):
        with registry_lock:
            ticket = tickets.get(token)
            if ticket is None:
                return 204
            if not ticket.lock.acquire(blocking=False):
                # Closing a preview does not interrupt a transaction which has
                # already claimed its bytes. Its finally block releases them.
                ticket.release_requested = True
                return 202
            try:
                tickets.pop(token)
                ticket.directory.cleanup()
            finally:
                ticket.lock.release()
        return 204

    @router.get('/resource-exchange/catalog')
    def resource_catalog(kind: ResourceKind, q: str=Query(default='', max_length=200),
                         page: int=Query(default=1, ge=1), page_size: int=Query(default=20, ge=1, le=100)):
        cleanup()
        return call(lambda: service.catalog(kind=kind, query=q, page=page, page_size=page_size))

    @router.get('/resource-exchange/export/{kind}/{resource_id}')
    def export_resource(kind: ResourceKind, resource_id: str, include_dependencies: bool = False):
        cleanup()
        directory = TemporaryDirectory(prefix='export-', dir=staging)
        destination = Path(directory.name) / 'resources-config.zip'
        try:
            result = call(lambda: service.export_resource(kind, resource_id, destination, include_dependencies=include_dependencies))
        except Exception:
            directory.cleanup()
            raise
        return FileResponse(result, media_type='application/zip', filename=f'{kind}-config.zip',
                            background=BackgroundTask(directory.cleanup), headers={'Cache-Control': 'no-store'})

    @router.get('/resource-exchange/export-preview/{kind}/{resource_id}')
    def preview_export(kind: ResourceKind, resource_id: str, include_dependencies: bool = False):
        return call(lambda: service.preview_export(kind, resource_id, include_dependencies=include_dependencies))

    @router.post('/resource-exchange/preview')
    def preview_resources(file: UploadFile = File(...), kind: ResourceKind = Form(...)):
        cleanup()
        with registry_lock:
            if sum(ticket.result is None for ticket in tickets.values()) >= MAX_PENDING_PREVIEWS:
                raise HTTPException(status_code=429, detail='待导入的资源包过多，请完成已有导入或稍后重试。')
        directory = TemporaryDirectory(prefix='preview-', dir=staging)
        destination = Path(directory.name) / 'upload.zip'
        digest = sha256()
        try:
            size = 0
            with destination.open('wb') as output:
                while block := file.file.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail='资源包不能超过 256 MiB。')
                    digest.update(block)
                    output.write(block)
            if not size:
                raise HTTPException(status_code=422, detail='资源包为空。')
            result = call(lambda: service.preview_import(destination))
            token = uuid4().hex
            ticket = PreviewTicket(directory, destination, digest.hexdigest(), monotonic() + PREVIEW_LIFETIME_SECONDS)
            with registry_lock:
                if sum(ticket.result is None for ticket in tickets.values()) >= MAX_PENDING_PREVIEWS:
                    raise HTTPException(status_code=429, detail='待导入的资源包过多，请完成已有导入或稍后重试。')
                tickets[token] = ticket
            return {**result, 'token': token, 'requested_kind': kind,
                    'upload_sha256': ticket.digest, 'expires_in_seconds': PREVIEW_LIFETIME_SECONDS}
        except Exception:
            directory.cleanup()
            raise
        finally:
            file.file.close()

    @router.post('/resource-exchange/imports')
    def import_resources(body: ImportResources):
        cleanup()
        with registry_lock:
            ticket = tickets.get(body.token)
            if ticket is None or ticket.expires <= monotonic():
                raise HTTPException(status_code=410, detail='导入预览已过期，请重新选择资源包。')
            # Acquire while the registry is held, so cleanup cannot delete a ticket
            # between lookup and import. Concurrent retries wait on the same ticket.
            ticket.lock.acquire()
        try:
            selections = [item.model_dump() for item in body.selected]
            signature = (tuple(sorted({(item['kind'], item['key']) for item in selections})), body.include_dependencies)
            if ticket.result is not None:
                if ticket.committed_selection != signature:
                    raise HTTPException(status_code=409, detail='此预览已完成导入。导入其他资源请重新上传资源包。')
                return ticket.result
            if _file_digest(ticket.path) != ticket.digest:
                raise HTTPException(status_code=409, detail='资源包在预览后发生变化，请重新上传。')
            result = call(lambda: service.import_resources(ticket.path, selections=selections, include_dependencies=body.include_dependencies))
            ticket.committed_selection = signature
            ticket.result = result
            # Retry receipts need no uploaded bytes once Studio has committed.
            # A failed cleanup must never turn a successful import into a retry.
            try:
                ticket.directory.cleanup()
            except OSError:
                pass
            return result
        finally:
            ticket.lock.release()
            if ticket.release_requested:
                release_ticket(body.token)

    @router.delete('/resource-exchange/preview/{token}', status_code=204)
    def discard_preview(token: str):
        return Response(status_code=release_ticket(token))
