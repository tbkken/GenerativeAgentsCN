from __future__ import annotations

from fastapi.testclient import TestClient

from generative_agents.web import create_app


def test_builtin_brain_is_an_immutable_stanford_town_baseline(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        catalog = client.get("/api/v1/brains?page=1&page_size=5")
        assert catalog.status_code == 200, catalog.text
        data = catalog.json()
        assert data["total"] == 1
        brain = data["items"][0]
        assert brain["brain_key"] == "stanford-town"
        assert brain["name"] == "斯坦福小镇"
        assert brain["is_builtin"] is True
        assert brain["current_draft"] is None
        assert brain["current_published"]["state"] == "PUBLISHED"
        revision_id = brain["current_published"]["id"]

        listing = client.get(
            f"/api/v1/brains/{brain['id']}/revisions/{revision_id}/workflows"
        )
        assert listing.status_code == 200, listing.text
        assert listing.json()["readonly"] is True
        assert len(listing.json()["items"]) == 5

        fork = client.post(
            f"/api/v1/brains/{brain['id']}/revisions/{revision_id}/fork"
        )
        assert fork.status_code == 409
        assert fork.json()["error"]["code"] == "BUILTIN_BRAIN_IMMUTABLE"

        experiment = client.post(
            "/api/v1/experiments",
            json={"name": "默认大脑实验", "source": {"type": "BUILTIN_DEFAULT"}},
        )
        assert experiment.status_code == 201, experiment.text
        experiment_id = experiment.json()["id"]
        draft = client.get(f"/api/v1/experiments/{experiment_id}/draft").json()
        assert draft["provenance"]["brain_id"] == brain["id"]
        assert draft["provenance"]["brain_revision_id"] == revision_id
        workflows = client.get(
            f"/api/v1/experiments/{experiment_id}/draft/workflows"
        ).json()
        assert len(workflows["items"]) == 5


def test_brain_copy_edit_publish_and_apply_to_experiment(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        baseline = client.get("/api/v1/brains").json()["items"][0]
        source_revision_id = baseline["current_published"]["id"]
        created = client.post(
            "/api/v1/brains",
            json={
                "name": "社区经营者",
                "description": "基于斯坦福小镇创作",
                "source_revision_id": source_revision_id,
            },
        )
        assert created.status_code == 201, created.text
        brain = created.json()
        assert brain["is_builtin"] is False
        assert brain["current_draft"]["state"] == "DRAFT"

        draft = client.get(f"/api/v1/brains/{brain['id']}/draft").json()
        schedule = client.get(
            f"/api/v1/brains/{brain['id']}/draft/workflows/schedule"
        ).json()
        workflow = schedule["workflow"]
        workflow["description"] = "社区经营者的日程基准"
        saved = client.put(
            f"/api/v1/brains/{brain['id']}/draft/workflows/schedule",
            json={
                "lock_version": draft["lock_version"],
                "workflow": workflow,
                "prompts": {
                    key: value["content"] for key, value in schedule["prompts"].items()
                },
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["lock_version"] == draft["lock_version"] + 1

        published = client.post(
            f"/api/v1/brains/{brain['id']}/draft/publish",
            json={
                "draft_revision_id": draft["id"],
                "lock_version": saved.json()["lock_version"],
            },
        )
        assert published.status_code == 200, published.text
        brain_revision = published.json()

        experiment = client.post(
            "/api/v1/experiments",
            json={"name": "大脑模板实验", "source": {"type": "BUILTIN_DEFAULT"}},
        ).json()
        experiment_draft = client.get(
            f"/api/v1/experiments/{experiment['id']}/draft"
        ).json()
        applied = client.put(
            f"/api/v1/experiments/{experiment['id']}/draft/brain",
            json={
                "lock_version": experiment_draft["lock_version"],
                "brain_revision_id": brain_revision["id"],
            },
        )
        assert applied.status_code == 200, applied.text
        applied_draft = applied.json()
        assert applied_draft["provenance"]["brain_id"] == brain["id"]
        assert applied_draft["provenance"]["brain_revision_id"] == brain_revision["id"]

        applied_listing = client.get(
            f"/api/v1/experiments/{experiment['id']}/draft/workflows"
        )
        assert applied_listing.status_code == 200, applied_listing.text
        assert len(applied_listing.json()["items"]) == 5
        applied_schedule = client.get(
            f"/api/v1/experiments/{experiment['id']}/draft/workflows/schedule"
        ).json()
        assert applied_schedule["workflow"]["description"] == "社区经营者的日程基准"

        baseline_after = client.get(
            f"/api/v1/brains/{baseline['id']}/revisions/{source_revision_id}/workflows/schedule"
        ).json()
        assert baseline_after["workflow"]["description"] != "社区经营者的日程基准"

        saved_as = client.post(
            f"/api/v1/experiments/{experiment['id']}/brain-template",
            json={
                "name": "实验沉淀大脑",
                "description": "从实验中的独立编排另存",
                "revision_id": applied_draft["id"],
            },
        )
        assert saved_as.status_code == 201, saved_as.text
        saved_brain = saved_as.json()
        assert saved_brain["current_draft"]["state"] == "DRAFT"
        saved_schedule = client.get(
            f"/api/v1/brains/{saved_brain['id']}/draft/workflows/schedule"
        ).json()
        assert saved_schedule["workflow"]["description"] == "社区经营者的日程基准"


def test_brain_catalog_uses_five_item_pages(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        source = client.get("/api/v1/brains").json()["items"][0]["current_published"]["id"]
        for index in range(6):
            response = client.post(
                "/api/v1/brains",
                json={"name": f"分页大脑 {index + 1}", "source_revision_id": source},
            )
            assert response.status_code == 201, response.text
        first = client.get("/api/v1/brains?page=1&page_size=5").json()
        second = client.get("/api/v1/brains?page=2&page_size=5").json()
        assert len(first["items"]) == 5
        assert second["items"]
        assert first["status_counts"]["ALL"] == first["total"]


def test_brain_workspace_exposes_template_and_experiment_entry_points():
    html = (
        __import__("pathlib").Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "experiment-console.html"
    ).read_text(encoding="utf-8")
    javascript = (
        __import__("pathlib").Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "brain-workspace.js"
    ).read_text(encoding="utf-8")

    assert 'data-page="brains"' in html
    assert '<span class="nav-text">大脑</span>' in html
    assert '<span class="nav-text">大脑编排</span>' in html
    assert 'id="newExperimentBrain"' in html
    assert 'id="newExperimentMap"' in html
    assert 'id="newExperimentCrowds"' in html
    assert 'id="quickExperienceBtn"' not in html
    assert 'id="newExperimentAgentPreset"' not in html
    assert "选择实验的配置起点" not in html
    assert 'id="copyRevisionSelect"' not in html
    assert 'id="saveExperimentBrainTemplateBtn"' in html
    assert "prepareExperimentCreate" in javascript
    assert "/brain-template" in javascript
    assert "brain-editor-mode" in javascript

    brain_css = (
        __import__("pathlib").Path(__file__).parents[2]
        / "generative_agents"
        / "web"
        / "static"
        / "brain-workspace.css"
    ).read_text(encoding="utf-8")
    assert "body.brain-editor-mode .content" in brain_css
    assert "body.brain-mode .content" not in brain_css
