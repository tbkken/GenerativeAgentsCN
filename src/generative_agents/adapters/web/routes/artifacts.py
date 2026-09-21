"""Artifacts HTTP routes."""
from __future__ import annotations
from generative_agents.ga_runtime.api import export_run_artifact, export_checkpoint_artifact
import hashlib
import mimetypes
from fastapi import HTTPException, Response
from generative_agents.ga_replay.api import read_run_quality
from generative_agents.ga_protocol.schemas.manifests import RunManifest
from generative_agents.ga_protocol.schemas.manifests import RunStatus
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.io import checked_package_path
from generative_agents.ga_protocol.packages.io import iter_package_files
from generative_agents.ga_replay.api import definition_names, conversation_views, memory_views
from generative_agents.adapters.web.routes.requests import ArtifactJobCreate

def install_routes(router, ctx):

    @router.get('/runs/{run_id}/artifacts/{artifact_id}/download')
    def download_run_artifact(run_id: str, artifact_id: str):
        with open_package(ctx.run_location(run_id)) as root:
            artifact_root = checked_package_path(root / 'artifacts')
            for _relative, path in iter_package_files(artifact_root) if artifact_root.is_dir() else []:
                if not path.is_file() or path.is_symlink() or path.name.startswith('.'):
                    continue
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest()[:32] != artifact_id:
                    continue
                return Response(content=content, media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream', headers={'Content-Disposition': f'attachment; filename="{path.name}"'})
        raise HTTPException(status_code=404, detail='Artifact is not present in this Run package')

    @router.post('/runs/{run_id}/artifact-jobs', status_code=201)
    def create_run_artifact(run_id: str, body: ArtifactJobCreate):
        root = ctx.mutable_run_root(run_id)
        facts = ctx.read_run_facts(run_id)
        source_status = RunStatus.model_validate(facts['status'])
        quality = read_run_quality(root, RunManifest.model_validate(read_json(root / 'run.json')), source_status) if body.job_type == 'RESULT_BUNDLE' else None
        return export_run_artifact(root, facts, job_type=body.job_type, parameters=body.parameters, quality=quality, memories=memory_views(facts, definition_names(facts)) if body.job_type == 'FILTERED_MEMORIES' else None, conversations=conversation_views(facts, definition_names(facts)) if body.job_type == 'FILTERED_CONVERSATIONS' else None)

    @router.post('/runs/{run_id}/checkpoints/{step_no}/artifact-job', status_code=201)
    def create_checkpoint_artifact(run_id: str, step_no: int):
        return export_checkpoint_artifact(ctx.mutable_run_root(run_id), run_id, step_no)
