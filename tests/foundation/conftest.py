"""基础能力回归测试：覆盖 ``conftest`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from pathlib import Path

import pytest

from generative_agents.config import ExperimentDefinition
from generative_agents.config.schema import make_blank_definition
from generative_agents.persistence import create_database, upgrade_database
from generative_agents.services import ExperimentService


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """为本测试模块封装 ``database_url`` 辅助步骤，减少重复的场景搭建代码。"""
    return "sqlite:///" + (tmp_path / "experiments.db").as_posix()


@pytest.fixture
def database(database_url: str):
    """为本测试模块封装 ``database`` 辅助步骤，减少重复的场景搭建代码。"""
    upgrade_database(database_url)
    value = create_database(database_url)
    yield value
    value.close()


@pytest.fixture
def service(database) -> ExperimentService:
    """为本测试模块封装 ``service`` 辅助步骤，减少重复的场景搭建代码。"""
    return ExperimentService(database)


@pytest.fixture
def publishable_definition() -> ExperimentDefinition:
    """为本测试模块封装 ``publishable_definition`` 辅助步骤，减少重复的场景搭建代码。"""
    definition = make_blank_definition(
        key="publishable-experiment", name="可发布实验", goal="验证发布不变量"
    )
    payload = definition.model_dump(mode="json", exclude_none=False)
    payload["models"]["chat"]["resolved_model"] = "Qwen/test-chat"
    payload["models"]["embedding"]["resolved_model"] = "test-embedding"
    payload["engine"].update(
        {
            "brain_revision_id": "brain-revision-test",
            "brain_revision_hash": "a" * 64,
        }
    )
    payload["world"].update(
        {
            "map_id": "map-test",
            "map_revision_id": "map-revision-test",
            "map_revision_hash": "b" * 64,
        }
    )
    payload["world"]["definition"] = {
        "world": "test",
        "tile_size": 16,
        "size": [1, 1],
        "map": [[0]],
        "camera": [0, 0],
        "tile_address_keys": {},
        "tiles": [
            {
                "coord": [0, 0],
                "collision": False,
                "address": ["home", "bedroom", "bed"],
            }
        ],
    }
    payload["agents"] = [
        {
            "agent_key": "test-agent",
            "enabled": True,
            "name": "Test Agent",
            "portrait_asset": None,
            "coord": [0, 0],
            "currently": "testing",
            "scratch": {
                "age": 30,
                "innate": "careful",
                "learned": "tests systems",
                "lifestyle": "repeatable",
                "daily_plan": "",
            },
            "spatial": {
                "address": {
                    "living_area": ["test", "home", "bedroom"],
                    "sleeping": ["test", "home", "bedroom", "bed"],
                },
                "tree": {"test": {"home": {"bedroom": ["bed"]}}},
            },
        }
    ]
    return ExperimentDefinition.model_validate(payload)
