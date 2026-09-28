"""Experiments HTTP routes."""
from __future__ import annotations
import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4
from zipfile import BadZipFile
from fastapi import File, Form, HTTPException, Query, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.io import open_package
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.constants import PROTOCOL_VERSION
from generative_agents.ga_protocol.packages.io import seal_directory
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_studio.api import WorkspaceConflictError
from generative_agents.ga_studio.api import AgentPlacement
from generative_agents.ga_studio.api import ExperimentSelection
from generative_agents.ga_protocol.packages.definition import _experiment_definition, experiment_overview
from generative_agents.ga_protocol.packages.reading import read_package_json
from generative_agents.ga_protocol.schemas.manifests import ExperimentManifest
from generative_agents.ga_replay.api import packaged_asset_url
from generative_agents.adapters.web.routes.requests import ExperimentWorkspaceCreate, ExperimentEntrypointUpdate, ExperimentDefinitionUpdate, ExperimentSettingsUpdate, ExperimentBatchRequest
from generative_agents.adapters.web.context import _catalog_item


def _package_read_error(row, error):
    """Read only the package header to explain rejection; never migrate its content."""
    try:
        name = 'run.json' if row.package_kind == 'run' else 'manifest.json'
        version = read_package_json(Path(row.location), name).get('schema_version')
        if version != PROTOCOL_VERSION:
            return f'协议版本不支持：此包为 v{version}，当前支持 v{PROTOCOL_VERSION}。原始文件已保留。'
    except (ValueError, OSError, AttributeError, BadZipFile):
        pass
    return f'资源包无法读取：{error}'

def install_routes(router, ctx):

    @router.get('/experiments')
    def list_experiment_workspaces(archived: str=Query(default='active', pattern='^(active|archived|all)$'), page: int=Query(default=1, ge=1), page_size: int=Query(default=5, ge=5, le=5)):
        presentation = ctx.read_presentation().get('experiments') or {}
        archived_ids = [key for key, value in presentation.items() if value.get('archived_at')]
        listing = ctx.catalog.page(package_kind='experiment', page=page, page_size=page_size,
                                   included_ids=archived_ids if archived == 'archived' else None,
                                   excluded_ids=archived_ids if archived == 'active' else ())
        rows, total = listing['items'], listing['total']
        visible_ids = {row.experiment_id for row in rows}
        by_experiment = ctx.catalog.recent_runs(visible_ids)
        items = []
        for row in rows:
            editable = Path(row.location).is_dir()
            lifecycle = 'DRAFT' if editable else 'SEALED'
            metadata = dict(presentation.get(row.experiment_id) or {})
            archived_at = metadata.get('archived_at')
            item_owner = str(metadata.get('owner') or '')
            item_tags = [str(value) for value in metadata.get('tags') or []]
            recent = by_experiment.get(row.experiment_id) or {}
            metadata = {'id': row.experiment_id, 'experiment_id': row.experiment_id, 'name': row.display_name,
                        'owner': item_owner, 'tags': item_tags, 'archived_at': archived_at, 'status': lifecycle,
                        'package_status': lifecycle, 'content_sha256': row.content_sha256,
                        'updated_at': row.updated_at.isoformat(), 'run_count': recent.get('count', 0)}
            try:
                row, manifest, definition = ctx.experiment_snapshot(row.experiment_id, summary_only=True)
            except (ValueError, OSError, HTTPException, BadZipFile) as exc:
                items.append({**metadata, 'editable': False, 'package_error': _package_read_error(row, exc),
                              'goal': '', 'latest_run': None, 'core_parameters': {}})
                continue
            editable = Path(row.location).is_dir()
            lifecycle = 'DRAFT' if editable else 'SEALED'
            metadata.update(status=lifecycle, package_status=lifecycle, name=row.display_name,
                            content_sha256=row.content_sha256, updated_at=row.updated_at.isoformat())
            latest_run, latest_run_error = None, None
            if recent:
                try:
                    latest_run = ctx.run_list_summary(recent['latest'])
                except (ValueError, OSError, HTTPException, BadZipFile) as exc:
                    latest_run_error = _package_read_error(recent['latest'], exc)
            items.append({**metadata, 'experiment_key': manifest['experiment'].get('key'), 'goal': manifest['experiment'].get('goal', ''), 'editable': editable, 'latest_run': latest_run, 'latest_run_error': latest_run_error, 'core_parameters': {'agent_count': len(definition['agents']), 'max_steps': definition['simulation'].get('max_steps'), 'world_name': definition['world'].get('world_name')}})
        return {'items': items, 'page': page, 'page_size': page_size, 'total': total, 'total_pages': max(1, (total + page_size - 1) // page_size)}

    @ctx.serialized_experiment
    def workspace_view(experiment_id, *, view='overview'):
        row = ctx.catalog.get('experiment', experiment_id)
        if row is None:
            raise HTTPException(404, 'experiment is not in the Studio package catalog')
        location = Path(row.location)
        try:
            manifest = ExperimentManifest.model_validate(read_package_json(location, 'manifest.json')).model_dump(mode='json')
            if manifest['experiment']['experiment_id'] != experiment_id:
                raise PackageError('experiment identity disagrees with its catalog')
            digest = read_package_json(location, 'integrity/sha256.json')['root_sha256']
            if view != 'status':
                manifest, definition, summary = experiment_overview(location)
                if view == 'definition':
                    manifest, definition = _experiment_definition(location)
        except (ValueError, OSError, BadZipFile) as exc:
            raise HTTPException(status_code=422, detail=_package_read_error(row, exc)) from exc
        recent = ctx.catalog.recent_runs([experiment_id]).get(experiment_id) or {}
        editable = location.is_dir()
        lifecycle = 'DRAFT' if editable else 'SEALED'
        presentation = ctx.experiment_presentation(experiment_id)
        snapshot = {'id': experiment_id, 'state': lifecycle, 'lock_version': 1, 'definition_hash': digest}
        payload = {'id': experiment_id, 'experiment_id': experiment_id,
                   'name': manifest['experiment'].get('name', row.display_name),
                   'goal': manifest['experiment'].get('goal', ''),
                   'owner': str(presentation.get('owner') or ''),
                   'tags': [str(value) for value in presentation.get('tags') or []],
                   'archived_at': presentation.get('archived_at'), 'row_version': 1,
                   'status': lifecycle, 'editable': editable,
                   'current_draft': snapshot if editable else None,
                   'current_published': snapshot if not editable else None,
                   'content_sha256': digest, 'updated_at': row.updated_at.isoformat(),
                   'run_count': recent.get('count', 0),
                   'latest_run': ctx.run_list_summary(recent['latest']) if recent else None}
        if view != 'status':
            payload.update(manifest=manifest, definition=definition, core_parameters=summary)
        return Response(json.dumps(payload, ensure_ascii=False), media_type='application/json')

    @router.get('/experiments/{experiment_id}')
    def get_experiment_workspace(experiment_id: str, view: str=Query(default='overview', pattern='^(overview|definition)$')):
        return workspace_view(experiment_id, view=view)

    @router.get('/experiments/{experiment_id}/status')
    def get_experiment_status(experiment_id: str):
        return workspace_view(experiment_id, view='status')

    @router.put('/experiments/{experiment_id}')
    def replace_experiment_definition(experiment_id: str, body: ExperimentDefinitionUpdate):
        try:
            result = ctx.workspaces.replace_definition(experiment_id, body.definition, expected_content_sha256=body.expected_content_sha256)
        except WorkspaceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {'id': experiment_id, 'state': 'DRAFT', 'lock_version': 1, 'definition_hash': result['content_sha256']}

    @router.patch('/experiments/{experiment_id}')
    def update_experiment_settings(experiment_id: str, body: ExperimentSettingsUpdate):
        try:
            result = ctx.workspaces.update_settings(experiment_id, body.sections, expected_content_sha256=body.expected_content_sha256)
        except WorkspaceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {'id': experiment_id, 'state': 'DRAFT', 'lock_version': 1, 'definition_hash': result['content_sha256'],
                'definition': {key: result['definition'][key] for key in body.sections}}

    @router.post('/experiments/{experiment_id}/validate')
    @ctx.jobs.action('VALIDATE_EXPERIMENT')
    @ctx.serialized_experiment
    def validate_experiment_workspace(experiment_id: str):
        row = ctx.catalog.get('experiment', experiment_id)
        if row is None:
            raise HTTPException(status_code=404, detail='experiment is not in the Studio package catalog')
        try:
            with open_package(Path(row.location)) as root:
                validate_experiment_directory(root)
        except Exception as exc:
            return {'valid': False, 'definition_hash': row.content_sha256 or '', 'counts': {'blocking': 1, 'warning': 0, 'automatic': 0, 'passed': 0}, 'errors': [{'code': 'PACKAGE_INVALID', 'message': str(exc)}], 'warnings': []}
        return {'valid': True, 'definition_hash': row.content_sha256 or '', 'counts': {'blocking': 0, 'warning': 0, 'automatic': 0, 'passed': 1}, 'errors': [], 'warnings': []}

    @router.get('/experiments/{experiment_id}/estimate')
    def estimate_experiment_run(experiment_id: str):
        row = ctx.catalog.get('experiment', experiment_id)
        if row is None:
            raise HTTPException(status_code=404, detail='experiment is not in the Studio package catalog')
        _manifest, definition, summary = experiment_overview(Path(row.location))
        agents = summary['enabled_agent_count']
        objects = summary['skill_bound_objects']
        steps = int(definition['simulation'].get('max_steps') or 1)
        calls = (agents + objects) * steps
        return {'experiment_id': experiment_id, 'definition_hash': row.content_sha256 or '', 'lock_version': 1, 'basis': '按实验包内 Agent 与绑定 Skill 的对象数量、Step 数和 Skill 调用链估算；实际消耗受重试、上下文规模和模型吞吐影响。', 'scale': {'execution_mode': 'SKILL_BRAIN', 'agents': agents, 'skill_bound_objects': objects, 'steps': steps, 'brain_skill': definition['engine'].get('brain_skill', '')}, 'estimate': {'model_calls': {'low': calls, 'high': calls * 3}, 'tokens': {'low': calls * 500, 'high': calls * 3000}, 'wall_seconds': {'low': calls * 2, 'high': calls * 30}, 'storage_bytes': {'low': calls * 2048, 'high': calls * 32768}}, 'high_scale': calls > 10000, 'threshold_reasons': ['（Agent + Skill 对象）× Step 超过 10000'] if calls > 10000 else []}

    @router.get('/experiments/{experiment_id}/runs')
    def list_experiment_runs(experiment_id: str, page: int=Query(default=1, ge=1), page_size: int=Query(default=5, ge=1, le=100)):
        listing = ctx.catalog.page(package_kind='run', experiment_id=experiment_id, page=page, page_size=page_size)
        return {**listing, 'items': [ctx.run_list_summary(row) for row in listing['items']],
                'next_cursor': page + 1 if page < listing['total_pages'] else None}

    @router.post('/experiments/{experiment_id}/archive')
    def archive_experiment(experiment_id: str):
        metadata = ctx.update_experiment_presentation(experiment_id, {'archived_at': datetime.now(UTC).isoformat()})
        return {'experiment_id': experiment_id, **metadata}

    @router.post('/experiments/{experiment_id}/restore')
    def restore_experiment(experiment_id: str):
        metadata = ctx.update_experiment_presentation(experiment_id, {'archived_at': None})
        return {'experiment_id': experiment_id, **metadata}

    @router.post('/experiments/batch')
    def batch_experiments(body: ExperimentBatchRequest):
        unique_ids = list(dict.fromkeys(body.experiment_ids))
        if not unique_ids:
            raise HTTPException(status_code=422, detail='at least one experiment is required')
        action = body.action
        for experiment_id in unique_ids:
            changes = {'archived_at': datetime.now(UTC).isoformat() if action == 'ARCHIVE' else None}
            ctx.update_experiment_presentation(experiment_id, changes)
        return {'affected': len(unique_ids), 'action': action}

    @router.post('/experiments/{experiment_id}/duplicate', status_code=201)
    @ctx.jobs.action('DUPLICATE_EXPERIMENT')
    def duplicate_experiment(experiment_id: str):
        try:
            return ctx.workspaces.duplicate(experiment_id)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete('/experiments/{experiment_id}', status_code=204)
    @ctx.serialized_experiment
    def delete_experiment(experiment_id: str):
        location = ctx.catalog_location('experiment', experiment_id)
        if location.is_dir():
            shutil.rmtree(location)
        else:
            location.unlink()
        ctx.catalog.delete('experiment', experiment_id)
        document = ctx.read_presentation()
        metadata = document.get('experiments') or {}
        if isinstance(metadata, dict):
            metadata.pop(experiment_id, None)
        ctx.write_presentation(document)
        return Response(status_code=204)

    @router.post('/experiments/{experiment_id}/agent-images', status_code=201)
    async def upload_experiment_agent_images(experiment_id: str, expected_content_sha256: str | None=Form(None), portrait: UploadFile | None=File(None), sprite: UploadFile | None=File(None)):
        uploads = {'portrait': portrait, 'sprite': sprite}
        prepared: dict[str, bytes] = {}
        result: dict[str, dict[str, Any]] = {}
        for kind, upload in uploads.items():
            if upload is None:
                continue
            data = await upload.read()
            if len(data) > 2 * 1024 * 1024:
                raise HTTPException(status_code=422, detail='Agent 图片不能超过 2 MB')
            try:
                width, height = ctx.asset_service._validate_agent_png(data, kind=kind)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            logical = f'assets/agents/uploads/{uuid4().hex}-{kind}.png'
            prepared[logical] = data
            result[kind] = {'kind': kind, 'width': width, 'height': height, 'logical_path': logical, 'content_url': packaged_asset_url(experiment_id, logical)}
        if not prepared:
            raise HTTPException(status_code=422, detail='请至少选择一张 Agent 图片')
        try:
            saved = await run_in_threadpool(ctx.workspaces.add_assets, experiment_id, prepared, expected_content_sha256=expected_content_sha256)
        except WorkspaceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for item in result.values():
            item['content_url'] = f"/api/studio/experiments/{experiment_id}/assets/{item['logical_path'].removeprefix('assets/')}"
        return {**result, 'content_sha256': saved['content_sha256']}

    @router.get('/experiments/{experiment_id}/assets/{asset_path:path}')
    def experiment_asset(experiment_id: str, asset_path: str, request: Request):
        from generative_agents.adapters.web.media import package_asset_response
        return package_asset_response(ctx.catalog_location('experiment', experiment_id),
                                      'assets/' + asset_path, request)

    @router.post('/experiments', status_code=201)
    @ctx.jobs.action('CREATE_EXPERIMENT')
    def create_experiment_workspace(body: ExperimentWorkspaceCreate):
        created = ctx.studio_call(lambda: ctx.workspaces.create(ExperimentSelection(name=body.name, goal=body.goal, key=body.key, timezone=body.timezone, map_id=body.map_id, brain_skill_id=body.brain_skill_id, model_preset_id=body.model_preset_id, embedding_model_preset_id=body.embedding_model_preset_id, agent_ids=tuple(body.agent_ids), crowd_ids=tuple(body.crowd_ids), evaluator_ids=tuple(body.evaluator_ids), placements=tuple((AgentPlacement(agent_id=item.agent_id, coord=item.coord) for item in body.placements)), simulation=body.simulation)))
        metadata = ctx.update_experiment_presentation(created['experiment_id'], {'owner': body.owner.strip(), 'tags': list(dict.fromkeys((value.strip() for value in body.tags if value.strip())))})
        return {**created, **metadata}

    @router.put('/experiments/{experiment_id}/entrypoints/{section}')
    def update_experiment_entrypoint(experiment_id: str, section: str, body: ExperimentEntrypointUpdate):
        if section not in {'world', 'agents', 'models', 'simulation', 'engine', 'evaluation'}:
            raise HTTPException(status_code=404, detail='unknown experiment entrypoint')
        try:
            return ctx.workspaces.update_entrypoint(experiment_id, section, body.document)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post('/experiments/{experiment_id}/seal')
    @ctx.jobs.action('SEAL_EXPERIMENT')
    @ctx.serialized_experiment
    def seal_experiment(experiment_id: str):
        source = ctx.catalog_location('experiment', experiment_id)
        if source.is_file():
            return _catalog_item(ctx.catalog.get('experiment', experiment_id))
        validate_experiment_directory(source)
        archive = ctx.package_root / 'experiments' / f'{experiment_id}.gaexp'
        try:
            seal_directory(source, archive)
            record = ctx.catalog.upsert(archive)
            cleanup_warning = None
            try:
                shutil.rmtree(source)
            except OSError as exc:
                cleanup_warning = f'实验已封存，旧工作目录清理失败：{exc}'
            return {'cleanup_warning': cleanup_warning, 'experiment_id': experiment_id, 'location': str(archive), 'status': 'SEALED', 'content_sha256': record.content_sha256}
        except Exception:
            if source.exists():
                archive.unlink(missing_ok=True)
            raise

    @router.post('/experiments/{experiment_id}/runs')
    @ctx.jobs.action('CREATE_RUN')
    def start_experiment_run(experiment_id: str, steps: int | None=Query(default=None, ge=1)):
        destination = ctx.package_root / 'runs' / f'workspace-{uuid4().hex}'
        try:
            experiment = ctx.experiment_location(experiment_id)
            if not experiment.is_file():
                raise PackageError('seal the experiment before starting a Run')
            run_root = ctx.runtime.create(experiment, destination, requested_steps=steps)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ctx.submit_directory(run_root)
