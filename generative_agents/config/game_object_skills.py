"""Contracts for passive Skills exposed by map Game Objects."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from .schema import StrictModel


InteractionKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
        max_length=80,
    ),
]
SkillName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        max_length=64,
    ),
]


class GameObjectSkillBinding(StrictModel):
    """One Agent-initiated request endpoint exposed by a Game Object.

    Proximity only makes the binding discoverable.  The runtime must not invoke
    the Skill until an Agent explicitly selects ``interaction_key``.
    """

    interaction_key: InteractionKey
    skill_name: SkillName
    description: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
    ]
    interaction_radius_m: float = Field(default=2.0, gt=0, le=1_000)
    default_request: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
    ] = "请提供当前状态和可执行信息。"


def validate_unique_skill_bindings(
    bindings: list[GameObjectSkillBinding],
) -> list[GameObjectSkillBinding]:
    """校验`unique`技能`bindings`。

    参数:
        bindings: 技能、提示词或空间对象之间的声明式绑定集合。 类型：`list[GameObjectSkillBinding]`。

    返回:
        返回按接口约定组织的结果集合。

    异常:
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """
    keys = [item.interaction_key for item in bindings]
    if len(keys) != len(set(keys)):
        raise ValueError("Game Object interaction_key values must be unique")
    return bindings


__all__ = [
    "GameObjectSkillBinding",
    "InteractionKey",
    "SkillName",
    "validate_unique_skill_bindings",
]
