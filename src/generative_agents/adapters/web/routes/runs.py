"""Runs HTTP routes."""
from __future__ import annotations
import shutil
from uuid import uuid4
from fastapi import HTTPException, Query
from generative_agents.ga_protocol.packages.reading import open_readonly_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.validation import validate_run_directory
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.locking import package_lock
from generative_agents.adapters.web.routes.requests import ResumeRunRequest
from generative_agents.adapters.web.context import _catalog_item
from generative_agents.ga_replay.api import ReplayReader, read_run_quality
from generative_agents.ga_runtime.api import reconcile_run


def recovery_options(location, run_id):
    if location.is_dir():
        reconcile_run(location)
    with open_readonly_package(location) as root:
        status = RunStatus.model_validate(read_json(root / 'status.json'))
        if status.run_id != run_id:
            raise HTTPException(status_code=409, detail='Run 状态身份与目录索引不一致')
        step = status.committed_step
        action = None
        reason = None
        if status.status.value in {'PAUSED', 'FAILED'}:
            try:
                validate_run_directory(root, sealed=location.is_file())
                action = 'finalize' if step == status.total_steps else 'restart' if step == 0 else 'resume'
            except (OSError, ValueError, KeyError, TypeError) as exc:
                reason = str(exc)
        else:
            reason = '只有暂停或失败的仿真可以恢复'
        return {'run_id': run_id, 'status': status.status.value, 'active_attempt_id': status.active_attempt_id,
                'committed_step': step, 'recoverable_step': step if action else 0,
                'recoverable': bool(action), 'recovery_action': action, 'recovery_reason': reason}

def install_routes(router, ctx):

    @router.get('/runs/{run_id}')
    def get_run(run_id: str):
        row = ctx.catalog.get('run', run_id)
        if row is None:
            raise HTTPException(status_code=404, detail='Run package is not in the Studio catalog')
        return ctx.run_summary(row)

    @router.get('/runs/{run_id}/quality')
    def run_quality(run_id: str, offset: int=Query(default=0, ge=0), limit: int=Query(default=50, ge=1, le=100)):
        with ReplayReader(ctx.run_location(run_id)) as replay:
            quality = read_run_quality(replay.root, replay.manifest, replay.status)
        issues = quality.get('issues') or []
        return {**quality, 'issues': issues[offset:offset+limit], 'total': len(issues),
                'next_offset': offset+limit if offset+limit < len(issues) else None}

    @router.get('/runs/{run_id}/recovery')
    def run_recovery(run_id: str):
        return recovery_options(ctx.run_location(run_id), run_id)

    @router.delete('/runs/{run_id}', status_code=204)
    def delete_run(run_id: str):
        return ctx.delete_run(run_id)

    @router.post('/runs/{run_id}/seal')
    @ctx.jobs.action('SEAL_RUN')
    @ctx.serialized_run
    def seal_run(run_id: str):
        if ctx.artifact_jobs.active(run_id):
            raise HTTPException(status_code=409, detail='Run 正在导出，请等待任务完成后封存')
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
    @ctx.jobs.action('RESUME_RUN')
    def resume_run(run_id: str, body: ResumeRunRequest | None=None):
        with package_lock(ctx.package_root / 'runs' / f'{run_id}.resume-submit.identity'):
            location = ctx.run_location(run_id)
            options = recovery_options(location, run_id)
            if not options['recoverable']:
                raise HTTPException(status_code=409, detail=options['recovery_reason'])
            if body and 'expected_attempt_id' in body.model_fields_set and body.expected_attempt_id != options['active_attempt_id']:
                raise HTTPException(status_code=409, detail='Attempt 已变化，请重新打开恢复确认')
            if body and body.checkpoint_step is not None and body.checkpoint_step != options['committed_step']:
                raise HTTPException(status_code=409, detail=f"必须从已提交边界 Step {options['committed_step']} 恢复，不能重复执行已提交步骤")
            run_root = ctx.runtime.materialize(location, extracted_destination=ctx.package_root / 'runs' / f'workspace-{uuid4().hex}') if location.is_file() else location
            return ctx.submit_directory(run_root)

    @router.post('/runs/{run_id}/rerun')
    @ctx.jobs.action('RERUN')
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
        location = ctx.run_location(run_id)
        if location.is_dir():
            reconcile_run(location)
        return ctx.supervisor.process_status(run_id, location if location.is_dir() else None)
