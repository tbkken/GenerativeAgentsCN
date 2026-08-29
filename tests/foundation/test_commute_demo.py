"""基础能力回归测试：覆盖 ``test_commute_demo`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from generative_agents.web import create_app


def test_two_day_commute_demo_uses_real_experiment_pages_and_cross_day_results(database_url):
    """回归验证 ``test_two_day_commute_demo_uses_real_experiment_pages_and_cross_day_results`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)

    with TestClient(app) as client:
        demo = client.get("/demos/two-day-commute")
        script = client.get("/static/console/commute-demo.js")
        style = client.get("/static/console/commute-demo.css")
        shared_map_script = client.get("/static/console/commute-map.js")
        shared_style = client.get("/static/console/product-demo.css")
        shell = client.get("/")

    assert demo.status_code == 200
    assert demo.headers["cache-control"] == "no-store"
    assert script.status_code == 200
    assert style.status_code == 200
    assert shared_map_script.status_code == 200
    assert shared_style.status_code == 200
    assert 'class="app-shell"' in demo.text
    assert 'id="experimentNavigation"' in demo.text
    assert 'id="autoDemo"' in demo.text
    assert 'class="demo-controller"' in demo.text
    assert "实验配置 · 1/10" in demo.text
    assert "commute-map.js" in demo.text
    assert "新建实验" in script.text
    assert "世界与地图" not in demo.text
    assert "大脑编排" not in demo.text
    assert "行为参数" not in demo.text
    assert "参与 Agent" in demo.text
    assert "模型与运行" in demo.text
    assert "实验结果" in demo.text
    assert "资源组合" in script.text
    assert "Brain Revision" in script.text
    assert "不创建实验覆盖层" in script.text
    assert "tool.car-01" in script.text
    assert "controller_agent_id" in script.text
    assert "company.vehicle.enter" in script.text
    assert "worldSharedMap" in script.text
    assert "replaySharedMap" in script.text
    assert "window.CommuteMap.render" in script.text
    assert "StepResult 是唯一事实来源" in script.text
    assert "周一 08:47" in script.text
    assert "周二 07:54" in script.text
    assert "路口 A 红灯停车" in script.text
    assert "停入 P03" in script.text
    assert "Event(SPO) + structured_payload" in script.text
    assert "commute-home-office" in shared_map_script.text
    assert "map-rev-commute-001" in shared_map_script.text
    assert "URLSearchParams" in script.text
    assert "demo-steps" not in demo.text
    assert "/demos/two-day-commute?autoplay=1" in shell.text
