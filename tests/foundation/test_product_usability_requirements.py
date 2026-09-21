"""基础能力回归测试：覆盖 ``test_product_usability_requirements`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_agent_deletion_is_list_scoped_and_spatial_data_uses_form_tables():
    """回归验证 ``test_agent_deletion_is_list_scoped_and_spatial_data_uses_form_tables`` 所描述的业务结果、故障边界和隔离约束。"""
    html = (ROOT / "src/generative_agents/adapters/web/static/shell/experiment-console.html").read_text(encoding="utf-8")
    source = (ROOT / "src/generative_agents/adapters/web/static/shell/console-api.js").read_text(encoding="utf-8")

    editor = html[html.index('id="agentEditorModal"') : html.index('id="createMapModal"')]
    assert 'id="deleteAgentBtn"' not in editor
    assert "空间定义 JSON" not in editor
    assert 'id="agentAddressRows"' in editor
    assert 'id="agentSpaceRows"' in editor
    assert '<label for="agentEditKey">稳定键</label>' not in editor
    assert '<input id="agentEditKey" type="hidden"' in editor
    assert 'id="agentEditorKeyMeta">文件键：—' in editor
    assert "画像资源引用" not in editor
    assert 'id="agentPortraitFile"' in editor
    assert 'id="agentSpriteFile"' in editor
    assert "4×4 行走图" in editor

    assert 'id="deleteSelectedAgentsBtn"' in html
    assert 'id="deleteAgentsModal"' in html
    assert "function openDeleteSelectedAgents" in source
    assert "function deleteSelectedAgents" in source
    assert "function flattenSpatialTree" in source
    assert "function readSpatialEditor" in source
    assert "const spatial = readSpatialEditor();" in source
    assert "function stageAgentImage" in source
    assert "async function uploadStagedAgentImages" in source
    assert "form.append('portrait'" in source and "form.append('sprite'" in source
