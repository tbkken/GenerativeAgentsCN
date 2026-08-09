"""Run-owned, windowed Replay V2 queries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from generative_agents.config import ExperimentDefinition
from generative_agents.persistence.database import Database
from generative_agents.persistence.models import (
    ExperimentRevision,
    Run,
    RunResultSummary,
    RunStep,
)
from generative_agents.runtime.replay_v2 import build_replay_v2, validate_replay_v2

from .errors import ServiceError, not_found
from .replay_frames import VerifiedRunFrameReader


class ReplayService:
    """Expose one validated Replay V2 contract without loading an entire Run."""

    def __init__(self, database: Database, *, var_dir: str | Path):
        self._database = database
        self._frames = VerifiedRunFrameReader(var_dir)

    def manifest(self, run_id: str) -> dict[str, Any]:
        context = self._context(run_id)
        for row in context["rows"]:
            self._frames.read(context["run"], row, materialize=False)
        document = build_replay_v2(
            run_id=run_id,
            revision_id=context["revision_id"],
            definition_hash=context["definition_hash"],
            definition=context["definition"],
            source_step=context["available_step"],
            partial=context["partial"],
            results=(),
        )
        document.pop("steps")
        return document

    def steps(
        self, run_id: str, *, from_step: int = 1, limit: int = 100
    ) -> dict[str, Any]:
        if from_step < 1:
            raise ServiceError(
                "INVALID_REPLAY_FROM_STEP", "from_step 必须大于零", status_code=422
            )
        if limit < 1 or limit > 100:
            raise ServiceError(
                "INVALID_REPLAY_WINDOW", "limit 必须在 1 到 100 之间", status_code=422
            )
        with self._database.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise not_found("run", run_id)
            revision = session.get(ExperimentRevision, run.revision_id)
            if revision is None:
                raise ServiceError(
                    "REPLAY_REVISION_MISSING", "Run 的 Revision 不存在", status_code=500
                )
            summary = session.get(RunResultSummary, run_id)
            available = summary.available_step if summary else 0
            result_version = summary.result_version if summary else 0
            partial = summary is None or summary.result_state != "COMPLETE"
            rows = list(
                session.scalars(
                    select(RunStep)
                    .where(
                        RunStep.run_id == run_id,
                        RunStep.step_no >= from_step,
                        RunStep.step_no <= available,
                    )
                    .order_by(RunStep.step_no)
                    .limit(limit)
                )
            )
            expected_end = min(available, from_step + limit - 1)
            expected_steps = (
                list(range(from_step, expected_end + 1))
                if from_step <= expected_end
                else []
            )
            if [row.step_no for row in rows] != expected_steps:
                raise ServiceError(
                    "REPLAY_FRAME_INDEX_INCOMPLETE",
                    "Replay 提交帧索引不完整",
                    status_code=500,
                )
            previous_attempt_id = None
            if from_step > 1:
                previous_attempt_id = session.scalar(
                    select(RunStep.attempt_id).where(
                        RunStep.run_id == run_id,
                        RunStep.step_no == from_step - 1,
                    )
                )
            definition = ExperimentDefinition.model_validate(revision.definition_json)
            revision_id = revision.id
            definition_hash = revision.definition_hash
            # The detached row retains only immutable scalar ownership fields.
            session.expunge(run)

        results = [self._frames.read(run, row) for row in rows]
        document = build_replay_v2(
            run_id=run_id,
            revision_id=revision_id,
            definition_hash=definition_hash,
            definition=definition,
            source_step=available,
            partial=partial,
            results=results,
            checkpoint_steps=(row.step_no for row in rows if row.checkpoint),
            previous_attempt_id=previous_attempt_id,
        )
        # Both live windows and immutable artifacts pass through the same V2
        # validator.  The transport wrapper only adds cursor/version facts.
        validated = validate_replay_v2(document)
        next_from = (
            rows[-1].step_no + 1
            if rows and rows[-1].step_no < available and len(rows) == limit
            else None
        )
        return {
            "run_id": run_id,
            "source_step": available,
            "available_step": available,
            "result_version": result_version,
            "from_step": from_step,
            "next_from_step": next_from,
            "partial": partial,
            "steps": validated["steps"],
        }

    def _context(self, run_id: str) -> dict[str, Any]:
        with self._database.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise not_found("run", run_id)
            revision = session.get(ExperimentRevision, run.revision_id)
            if revision is None:
                raise ServiceError(
                    "REPLAY_REVISION_MISSING", "Run 的 Revision 不存在", status_code=500
                )
            summary = session.get(RunResultSummary, run_id)
            rows = list(
                session.scalars(
                    select(RunStep)
                    .where(
                        RunStep.run_id == run_id,
                        RunStep.step_no <= (summary.available_step if summary else 0),
                    )
                    .order_by(RunStep.step_no)
                )
            )
            available_step = summary.available_step if summary else 0
            if [row.step_no for row in rows] != list(range(1, available_step + 1)):
                raise ServiceError(
                    "REPLAY_FRAME_INDEX_INCOMPLETE",
                    "Replay 提交帧索引不完整",
                    status_code=500,
                )
            session.expunge(run)
            return {
                "run": run,
                "rows": rows,
                "revision_id": revision.id,
                "definition_hash": revision.definition_hash,
                "definition": ExperimentDefinition.model_validate(revision.definition_json),
                "available_step": available_step,
                "partial": summary is None or summary.result_state != "COMPLETE",
            }
