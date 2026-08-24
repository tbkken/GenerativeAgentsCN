from __future__ import annotations

from fastapi.testclient import TestClient

from generative_agents.web import create_app


def test_map_configuration_demo_reuses_console_ux_from_catalog_to_published_revision(database_url):
    app = create_app(database_url=database_url, supervisor_enabled=False)

    with TestClient(app) as client:
        demo = client.get("/demos/map-configuration")
        script = client.get("/static/console/map-configuration-demo.js")
        style = client.get("/static/console/map-configuration-demo.css")
        shared_map_script = client.get("/static/console/commute-map.js")
        shared_map_style = client.get("/static/console/commute-map.css")
        shared_style = client.get("/static/console/product-demo.css")
        shell = client.get("/")

    assert demo.status_code == 200
    assert demo.headers["cache-control"] == "no-store"
    assert script.status_code == 200
    assert style.status_code == 200
    assert shared_map_script.status_code == 200
    assert shared_map_style.status_code == 200
    assert shared_style.status_code == 200
    assert 'class="app-shell"' in demo.text
    assert 'class="sidebar"' in demo.text
    assert 'class="demo-controller"' in demo.text
    assert 'id="mapAutoDemo"' in demo.text
    assert 'id="mapDemoStage"' in demo.text
    assert "地图中心" in script.text
    assert "新建公共地图" in script.text
    assert "地图画布" in script.text
    assert "空间语义" in script.text
    assert "画块与物件" in script.text
    assert "校验与版本" in script.text
    assert "地图配置 · 1/16" in demo.text
    assert "commute-map.js" in demo.text
    assert "住宅—公司两日通勤地图" in shared_map_script.text
    assert "commute-home-office" in shared_map_script.text
    assert "map-rev-commute-001" in shared_map_script.text
    assert "拖画 y=28 → y=34" in script.text
    assert "路口模块实例 A" in script.text
    assert "路口模块实例 B" in script.text
    assert "图层 ${s.phase}/8" in script.text
    assert "window.CommuteMap.render" in script.text
    assert "object-traffic-light" in script.text
    assert "map.signal-control.v2" in script.text
    assert "map.gate-parking-control.v1" in script.text
    assert "pointerdown" in script.text
    assert "wheel" in script.text
    assert "URLSearchParams" in script.text
    assert "map-step-ribbon" not in demo.text
    assert "/demos/map-configuration?autoplay=1" in shell.text
