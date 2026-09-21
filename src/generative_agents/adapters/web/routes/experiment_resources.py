"""The shared resource editors' file-backed adapter for one experiment.

Only the catalog resolves an experiment ID. Resource reads and writes below
never resolve public author IDs; all relationships use keys inside the package.
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response

from generative_agents.ga_studio.api import ExperimentWorkspaceService
from generative_agents.ga_studio.api import WorkspaceConflictError
from generative_agents.ga_protocol.schemas.trials import SkillTrialError
from generative_agents.ga_runtime.api import execute_skill_trial
from generative_agents.ga_studio.api import ExperimentResourceEditor, ExperimentResourceError


def create_experiment_resource_router(database, var_dir):
    workspace = ExperimentWorkspaceService(database, package_root=Path(var_dir) / "packages", var_dir=var_dir)
    editor = ExperimentResourceEditor(workspace)
    router = APIRouter(prefix="/api/studio/experiments/{experiment_id}/resources", tags=["experiment-resources"])

    @router.post("/assets")
    async def upload(experiment_id: str, request: Request):
        form = await request.form()
        uploaded = form.get("file")
        if not uploaded or not hasattr(uploaded, "read"):
            raise HTTPException(422, "请选择图片")
        content = await uploaded.read(16 * 1024 * 1024 + 1)
        if len(content) > 16 * 1024 * 1024:
            raise HTTPException(422, "图片不能超过 16 MB")
        from PIL import Image
        import io
        try:
            image = Image.open(io.BytesIO(content))
            image.verify()
            suffix, media = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg"), "WEBP": (".webp", "image/webp")}[image.format]
            logical = f"assets/maps/{uuid4().hex}{suffix}"
            with editor.package(experiment_id, write=True, expected=form.get("expected_content_sha256")) as (root, ep, digest, editable):
                digest = editor.commit(root, {logical: content})
            return {"logical_path": logical, "asset_id": logical, "sha256": hashlib.sha256(content).hexdigest(),
                    "media_type": media, "content_sha256": digest}
        except WorkspaceConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ExperimentResourceError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/{resource_path:path}")
    def read_resource(experiment_id: str, resource_path: str, request: Request):
        try:
            return editor.read(experiment_id, resource_path, dict(request.query_params))
        except ExperimentResourceError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.api_route("/{resource_path:path}", methods=["PUT", "POST", "DELETE"])
    def write_resource(experiment_id: str, resource_path: str, request: Request, body: dict):
        try:
            parts = resource_path.split("/")
            if request.method == "POST" and parts == ["maps", experiment_id, "validate"]:
                # Validation also works for sealed packages and never rewrites them.
                return editor.read(experiment_id, f"maps/{experiment_id}", {}, validate_execution=True)
            if request.method == "POST" and len(parts) == 3 and parts[0] == "skills" and parts[2] == "run":
                with tempfile.TemporaryDirectory(prefix="ga-skill-trial-") as destination:
                    credentials = editor.prepare_trial(experiment_id, parts[1], body, destination=destination)
                    return execute_skill_trial(destination, credentials=credentials)
            result = editor.write(experiment_id, resource_path, request.method, body)
            return Response(status_code=204) if result is None else result
        except WorkspaceConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except SkillTrialError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": str(exc), "trace": exc.trace}) from exc
        except ExperimentResourceError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
