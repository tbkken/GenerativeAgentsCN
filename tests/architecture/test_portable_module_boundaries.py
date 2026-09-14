from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from generative_agents.ga_studio.schema import prepare_studio_database
from generative_agents.ga_studio.web import create_studio_app
from generative_agents.persistence import create_database, upgrade_database


ROOT = Path(__file__).resolve().parents[2] / "generative_agents"


def _python_sources(module: str) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / module).rglob("*.py"))
    )


def test_protocol_runtime_and_replay_do_not_import_studio_persistence() -> None:
    for module in ("ga_protocol", "ga_runtime", "ga_replay"):
        source = _python_sources(module)
        assert "generative_agents.persistence" not in source
        assert "generative_agents.ga_studio" not in source
        assert "from sqlalchemy" not in source
        assert "import sqlalchemy" not in source


def test_replay_does_not_import_models_brain_or_skill_runtime() -> None:
    source = _python_sources("ga_replay")
    assert "BrainRuntime" not in source
    assert "SkillRuntime" not in source
    assert "llm_model" not in source


def test_runtime_memory_is_a_file_protocol_not_sqlite() -> None:
    source = _python_sources("ga_runtime")
    assert "sqlite3" not in source
    assert ".sqlite" not in source


def test_clean_database_baseline_contains_only_studio_business_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(database_url)
    database = create_database(database_url)
    try:
        tables = set(inspect(database.engine).get_table_names())
    finally:
        database.close()
    assert {
        "studio_agents",
        "studio_crowds",
        "studio_evaluators",
        "studio_model_presets",
        "studio_package_catalog",
        "studio_skills",
        "world_maps",
        "spatial_asset_definitions",
        "assets",
        "secrets",
    } <= tables
    assert not {
        "experiments",
        "experiment_revisions",
        "runs",
        "run_attempts",
        "run_steps",
        "run_step_effects",
        "run_conversations",
    } & tables


def test_incompatible_local_database_is_backed_up_and_rebuilt(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO alembic_version(version_num) VALUES (?)",
            ("0001_mutable_resource_baseline",),
        )
        connection.execute("CREATE TABLE runs (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO runs(id) VALUES ('legacy-run')")
        connection.commit()
    finally:
        connection.close()

    result = prepare_studio_database(
        f"sqlite:///{database_path.as_posix()}",
        backup_dir=tmp_path / "backups",
    )

    assert result.rebuilt is True
    assert result.backup_path is not None and result.backup_path.is_file()
    backup = sqlite3.connect(result.backup_path)
    try:
        assert backup.execute("SELECT id FROM runs").fetchone() == ("legacy-run",)
    finally:
        backup.close()

    database = create_database(f"sqlite:///{database_path.as_posix()}")
    try:
        tables = set(inspect(database.engine).get_table_names())
    finally:
        database.close()
    assert "studio_skills" in tables
    assert "runs" not in tables


def test_package_studio_root_serves_functional_management_console(tmp_path: Path) -> None:
    database_path = tmp_path / "studio.sqlite"
    app = create_studio_app(
        database_url=f"sqlite:///{database_path.as_posix()}",
        var_dir=tmp_path / "var",
    )
    with TestClient(app) as client:
        page = client.get("/")
        script = client.get("/static/console/console-api.js")
        health = client.get("/api/studio/health")
        spatial_assets = client.get("/api/studio/resources/spatial-assets")

    assert page.status_code == 200
    assert "Agent Foundry" in page.text
    assert "Research Console" in page.text
    assert 'id="page-experiments"' in page.text
    assert 'id="page-maps"' in page.text
    assert 'id="page-results"' in page.text
    assert script.status_code == 200
    assert "/api/studio" in script.text
    assert health.json()["runtime_truth"] == "files"
    assert spatial_assets.status_code == 200
    assert spatial_assets.json()["total"] >= 1


def test_agent_manager_exposes_one_user_owned_public_catalog() -> None:
    static_root = ROOT / "web" / "static"
    html = (static_root / "experiment-console.html").read_text(encoding="utf-8")
    workspace = (static_root / "crowd-workspace.js").read_text(encoding="utf-8")

    assert "系统公共 Agent" not in html
    assert "系统 Agent" not in html
    assert "新建自定义 Agent" not in html
    assert "系统公共 Agent" not in workspace
    assert "SYSTEM AGENT" not in workspace
    assert "item.is_builtin" not in workspace
    assert "内置头像" not in workspace
    assert "内置行走图" not in workspace
