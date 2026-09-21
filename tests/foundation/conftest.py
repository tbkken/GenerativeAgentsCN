"""基础能力回归测试：覆盖 ``conftest`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

from pathlib import Path

import pytest

from generative_agents.ga_studio.storage.database import create_database
from generative_agents.ga_studio.storage.database import upgrade_database


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
