"""Shared memory lifecycle and committed memory delta values."""
from enum import StrEnum


class MemoryState(StrEnum):
    """运行记忆在持久化存储中的生命周期状态。"""

    ACTIVE = "ACTIVE"  # 记忆仍可被检索、访问和用于智能体决策。
    EXPIRED = "EXPIRED"  # 记忆已到达过期条件，不再参与后续检索。
    EVICTED = "EVICTED"  # 记忆因容量或保留策略被淘汰。
    SUPERSEDED = "SUPERSEDED"  # 记忆已被一个更正或更新版本替代。
    INVALIDATED = "INVALIDATED"


class MemoryDeltaKind(StrEnum):
    """单步结果中记录的记忆变化类型。"""

    CREATED = "CREATED"  # 当前步骤创建了一条新记忆。
    ACCESSED = "ACCESSED"  # 当前步骤读取并使用了一条现有记忆。
    EXPIRED = "EXPIRED"  # 当前步骤确认记忆自然过期。
    EVICTED = "EVICTED"  # 当前步骤按保留策略淘汰记忆。
    SUPERSEDED = "SUPERSEDED"  # 当前步骤用一个新记忆版本替代旧版本。
    INVALIDATED = "INVALIDATED"


__all__ = ["MemoryState", "MemoryDeltaKind"]
