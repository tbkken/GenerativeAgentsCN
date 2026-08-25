"""Controlled lookup for a Run-owned replay artifact.

The former Flask debug server accepted a display name and joined it into a
filesystem path. HTTP delivery now belongs to FastAPI after database ownership
checks; this module only resolves an already validated RunPaths instance.
"""

from __future__ import annotations

from pathlib import Path

from generative_agents.runtime.context import RunPaths


def resolve_replay_artifact(paths: RunPaths) -> Path:
    """解析`replay`产物。

    参数:
        paths: 传入当前算法的`paths`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`RunPaths`。

    返回:
        返回目标文件或目录路径。

    异常:
        FileNotFoundError: 当所需文件或目录不存在时抛出。
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """
    target = (paths.artifacts / "movement.json").resolve()
    artifact_root = paths.artifacts.resolve()
    if target.parent != artifact_root:
        raise ValueError("replay artifact escaped its Run directory")
    if not target.is_file() or target.is_symlink():
        raise FileNotFoundError("Run replay artifact is not ready")
    return target
