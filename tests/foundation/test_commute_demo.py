from __future__ import annotations

from fastapi.testclient import TestClient

from generative_agents.web import create_app


def test_two_day_commute_demo_uses_real_experiment_pages_and_cross_day_results(database_url):
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
    assert "实验配置 · 1/11" in demo.text
    assert "commute-map.js" in demo.text
    assert "新建实验" in script.text
    assert "世界与地图" in demo.text
    assert "Agent 配置" in demo.text
    assert "场景装配" in demo.text
    assert "实验结果" in demo.text
    assert "能力与工具" in script.text
    assert "tool.car-01" in script.text
    assert "controller_agent_id" in script.text
    assert "company.vehicle.enter" in script.text
    assert "worldSharedMap" in script.text
    assert "replaySharedMap" in script.text
    assert "window.CommuteMap.render" in script.text
    assert "一条跨日时间轴" in script.text
    assert "周一 08:47:18" in script.text
    assert "周二 07:54:12" in script.text
    assert "路口 A 红灯停车" in script.text
    assert "停入 P03" in script.text
    assert "case 10:unifiedReplay()" in script.text
    assert "commute-home-office" in shared_map_script.text
    assert "map-rev-commute-001" in shared_map_script.text
    assert "127.0.0.1:5001/v1" in script.text
    assert "URLSearchParams" in script.text
    assert "demo-steps" not in demo.text
    assert "/demos/two-day-commute?autoplay=1" in shell.text
