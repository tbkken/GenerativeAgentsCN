"""Read the independent observational quality report for one Run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from generative_agents.persistence.database import Database
from generative_agents.persistence.models import Run, RunEvent
from generative_agents.status import RunStatus

from .errors import not_found


class RunQualityService:
    def __init__(self, database: Database, *, var_dir: str | Path) -> None:
        self._database = database
        self._var_dir = Path(var_dir).resolve()

    def get(self, run_id: str) -> dict[str, Any]:
        with self._database.session_factory() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise not_found("run", run_id)
            status = run.status
            post_processing = session.scalar(
                select(RunEvent)
                .where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == "post_processing",
                )
                .order_by(RunEvent.id.desc())
                .limit(1)
            )
            post_processing_payload = (
                dict(post_processing.payload_json or {})
                if post_processing is not None
                else None
            )
        path = self._var_dir / "runs" / run_id / "quality" / "report.json"
        if path.is_file() and not path.is_symlink():
            return json.loads(path.read_text(encoding="utf-8"))
        terminal = status in {
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }
        processing = bool(
            post_processing_payload
            and post_processing_payload.get("status") == "RUNNING"
        )
        processing_failed = bool(
            post_processing_payload
            and post_processing_payload.get("status") == "FAILED"
        )
        return {
            "schema_version": 1,
            "quality_status": "PENDING" if processing else "NOT_EVALUATED" if terminal else "PENDING",
            "execution_status_affected": False,
            "summary": (
                str(post_processing_payload.get("message"))
                if post_processing_payload
                else "本次运行没有生成质量报告"
                if terminal
                else "运行结束后生成质量报告"
            ),
            "issues": [],
            "evaluator": {
                "status": (
                    "PENDING" if processing else "FAILED" if processing_failed else "NOT_EVALUATED" if terminal else "PENDING"
                )
            },
            "post_processing": post_processing_payload,
        }


__all__ = ["RunQualityService"]
