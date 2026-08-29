"""基础能力回归测试：覆盖 ``test_map_workspace_frontend`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import subprocess
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from generative_agents.web.app import create_app


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "generative_agents" / "web" / "static"


class _IdParser(HTMLParser):
    """为 ``_IdParser`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self) -> None:
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """为本测试模块封装 ``handle_starttag`` 辅助步骤，减少重复的场景搭建代码。"""
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"] or "")


def test_public_map_workspace_is_a_first_class_console_surface():
    """回归验证 ``test_public_map_workspace_is_a_first_class_console_surface`` 所描述的业务结果、故障边界和隔离约束。"""
    shell = (STATIC / "experiment-console.html").read_text(encoding="utf-8")
    parser = _IdParser()
    parser.feed(shell)

    assert len(parser.ids) == len(set(parser.ids))
    assert 'data-page="experiments"' in shell
    assert 'data-page="maps"' in shell
    assert 'id="page-maps"' in shell
    assert 'id="publicMapEditor"' in shell
    assert 'id="experimentMapEditor"' not in shell
    assert 'id="experimentMapRevisionSelect"' in shell
    assert 'id="tuneExperimentMapBtn"' not in shell
    assert 'id="mapStatusFilters"' in shell
    assert 'data-map-filter="draft"' in shell
    assert 'id="mapPagination"' not in shell
    assert 'id="createMapBtn" hidden' in shell
    assert shell.count("map-workspace.js") == 1
    assert shell.count("map-workspace.css") == 1


def test_map_workspace_edits_public_revisions_and_experiments_only_select_them():
    """地图编辑只属于资源中心；实验页只保留 Revision 选择。"""
    source = (STATIC / "map-workspace.js").read_text(encoding="utf-8")
    editor = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")

    assert "class GridEditor" not in source
    assert "pointermove" in editor
    assert "event.deltaY" in editor
    assert "/draft/map-overlay" not in source
    assert "experimentMapRevisionSelect" in source
    assert "/draft/map`" in source
    assert "this.publicEditor.resize();" in source
    assert "this.publicEditor.fit();" in source
    assert "page_size: '100'" in source
    assert "data-map-page" not in source
    assert "status_counts" in source
    assert "window.prompt" not in source
    subprocess.run(
        ["node", "--check", str(STATIC / "map-workspace.js")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_public_map_editor_auto_saves_and_keeps_a_local_recovery_copy():
    """回归验证 ``test_public_map_editor_auto_saves_and_keeps_a_local_recovery_copy`` 所描述的业务结果、故障边界和隔离约束。"""
    shell = (STATIC / "experiment-console.html").read_text(encoding="utf-8")
    workspace = (STATIC / "map-workspace.js").read_text(encoding="utf-8")
    editor = (STATIC / "map-editor-v2.js").read_text(encoding="utf-8")

    assert 'id="mapAutosaveStatus"' in shell
    assert 'aria-live="polite"' in shell
    assert "const MAP_AUTO_SAVE_DELAY_MS = 1200" in workspace
    assert "map-editor-v2:change" in workspace
    assert "localStorage.setItem" in workspace
    assert "baseLockVersion" in workspace
    assert "window.addEventListener('beforeunload'" in workspace
    assert "if (this.publicEditor.changed || this.savePromise)" in workspace
    assert "acceptSavedWorld(saved.world, editorRevision)" in workspace
    assert "get changeRevision()" in editor
    assert "this.root.dispatchEvent(new CustomEvent('map-editor-v2:change'" in editor


def test_readonly_map_can_create_a_draft_and_continue_with_a_new_canvas():
    """回归验证 ``test_readonly_map_can_create_a_draft_and_continue_with_a_new_canvas`` 所描述的业务结果、故障边界和隔离约束。"""
    workspace = (STATIC / "map-workspace.js").read_text(encoding="utf-8")

    assert "map-editor-v2:request-edit" in workspace
    assert "handlePublicEditorEditRequest(event)" in workspace
    assert "await this.publishOrFork();" in workspace
    assert "modal('open', 'createCanvasModal', 'newCanvasName')" in workspace
    assert "this.publicEditor.createMaterialCanvas({ name, width, height });" in workspace
    assert "画布宽高必须是 1–256 的整数" in workspace
    assert "editTransitionPromise" in workspace


def test_packaged_map_workspace_assets_are_served(database_url):
    """回归验证 ``test_packaged_map_workspace_assets_are_served`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        page = client.get("/?view=maps")
        css = client.get("/static/console/map-workspace.css")
        script = client.get("/static/console/map-workspace.js")

    assert page.status_code == 200
    assert css.status_code == 200
    assert script.status_code == 200
    assert "地图" in page.text
    assert ".map-editor-layout" in css.text
    assert "window.MapWorkspace" in script.text
