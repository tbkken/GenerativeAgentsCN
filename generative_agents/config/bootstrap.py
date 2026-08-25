"""Creation-time catalog for the bundled Chinese town experiment template.

Runtime workers never read these files. They are materialized into a new Draft
Revision once, after which the database Revision is the only configuration fact.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .schema import ExperimentDefinition, make_blank_definition

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_VILLAGE_ROOT = _PACKAGE_ROOT / "frontend" / "static" / "assets" / "village"


def _read_json(path: Path) -> dict:
    """读取`json`。

    参数:
        path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 类型：`Path`。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _bundled_payload() -> dict:
    """执行`bundled`载荷的内部处理，供当前模块或类复用。

    返回:
        返回以字段名或业务键组织的结构化映射。
    """
    base = make_blank_definition(
        key="builtin-town",
        name="标准小镇模板",
        goal="观察居民在共享世界中的日程、记忆、对话与社会传播。",
    ).model_dump(mode="json", exclude_none=False)
    base["world"] = {
        "world_key": "the-ville",
        "world_name": "the Ville",
        "definition": _read_json(_VILLAGE_ROOT / "maze.json"),
        # Tile images remain legacy display resources until explicitly uploaded
        # to the content-addressed Asset API. Simulation reads only the inline
        # immutable maze definition above.
        "assets": [],
    }
    agents = []
    agent_dirs = sorted(
        (path for path in (_VILLAGE_ROOT / "agents").iterdir() if path.is_dir()),
        key=lambda item: item.name,
    )
    for index, directory in enumerate(agent_dirs, start=1):
        source = _read_json(directory / "agent.json")
        agents.append(
            {
                "agent_key": f"resident-{index:03d}",
                "enabled": True,
                "name": source["name"],
                "portrait_asset": None,
                "coord": source["coord"],
                "currently": source.get("currently", ""),
                "scratch": source["scratch"],
                "spatial": source.get("spatial", {}),
            }
        )
    base["agents"] = agents
    return base


def make_builtin_definition(
    *, key: str, name: str, goal: str = ""
) -> ExperimentDefinition:
    """执行 的`make``builtin`仿真定义操作。

    参数:
        key: 用于定位目标记录、配置项或技能的稳定键。 类型：`str`。
        name: 目标对象的人类可读名称。 类型：`str`。
        goal: 路径搜索、计划或推理任务需要达到的目标。 类型：`str`。 默认值：`''`。

    返回:
        返回 `ExperimentDefinition` 类型的处理结果。
    """

    payload = _bundled_payload()
    # Pydantic validation produces a deep owned structure, so no caller can
    # mutate the cached catalog payload or another experiment's Draft.
    owned = json.loads(json.dumps(payload, ensure_ascii=False))
    owned["experiment"].update({"key": key, "name": name, "goal": goal})
    return ExperimentDefinition.model_validate(owned)
