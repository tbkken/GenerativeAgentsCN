"""One natural-language Skill gives an object autonomous and interactive behavior."""

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
    """Binding enables both execution each Step and responses to interactions."""

    interaction_key: InteractionKey = "interact"
    skill_name: SkillName
    description: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
    ] = "与对象交互"
    interaction_radius_tiles: float = Field(default=2.0, gt=0, le=1_000)
    vision_radius: int = Field(default=4, ge=0, le=100)
    attention_bandwidth: int = Field(default=8, ge=0, le=100)
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
    if len(bindings) > 1:
        raise ValueError("a Game Object binds one root Skill; compose child Skills in its SOP")
    if len(keys) != len(set(keys)):
        raise ValueError("Game Object interaction_key values must be unique")
    return bindings


__all__ = [
    "GameObjectSkillBinding",
    "InteractionKey",
    "SkillName",
    "validate_unique_skill_bindings",
]
