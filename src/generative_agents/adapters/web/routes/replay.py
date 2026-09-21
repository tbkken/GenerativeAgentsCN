"""Replay HTTP routes."""
from __future__ import annotations
from typing import Any
from fastapi import HTTPException, Query, Response
from generative_agents.ga_replay.api import ReplayReader
from generative_agents.ga_replay.api import read_run_overview
from generative_agents.ga_replay.api import replay_web_manifest, replay_web_step

def install_routes(router, ctx):

    @router.get('/runs/{run_id}/replay/manifest')
    def replay_manifest(run_id: str):
        facts = ctx.read_run_facts(run_id)
        return replay_web_manifest(run_id, facts)

    @router.get('/runs/{run_id}/replay/availability')
    def replay_availability(run_id: str):
        summary, _status, _quality = read_run_overview(ctx.run_location(run_id))
        return {'run_id': run_id, 'available_step': summary['committed_step'], 'partial': summary['status'] != 'COMPLETED'}

    @router.get('/runs/{run_id}/replay/steps')
    def replay_steps(run_id: str, from_step: int=Query(default=1, ge=1), limit: int=Query(default=100, ge=1, le=100)):
        facts = ctx.read_run_facts(run_id)
        frames = facts['frames']
        selected = [frame for frame in frames if frame['step_no'] >= from_step][:limit]
        previous_attempt = None
        for frame in frames:
            if frame['step_no'] >= from_step:
                break
            previous_attempt = frame.get('attempt_id')
        steps = []
        current_attempt = previous_attempt
        for frame in selected:
            attempt = frame.get('attempt_id')
            steps.append(replay_web_step(frame, checkpoint=frame['step_no'] in facts['checkpoint_steps'], attempt_boundary=current_attempt != attempt))
            current_attempt = attempt
        world_state: dict[str, Any] = {}
        for frame in frames:
            if frame['step_no'] >= from_step:
                break
            for event in frame.get('domain_events') or []:
                if event.get('event_type') != 'GAME_OBJECT_STATE_CHANGED':
                    continue
                payload = event.get('payload') or {}
                structured = payload.get('structured_payload') or {}
                object_key = structured.get('object_key') or structured.get('object_id')
                if object_key:
                    world_state[str(object_key)] = structured.get('after') or structured.get('state') or structured
        return {'run_id': run_id, 'available_step': facts['summary']['committed_step'], 'result_version': facts['summary']['committed_step'], 'world_state_before': world_state, 'steps': steps}

    @router.get('/runs/{run_id}/replay')
    def replay_summary(run_id: str):
        with ReplayReader(ctx.run_location(run_id)) as replay:
            return replay.summary()

    @router.get('/runs/{run_id}/replay/world')
    def replay_world(run_id: str):
        with ReplayReader(ctx.run_location(run_id)) as replay:
            return replay.experiment_world()

    @router.get('/runs/{run_id}/replay/semantic-index')
    def replay_semantic_index(run_id: str):
        with ReplayReader(ctx.run_location(run_id)) as replay:
            return replay.semantic_index()

    @router.get('/runs/{run_id}/replay/assets/{asset_path:path}')
    def replay_asset(run_id: str, asset_path: str):
        try:
            with ReplayReader(ctx.run_location(run_id)) as replay:
                content, media_type = replay.asset(f'assets/{asset_path}')
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail='Replay asset does not exist') from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(content=content, media_type=media_type)

    @router.get('/runs/{run_id}/replay/timeline')
    def replay_timeline(run_id: str, start: int=Query(default=1, ge=1), end: int | None=Query(default=None, ge=1)):
        with ReplayReader(ctx.run_location(run_id)) as replay:
            return {'items': replay.timeline(start=start, end=end)}

    @router.get('/runs/{run_id}/replay/state/{step_no}')
    def replay_state(run_id: str, step_no: int):
        try:
            with ReplayReader(ctx.run_location(run_id)) as replay:
                return replay.state_at(step_no)
        except (IndexError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
