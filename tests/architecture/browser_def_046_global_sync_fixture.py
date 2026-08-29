"""Controllable production-UI fixture for the DEF-046 fresh Browser gate.

The application and all user-facing routes are production code.  Only the
loopback ``/__qa__`` endpoints are test controls which advance one durable Run
through real scheduler transitions while the Browser keeps the same document.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import psutil
import uvicorn

from generative_agents.config import ExperimentDefinition
from generative_agents.config.bootstrap import make_builtin_definition
from generative_agents.persistence import create_database, upgrade_database
from generative_agents.persistence.models import Run, RunEvent
from generative_agents.runtime.scheduler import LocalRunSchedulerRepository
from generative_agents.services import ExperimentService
from generative_agents.services.runs import RunService
from generative_agents.web import create_app
from tests.support import brain_selection_for_database, publish_user_map


def _definition() -> ExperimentDefinition:
    """为本测试模块封装 ``_definition`` 辅助步骤，减少重复的场景搭建代码。"""
    definition = make_builtin_definition(
        key="def-046-browser-sync",
        name="DEF-046 全局状态同步验收",
        goal="在同一个未刷新页面观察 QUEUED→RUNNING→COMPLETED 全局收敛",
    )
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["simulation"]["max_steps"] = 10
    payload["simulation"]["checkpoint_interval_steps"] = 1
    payload["models"]["chat"]["resolved_model"] = "Qwen/def-046-browser"
    payload["models"]["embedding"]["resolved_model"] = "def-046-embedding"
    payload["agents"] = payload["agents"][:2]
    return ExperimentDefinition.model_validate(payload)


def _seed(root: Path) -> dict[str, str]:
    """为本测试模块封装 ``_seed`` 辅助步骤，减少重复的场景搭建代码。"""
    root.mkdir(parents=True, exist_ok=True)
    var_dir = root / "var"
    database_path = root / "def-046-browser.db"
    database_url = "sqlite:///" + database_path.as_posix()
    upgrade_database(database_url)
    database = create_database(database_url)
    try:
        definition = _definition()
        experiments = ExperimentService(database)
        map_revision = publish_user_map(database, world=definition.world)
        experiment = experiments.create_experiment(
            name=definition.experiment.name,
            goal=definition.experiment.goal,
            source_type="BLANK",
            map_revision_id=map_revision["id"],
            **brain_selection_for_database(database),
        )
        draft = experiments.get_draft(experiment["id"])
        payload = definition.model_dump(mode="json", exclude_none=False)
        payload["experiment"]["key"] = experiment["experiment_key"]
        payload["world"] = draft["definition"]["world"]
        payload["engine"] = draft["definition"]["engine"]
        draft = experiments.update_draft(
            experiment_id=experiment["id"],
            expected_lock_version=draft["lock_version"],
            definition=ExperimentDefinition.model_validate(payload),
        )
        revision = experiments.publish_draft(
            experiment_id=experiment["id"],
            draft_revision_id=draft["id"],
            expected_lock_version=draft["lock_version"],
        )
        run = RunService(database, var_dir=var_dir).create_from_published(
            experiment["id"], revision["id"]
        )
        metadata = {
            "database_url": database_url,
            "var_dir": str(var_dir.resolve()),
            "experiment_id": experiment["id"],
            "run_id": run["run_id"],
            "initial_status": "QUEUED",
        }
        (root / "fixture.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return metadata
    finally:
        database.close()


def _load_or_seed(root: Path) -> dict[str, str]:
    """为本测试模块封装 ``_load_or_seed`` 辅助步骤，减少重复的场景搭建代码。"""
    metadata_path = root / "fixture.json"
    if metadata_path.is_file():
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    return _seed(root)


def main() -> None:
    """为本测试模块封装 ``main`` 辅助步骤，减少重复的场景搭建代码。"""
    root = Path(os.environ["GA_DEF046_QA_ROOT"]).resolve()
    port = int(os.environ.get("GA_DEF046_QA_PORT", "8766"))
    metadata = _load_or_seed(root)
    app = create_app(
        database_url=metadata["database_url"],
        var_dir=metadata["var_dir"],
        migrate=False,
        supervisor_enabled=False,
    )
    # create_app owns its Database inside the ASGI lifespan.  The fixture
    # control plane uses a separate short-transaction handle to the same file.
    database = create_database(metadata["database_url"])
    app.router.add_event_handler("shutdown", database.close)
    scheduler = LocalRunSchedulerRepository(database, max_concurrent_runs=1)

    @app.get("/__qa__/metadata")
    def qa_metadata():
        """为本测试模块封装 ``qa_metadata`` 辅助步骤，减少重复的场景搭建代码。"""
        return {
            **metadata,
            "run": RunService(database, var_dir=metadata["var_dir"]).get_run(
                metadata["run_id"]
            ),
        }

    @app.post("/__qa__/advance/{target}")
    def qa_advance(target: str):
        """为本测试模块封装 ``qa_advance`` 辅助步骤，减少重复的场景搭建代码。"""
        target = target.upper()
        run_id = metadata["run_id"]
        current = RunService(database, var_dir=metadata["var_dir"]).get_run(run_id)
        if current["status"] == target:
            return qa_metadata()
        if target == "RUNNING" and current["status"] == "QUEUED":
            claimed = scheduler.claim_next()
            if claimed is None or claimed.run_id != run_id:
                raise RuntimeError("fixture Run was not claimed")
            process = psutil.Process(os.getpid())
            if not scheduler.register_worker(
                claimed,
                pid=process.pid,
                pid_create_time=process.create_time(),
            ):
                raise RuntimeError("fixture worker registration failed")
            log_path = Path(metadata["var_dir"]) / claimed.log_path
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("INFO DEF-046 Browser fixture entered RUNNING\n", encoding="utf-8")
            with database.session_factory.begin() as session:
                run = session.get(Run, run_id)
                assert run is not None
                run.completed_steps = 5
                run.virtual_time = run.virtual_time + timedelta(
                    minutes=run.stride_minutes * 5
                )
                session.add(
                    RunEvent(
                        run_id=run_id,
                        event_type="progress",
                        payload_json={
                            "status": "RUNNING",
                            "completed_steps": 5,
                            "recoverable_step": 0,
                        },
                    )
                )
            return qa_metadata()
        if target == "COMPLETED" and current["status"] == "RUNNING":
            with database.session_factory.begin() as session:
                run = session.get(Run, run_id)
                assert run is not None and run.current_attempt_id is not None
                attempt_id = run.current_attempt_id
                run.completed_steps = run.requested_steps
                run.virtual_time = run.virtual_time + timedelta(
                    minutes=run.stride_minutes * 5
                )
            if not scheduler.finish_worker(run_id, attempt_id, exit_code=0):
                raise RuntimeError("fixture worker completion failed")
            return qa_metadata()
        raise RuntimeError(
            f"unsupported fixture transition {current['status']} -> {target}"
        )

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
