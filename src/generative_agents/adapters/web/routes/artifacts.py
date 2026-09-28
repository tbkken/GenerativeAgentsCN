"""Artifacts HTTP routes."""
from __future__ import annotations
from generative_agents.ga_runtime.api import export_run_artifact, export_checkpoint_artifact
import hashlib
import mimetypes
from fastapi import HTTPException, Response
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from generative_agents.ga_replay.api import read_run_quality
from generative_agents.ga_protocol.schemas.manifests import RunManifest
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import checked_package_path
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.io import open_shared_reader
from generative_agents.adapters.web.media import _SnapshotResponse
from generative_agents.ga_protocol.packages.io import iter_package_files
from generative_agents.ga_replay.api import definition_names, conversation_views, memory_views
from generative_agents.adapters.web.routes.requests import ArtifactJobCreate
from generative_agents.ga_replay.api import artifact_for_id, read_slice, read_metadata, ReplayReader
from generative_agents.ga_protocol.packages.reading import open_readonly_package

def install_routes(router, ctx):

    @router.get('/runs/{run_id}/artifacts/{artifact_id}/download')
    def download_run_artifact(run_id: str, artifact_id: str, logical_name: str | None=None):
        location = ctx.run_location(run_id)
        try:
            item = artifact_for_id(location, run_id, artifact_id, logical_name=logical_name, verify=False)
        except PackageError:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=404, detail='Artifact is not present in this Run package') from exc
        if item is None:
            raise HTTPException(status_code=404, detail='Artifact is not present in this Run package')
        context = open_readonly_package(location)
        root = context.__enter__()
        reader = None
        closed = False
        def close():
            nonlocal closed
            if not closed:
                closed = True
                try:
                    if reader is not None:
                        reader.__exit__(None, None, None)
                finally:
                    context.__exit__(None, None, None)
        try:
            path = checked_package_path(root / 'artifacts' / item['logical_name'])
            reader = open_shared_reader(path)
            handle = reader.__enter__()
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            if digest[:32] != artifact_id:
                raise HTTPException(status_code=404, detail='Artifact is not present in this Run package')
            size = handle.tell()
            handle.seek(0)
            from urllib.parse import quote
            return _SnapshotResponse(iter(lambda: handle.read(65536), b''), close, media_type=item['media_type'],
                headers={'ETag': f'"{digest}"', 'Content-Length': str(size), 'Content-Disposition': f"attachment; filename*=utf-8''{quote(path.name)}"})
        except BaseException:
            close()
            raise

    @router.post('/runs/{run_id}/artifact-jobs', status_code=201)
    @ctx.serialized_run
    def create_run_artifact(run_id: str, body: ArtifactJobCreate):
        root = ctx.mutable_run_root(run_id)
        summary, source_status = read_metadata(root)
        # Admission only inspects the namespace and captures the boundary.
        # Full integrity checks and report reconstruction belong to the job.
        for path in checked_package_path(root / 'frames').glob('step-*.json.gz'):
            checked_package_path(path)
        facts = {'summary': summary, 'status': source_status.model_dump(mode='json')}
        def export():
            with ReplayReader(root):
                pass
            quality = read_run_quality(root, RunManifest.model_validate(read_json(root / 'run.json')), source_status) if body.job_type == 'RESULT_BUNDLE' else None
            selected = read_slice(root, end=source_status.committed_step) if body.job_type != 'RESULT_BUNDLE' and source_status.committed_step else {**facts, 'frames': [], 'definitions': []}
            return export_run_artifact(root, facts, job_type=body.job_type, parameters=body.parameters, quality=quality,
                memories=memory_views(selected, definition_names(selected)) if body.job_type == 'FILTERED_MEMORIES' else None,
                conversations=conversation_views(selected, definition_names(selected)) if body.job_type == 'FILTERED_CONVERSATIONS' else None)
        return ctx.artifact_jobs.submit(root, run_id, export, parameters={'job_type': body.job_type, 'source_step': source_status.committed_step})

    @router.get('/runs/{run_id}/artifact-jobs/{job_id}')
    def artifact_job_status(run_id: str, job_id: str):
        with open_readonly_package(ctx.run_location(run_id)) as root:
            return ctx.artifact_jobs.status(root, run_id, job_id)

    @router.post('/runs/{run_id}/checkpoints/{step_no}/artifact-job', status_code=201)
    @ctx.serialized_run
    def create_checkpoint_artifact(run_id: str, step_no: int):
        root = ctx.mutable_run_root(run_id)
        status = RunStatus.model_validate(read_json(root / 'status.json'))
        if step_no < 1 or step_no > status.committed_step:
            raise HTTPException(status_code=422, detail='Checkpoint is outside the committed Run boundary')
        return ctx.artifact_jobs.submit(root, run_id, lambda: export_checkpoint_artifact(root, run_id, step_no),
                                       parameters={'job_type': 'CHECKPOINT', 'source_step': step_no})
