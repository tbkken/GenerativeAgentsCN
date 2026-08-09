from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select

from generative_agents.config import make_builtin_definition
from generative_agents.persistence.models import (
    BuiltinCatalogSnapshot,
    ExperimentRevision,
    LegacyImport,
    Run,
    RunAgentStep,
    RunAgentSummary,
    RunArtifact,
    RunAttempt,
    RunConversation,
    RunResultSummary,
    RunStep,
)
from generative_agents.services import ExperimentService
from generative_agents.services.legacy_import import LegacyImportService


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_catalog_bootstrap_is_idempotent_and_drives_new_drafts(database, tmp_path):
    project = tmp_path / "project"
    package = project / "generative_agents"
    (package / "data" / "prompts").mkdir(parents=True)
    (package / "frontend" / "static" / "assets" / "village").mkdir(parents=True)
    (package / "data" / "config.json").write_text("{}", encoding="utf-8")
    # The definition factory remains package-owned; the temporary tree only proves
    # source fingerprint idempotency without mutating the repository fixture.
    importer = LegacyImportService(database, project_root=project, var_dir=tmp_path / "var")
    first = importer.bootstrap_catalog(apply=True)
    second = importer.bootstrap_catalog(apply=True)
    assert first["created"] == 1
    assert second["skipped"] == 1

    created = ExperimentService(database).create_experiment(
        name="Catalog owned", source_type="BUILTIN_DEFAULT"
    )
    draft = ExperimentService(database).get_draft(created["id"])
    assert draft["provenance"]["catalog_snapshot_id"] == first["catalog_snapshot_id"]
    with database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(BuiltinCatalogSnapshot)) == 1


def test_legacy_run_import_is_partial_idempotent_and_queryable(database, tmp_path):
    source = tmp_path / "legacy"
    checkpoint = source / "checkpoints" / "sample"
    definition = make_builtin_definition(key="fixture", name="fixture")
    agent = definition.agents[0]
    document = {
        "stride": 10,
        "time": "20250213-09:30",
        "step": 1,
        "agent_base": {},
        "agents": {
            agent.name: {
                "coord": [7, 9],
                "currently": "testing legacy import",
                "action": {
                    "event": {
                        "address": ["the Ville", "lab"],
                        "describe": "inspect old result",
                        "emoji": "check",
                    }
                },
            }
        },
    }
    _write_json(checkpoint / "simulate-20250213-0930.json", document)
    _write_json(
        checkpoint / "conversation.json",
        {
            "20250213-09:30": [
                {
                    f"{agent.name} -> {definition.agents[1].name} @ the Ville, lab": [
                        [agent.name, "hello"],
                        [definition.agents[1].name, "hi"],
                    ]
                }
            ]
        },
    )
    compressed = source / "compressed" / "sample"
    # The legacy trees may contain the same filename. They must remain distinct
    # logical artifacts instead of violating the run artifact identity key.
    _write_json(compressed / "conversation.json", {"compressed": True})
    importer = LegacyImportService(database, project_root=tmp_path, var_dir=tmp_path / "var")
    dry_run = importer.import_runs(apply=False, source_root=source)
    applied = importer.import_runs(apply=True, source_root=source)
    repeated = importer.import_runs(apply=True, source_root=source)
    assert dry_run["created"] == 1
    assert applied["created"] == 1 and applied["failed"] == 0, applied
    assert repeated["skipped"] == 1

    run_id = applied["items"][0]["run_id"]
    with database.session_factory() as session:
        run = session.get(Run, run_id)
        revision = session.get(ExperimentRevision, run.revision_id)
        summary = session.get(RunResultSummary, run_id)
        step = session.scalar(select(RunAgentStep).where(RunAgentStep.run_id == run_id))
        run_step = session.scalar(select(RunStep).where(RunStep.run_id == run_id))
        agent_summary = session.get(RunAgentSummary, (run_id, agent.agent_key))
        attempt = session.scalar(select(RunAttempt).where(RunAttempt.run_id == run_id))
        artifact_names = set(
            session.scalars(
                select(RunArtifact.logical_name).where(RunArtifact.run_id == run_id)
            )
        )
        assert run.status == "COMPLETED"
        assert revision.snapshot_complete is False
        assert revision.provenance_json["source_type"] == "LEGACY_RUN"
        assert summary.result_state == "PARTIAL"
        assert summary.projection_version == "legacy-v1"
        assert summary.capabilities_json["memories"]["state"] == "UNAVAILABLE"
        assert step.path_source == "RECONSTRUCTED"
        assert run_step.conversation_count == 1
        assert run_step.message_count == 2
        assert agent_summary.conversation_count == 1
        assert agent_summary.message_count == 2
        assert artifact_names >= {
            "checkpoints/conversation.json",
            "compressed/conversation.json",
        }
        assert (tmp_path / "var" / attempt.log_path).is_file()
        assert session.scalar(select(func.count()).select_from(RunConversation)) == 1
        assert session.scalar(select(func.count()).select_from(LegacyImport)) == 1
