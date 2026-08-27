"""基础能力回归测试：覆盖 ``test_sysu_campus_map_seed`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from generative_agents.config.schema import WorldConfig
from generative_agents.web.app import create_app
from tools.seed_sysu_south_campus_map import HEIGHT, MAP_KEY, WIDTH, build_world


def test_campus_map_has_approximate_geometry_and_agent_semantics():
    """回归验证 ``test_campus_map_has_approximate_geometry_and_agent_semantics`` 所描述的业务结果、故障边界和隔离约束。"""
    world = WorldConfig.model_validate(build_world())
    definition = world.definition
    coords = [tuple(tile["coord"]) for tile in definition["tiles"]]
    addresses = [tile.get("address") for tile in definition["tiles"]]

    assert definition["size"] == [HEIGHT, WIDTH]
    assert definition["tile_address_keys"] == ["world", "sector", "arena", "game_object"]
    assert len(coords) == len(set(coords))
    assert len(definition["editor"]["cells"]) > 7_000
    assert any(address == ["中部教学区", "南校区图书馆", "阅览室"] for address in addresses)
    assert any(address == ["校门与公共区域", "北门", "北门入口"] for address in addresses)
    assert any(address == ["校门与公共区域", "南门", "南门入口"] for address in addresses)
    assert any(tile.get("collision") and definition["editor"]["cells"].get(
        f"{tile['coord'][0]},{tile['coord'][1]}", {}
    ).get("kind") == "water" for tile in definition["tiles"])


def test_api_accepts_a_human_selected_public_map_key(database_url):
    """回归验证 ``test_api_accepts_a_human_selected_public_map_key`` 所描述的业务结果、故障边界和隔离约束。"""
    app = create_app(database_url=database_url, supervisor_enabled=False)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/maps",
            json={"map_key": MAP_KEY, "name": "校园地图"},
        )
        duplicate = client.post(
            "/api/v1/maps",
            json={"map_key": MAP_KEY, "name": "重复校园地图"},
        )

    assert created.status_code == 201
    assert created.json()["map_key"] == MAP_KEY
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "MAP_KEY_CONFLICT"
