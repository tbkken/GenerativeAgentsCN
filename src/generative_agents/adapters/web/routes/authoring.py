"""Authoring HTTP routes."""
from __future__ import annotations
from fastapi import File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from generative_agents.adapters.web.routes.requests import AgentResourceCreate, AgentResourceUpdate, CrowdResourceCreate, CrowdResourceUpdate, DocumentResourceCreate, DocumentResourceUpdate, SecretCreate

def install_routes(router, ctx):

    @router.post('/resources/agent-images', status_code=201)
    def upload_public_agent_images(portrait: UploadFile | None=File(None), sprite: UploadFile | None=File(None)):
        images = {}
        if portrait is not None:
            images['portrait'] = (portrait.file, portrait.filename or 'portrait.png')
        if sprite is not None:
            images['sprite'] = (sprite.file, sprite.filename or 'sprite-4x4.png')
        try:
            result = ctx.asset_service.upload_database_images(images)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for item in result.values():
            item['content_url'] = f"/api/studio/resources/assets/{item['asset_id']}/content"
        return result

    @router.post('/resources/assets', status_code=201)
    def upload_studio_asset(file: UploadFile=File(...)):
        """Upload a mutable Studio asset used while authoring a public Map."""
        try:
            return ctx.asset_service.upload(file.file, logical_name=file.filename or 'asset', media_type=file.content_type)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get('/resources/assets/{asset_id}')
    def get_studio_asset(asset_id: str):
        try:
            return ctx.asset_service.get(asset_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get('/resources/assets/{asset_id}/content')
    def public_asset_content(asset_id: str, request: Request, width: int | None=Query(default=None)):
        try:
            metadata = ctx.asset_service.content_metadata(asset_id)
            if width is not None and width not in {96, 192, 384}:
                raise HTTPException(422, '图片预览宽度须为 96、192 或 384')
            suffix = f'-{width}-v1' if width else ''
            etag = f'"{metadata["sha256"]}{suffix}"'
            headers = {'ETag': etag, 'Cache-Control': 'public, max-age=31536000, immutable',
                       'X-Content-Type-Options': 'nosniff'}
            candidates = {item.strip().removeprefix('W/') for item in request.headers.get('if-none-match', '').split(',')}
            if etag in candidates or '*' in candidates:
                return Response(status_code=304, headers=headers)
            content = ctx.asset_service.thumbnail(metadata, width) if width else ctx.asset_service.delivery_content(metadata)
            media_type = 'image/webp' if width else metadata['media_type']
            if isinstance(content, bytes):
                return Response(content=content, media_type=media_type, headers=headers)
            return FileResponse(content, media_type=media_type, headers=headers,
                                content_disposition_type='inline')
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(getattr(exc, 'status_code', 404), str(exc)) from exc

    @router.post('/secrets', status_code=201)
    def create_secret(body: SecretCreate):
        try:
            return ctx.secret_service.create(kind=body.kind, value=body.value)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post('/secrets/{secret_id}/replacement', status_code=201)
    def replace_secret(secret_id: str, body: SecretCreate):
        try:
            return ctx.secret_service.create(kind=body.kind, value=body.value, supersedes_id=secret_id)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get('/resources/agents')
    def list_agents(include_archived: bool=False, q: str=Query(default='', max_length=200),
                      page: int=Query(default=1, ge=1), page_size: int=Query(default=20, ge=1, le=100)):
        return ctx.resources.list_summaries('agent', include_archived=include_archived,
                                            query=q, page=page, page_size=page_size)

    @router.post('/resources/agents', status_code=201)
    def create_agent(body: AgentResourceCreate):
        return ctx.studio_call(lambda: ctx.resources.create_agent(body.definition, description=body.description))

    @router.get('/resources/agents/{agent_id}')
    def get_agent(agent_id: str):
        return ctx.studio_call(lambda: ctx.resources.get_agent(agent_id))

    @router.put('/resources/agents/{agent_id}')
    def update_agent(agent_id: str, body: AgentResourceUpdate):
        return ctx.studio_call(lambda: ctx.resources.save_agent(agent_id, body.definition, expected_row_version=body.row_version, description=body.description))

    @router.delete('/resources/agents/{agent_id}', status_code=204)
    def delete_agent(agent_id: str):
        ctx.studio_call(lambda: ctx.resources.delete('agent', agent_id))
        return Response(status_code=204)

    @router.get('/resources/crowds')
    def list_crowds(include_archived: bool=False, q: str=Query(default='', max_length=200),
                      page: int=Query(default=1, ge=1), page_size: int=Query(default=20, ge=1, le=100)):
        return ctx.resources.list_summaries('crowd', include_archived=include_archived,
                                            query=q, page=page, page_size=page_size)

    @router.get('/resources/crowds/{crowd_id}')
    def get_crowd(crowd_id: str):
        return ctx.studio_call(lambda: ctx.resources.get_crowd(crowd_id))

    @router.post('/resources/crowds', status_code=201)
    def create_crowd(body: CrowdResourceCreate):
        return ctx.studio_call(lambda: ctx.resources.create_crowd(name=body.name, description=body.description, crowd_key=body.crowd_key, agent_ids=body.agent_ids))

    @router.put('/resources/crowds/{crowd_id}')
    def update_crowd(crowd_id: str, body: CrowdResourceUpdate):
        return ctx.studio_call(lambda: ctx.resources.save_crowd(crowd_id, agent_ids=body.agent_ids, expected_row_version=body.row_version, name=body.name, description=body.description))

    @router.delete('/resources/crowds/{crowd_id}', status_code=204)
    def delete_crowd(crowd_id: str):
        ctx.studio_call(lambda: ctx.resources.delete('crowd', crowd_id))
        return Response(status_code=204)

    @router.get('/resources/model-presets')
    def list_model_presets(include_archived: bool=False, q: str=Query(default='', max_length=200),
                      page: int=Query(default=1, ge=1), page_size: int=Query(default=20, ge=1, le=100)):
        return ctx.resources.list_summaries('model', include_archived=include_archived,
                                            query=q, page=page, page_size=page_size)

    @router.get('/resources/model-presets/{preset_id}')
    def get_model_preset(preset_id: str):
        return ctx.studio_call(lambda: ctx.resources.get_model_preset(preset_id))

    @router.post('/resources/model-presets', status_code=201)
    def create_model_preset(body: DocumentResourceCreate):
        return ctx.studio_call(lambda: ctx.resources.create_model_preset(name=body.name, description=body.description, preset_key=body.key, config=body.config))

    @router.put('/resources/model-presets/{preset_id}')
    def update_model_preset(preset_id: str, body: DocumentResourceUpdate):
        return ctx.studio_call(lambda: ctx.resources.save_model_preset(preset_id, config=body.config, expected_row_version=body.row_version, name=body.name, description=body.description))

    @router.delete('/resources/model-presets/{preset_id}', status_code=204)
    def delete_model_preset(preset_id: str):
        ctx.studio_call(lambda: ctx.resources.delete('model', preset_id))
        return Response(status_code=204)

    @router.get('/resources/evaluators')
    def list_evaluators(include_archived: bool=False, q: str=Query(default='', max_length=200),
                      page: int=Query(default=1, ge=1), page_size: int=Query(default=20, ge=1, le=100)):
        return ctx.resources.list_summaries('evaluator', include_archived=include_archived,
                                            query=q, page=page, page_size=page_size)

    @router.get('/resources/evaluators/{evaluator_id}')
    def get_evaluator(evaluator_id: str):
        return ctx.studio_call(lambda: ctx.resources.get_evaluator(evaluator_id))

    @router.post('/resources/evaluators', status_code=201)
    def create_evaluator(body: DocumentResourceCreate):
        return ctx.studio_call(lambda: ctx.resources.create_evaluator(name=body.name, description=body.description, evaluator_key=body.key, config=body.config))

    @router.put('/resources/evaluators/{evaluator_id}')
    def update_evaluator(evaluator_id: str, body: DocumentResourceUpdate):
        return ctx.studio_call(lambda: ctx.resources.save_evaluator(evaluator_id, config=body.config, expected_row_version=body.row_version, name=body.name, description=body.description))

    @router.delete('/resources/evaluators/{evaluator_id}', status_code=204)
    def delete_evaluator(evaluator_id: str):
        ctx.studio_call(lambda: ctx.resources.delete('evaluator', evaluator_id))
        return Response(status_code=204)

    @router.post('/resources/{kind}/{resource_id}/archive')
    def archive_resource(kind: str, resource_id: str):
        mapping = {'agents': 'agent', 'crowds': 'crowd', 'model-presets': 'model', 'evaluators': 'evaluator'}
        if kind not in mapping:
            raise HTTPException(status_code=404, detail='unknown Studio resource kind')
        ctx.studio_call(lambda: ctx.resources.archive(mapping[kind], resource_id, archived=True))
        return {'resource_id': resource_id, 'archived': True}

    @router.post('/resources/{kind}/{resource_id}/restore')
    def restore_resource(kind: str, resource_id: str):
        mapping = {'agents': 'agent', 'crowds': 'crowd', 'model-presets': 'model', 'evaluators': 'evaluator'}
        if kind not in mapping:
            raise HTTPException(status_code=404, detail='unknown Studio resource kind')
        ctx.studio_call(lambda: ctx.resources.archive(mapping[kind], resource_id, archived=False))
        return {'resource_id': resource_id, 'archived': False}
