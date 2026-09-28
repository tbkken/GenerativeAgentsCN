"""Replay HTTP routes."""
from __future__ import annotations
from typing import Any
from fastapi import HTTPException, Query, Response, Request
from generative_agents.adapters.web.media import package_asset_response
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.reading import read_package_json
from generative_agents.ga_replay.api import ReplayReader
from generative_agents.ga_replay.api import read_run_overview
from generative_agents.ga_replay.api import replay_web_manifest, replay_web_step
from generative_agents.ga_replay.api import read_slice, read_metadata, read_state, read_run_status

def install_routes(router, ctx):

    @router.get('/runs/{run_id}/replay/manifest')
    def replay_manifest(run_id: str):
        facts = read_slice(ctx.run_location(run_id), include_frames=False)
        return replay_web_manifest(run_id, facts)

    @router.get('/runs/{run_id}/replay/availability')
    def replay_availability(run_id: str):
        _manifest, status = read_run_status(ctx.run_location(run_id))
        return {'run_id': run_id, 'available_step': status.committed_step, 'partial': status.status.value != 'COMPLETED'}

    @router.get('/runs/{run_id}/replay/steps')
    def replay_steps(run_id: str, from_step: int=Query(default=1, ge=1), limit: int=Query(default=100, ge=1, le=100), include_audit: bool=False, include_baseline: bool=True):
        location = ctx.run_location(run_id)
        facts = read_slice(location, start=from_step, end=from_step+limit-1, include_definition=False)
        selected = facts['frames']
        before = read_state(location, min(from_step-1, facts['summary']['committed_step'])) if from_step > 1 and include_baseline else {}
        previous_attempt = before.get('attempt_id')
        steps = []
        current_attempt = previous_attempt
        for frame in selected:
            attempt = frame.get('attempt_id')
            step = replay_web_step(frame, checkpoint=frame['step_no'] in facts['checkpoint_steps'], attempt_boundary=current_attempt != attempt)
            if not include_audit:
                step.pop('effects', None)
                step.pop('memory_deltas', None)
                step.pop('schedule_revisions', None)
                for agent in step['agents']:
                    agent.pop('decision_context', None)
            steps.append(step)
            current_attempt = attempt
        world_state = before.get('object_states') or {}
        return {'run_id': run_id, 'available_step': facts['summary']['committed_step'], 'result_version': facts['summary']['committed_step'], 'world_state_before': world_state, 'steps': steps}

    @router.get('/runs/{run_id}/replay')
    def replay_summary(run_id: str):
        return read_metadata(ctx.run_location(run_id))[0]

    @router.get('/runs/{run_id}/replay/world')
    def replay_world(run_id: str):
        with ReplayReader(ctx.run_location(run_id)) as replay:
            return replay.experiment_world()

    @router.get('/runs/{run_id}/replay/semantic-index')
    def replay_semantic_index(run_id: str):
        with ReplayReader(ctx.run_location(run_id)) as replay:
            return replay.semantic_index()

    @router.get('/runs/{run_id}/replay/assets/{asset_path:path}')
    def replay_asset(run_id: str, asset_path: str, request: Request):
        try:
            location = ctx.run_location(run_id)
            manifest, _status = read_run_status(location)
            prefix = manifest.experiment.path
            integrity = read_package_json(location, f'{prefix}/integrity/sha256.json')
            if integrity.get('root_sha256') != manifest.experiment.root_sha256:
                raise PackageError('embedded experiment hash does not match Run manifest')
            return package_asset_response(location, f'{prefix}/assets/{asset_path}', request, integrity_prefix=prefix)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail='Replay asset does not exist') from exc
        except HTTPException:
            raise
        except PackageError:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get('/runs/{run_id}/replay/timeline')
    def replay_timeline(run_id: str, start: int=Query(default=1, ge=1), end: int | None=Query(default=None, ge=1), limit: int=Query(default=100, ge=1, le=100)):
        with ReplayReader(ctx.run_location(run_id)) as replay:
            return {'items': replay.timeline(start=start, end=min(end or start+limit-1, start+limit-1))}

    @router.get('/runs/{run_id}/replay/state/{step_no}')
    def replay_state(run_id: str, step_no: int):
        try:
            return read_state(ctx.run_location(run_id), step_no)
        except (IndexError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
