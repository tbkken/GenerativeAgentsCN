"""Runs HTTP routes."""
from __future__ import annotations
import shutil
from uuid import uuid4
from fastapi import HTTPException, Query
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.locking import package_lock
from generative_agents.adapters.web.routes.requests import ResumeRunRequest
from generative_agents.adapters.web.context import _catalog_item

def install_routes(router, ctx):

    @router.get('/runs/{run_id}')
    def get_run(run_id: str):
        row = ctx.catalog.get('run', run_id)
        if row is None:
            raise HTTPException(status_code=404, detail='Run package is not in the Studio catalog')
        return ctx.run_summary(row)

    @router.delete('/runs/{run_id}', status_code=204)
    def delete_run(run_id: str):
        return ctx.delete_run(run_id)

    @router.post('/runs/{run_id}/seal')
    def seal_run(run_id: str):
        source = ctx.catalog_location('run', run_id)
        if source.is_file():
            return _catalog_item(ctx.catalog.get('run', run_id))
        archive = ctx.package_root / 'runs' / f'{run_id}.garun'
        try:
            ctx.runtime.seal(source, archive)
            shutil.rmtree(source)
            record = ctx.catalog.upsert(archive)
            return {'run_id': run_id, 'location': str(archive), 'status': record.package_status}
        except Exception:
            if source.exists():
                archive.unlink(missing_ok=True)
            raise

    @router.post('/runs/{run_id}/resume')
    def resume_run(run_id: str, body: ResumeRunRequest | None=None):
        from generative_agents.ga_protocol.facts.recovery import boundary_snapshot
        with package_lock(ctx.package_root / 'runs' / f'{run_id}.resume-submit.identity'):
            location = ctx.run_location(run_id)
            with open_package(location) as root:
                status = RunStatus.model_validate(read_json(root / 'status.json'))
                if status.status.value not in {'PAUSED', 'FAILED'}:
                    raise HTTPException(status_code=409, detail='只有暂停或失败的仿真可以恢复')
                if body and body.expected_attempt_id and (body.expected_attempt_id != status.active_attempt_id):
                    raise HTTPException(status_code=409, detail='Attempt 已变化，请重新打开恢复确认')
                if body and body.checkpoint_step != status.committed_step:
                    raise HTTPException(status_code=409, detail=f'必须从已提交边界 Step {status.committed_step} 恢复，不能重复执行已提交步骤')
                try:
                    if status.committed_step:
                        boundary_snapshot(root, run_id, status.committed_step)
                except (OSError, ValueError) as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
            run_root = ctx.runtime.materialize(location, extracted_destination=ctx.package_root / 'runs' / f'workspace-{uuid4().hex}') if location.is_file() else location
            return ctx.submit_directory(run_root)

    @router.post('/runs/{run_id}/rerun')
    def rerun(run_id: str, steps: int | None=Query(default=None, ge=1)):
        destination = ctx.package_root / 'runs' / f'workspace-{uuid4().hex}'
        try:
            run_root = ctx.runtime.create_rerun(ctx.run_location(run_id), destination, requested_steps=steps)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ctx.submit_directory(run_root)

    @router.post('/runs/{run_id}/pause')
    def pause_run(run_id: str):
        location = ctx.run_location(run_id)
        if location.is_file():
            raise HTTPException(status_code=409, detail='a sealed Run is not executing')
        ctx.runtime.request_pause(location)
        return {'run_id': run_id, 'pause_requested': True}

    @router.post('/runs/{run_id}/cancel')
    def cancel_run(run_id: str):
        location = ctx.run_location(run_id)
        if location.is_file():
            raise HTTPException(status_code=409, detail='a sealed Run is not executing')
        ctx.runtime.request_cancel(location)
        return {'run_id': run_id, 'cancel_requested': True}

    @router.get('/runs/{run_id}/process')
    def runtime_process(run_id: str):
        return ctx.supervisor.process_status(run_id)
