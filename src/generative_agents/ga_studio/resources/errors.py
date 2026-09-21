"""Transport-neutral application errors."""

from __future__ import annotations



from generative_agents.ga_protocol.schemas.errors import ServiceError


def not_found(kind: str, object_id: str) -> ServiceError:
    """执行 的`not``found`操作。

    参数:
        kind: 用于选择解析、校验或执行分支的稳定类型判别值。 类型：`str`。
        object_id: 对象的唯一标识。 类型：`str`。

    返回:
        返回 `ServiceError` 类型的处理结果。
    """
    return ServiceError(
        f"{kind.upper()}_NOT_FOUND",
        f"{kind} 不存在",
        status_code=404,
        details={"id": object_id},
    )
