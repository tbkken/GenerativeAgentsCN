"""File-backed Agent Skills and their natural-language runtime."""

from .passive import (
    PassiveSkillResult,
    PassiveSkillRuntimeError,
    SnapshotPassiveSkillRuntime,
)
from .registry import (
    SkillDocument,
    SkillRegistry,
    SkillRegistryError,
    SnapshotSkillRegistry,
)
from .runtime import (
    RecoverableSkillRuntimeError,
    SkillLoopError,
    SkillModelError,
    SkillRunResult,
    SkillRuntime,
    SkillRuntimeError,
)

__all__ = [
    "MemoryStream",
    "DatabaseSkillRegistry",
    "PassiveSkillResult",
    "PassiveSkillRuntimeError",
    "RecoverableSkillRuntimeError",
    "SkillDocument",
    "SkillMCPServer",
    "SkillLoopError",
    "SkillModelError",
    "SkillRegistry",
    "SkillRegistryError",
    "SkillRunResult",
    "SkillRuntime",
    "SkillRuntimeError",
    "SnapshotSkillRegistry",
    "SnapshotPassiveSkillRuntime",
]


def __getattr__(name: str):
    """Keep the Studio-only database registry behind an explicit lazy boundary.

    Runtime and Replay may import ``generative_agents.skills`` without installing
    or initializing SQLAlchemy.  Existing Studio code can continue importing the
    registry by name while it is migrated to ``ga_studio``.
    """

    if name == "DatabaseSkillRegistry":
        from .database import DatabaseSkillRegistry

        return DatabaseSkillRegistry
    if name in {"MemoryStream", "SkillMCPServer"}:
        from .mcp import MemoryStream, SkillMCPServer

        return {"MemoryStream": MemoryStream, "SkillMCPServer": SkillMCPServer}[name]
    raise AttributeError(name)
