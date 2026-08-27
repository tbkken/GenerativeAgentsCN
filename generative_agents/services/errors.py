"""Transport-neutral application errors."""

from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    """可稳定映射为 HTTP 状态码、错误代码和详情的业务异常。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            code: 稳定错误码、状态码或调用方可识别的协议代码。 类型：`str`。
            message: 待发送、校验、脱敏或写入会话的消息文本或对象。 类型：`str`。
            status_code: 传入当前算法的`status``code`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`int`。
            details: 随错误或结果返回的结构化诊断详情。 类型：`dict[str, Any] | None`。 默认值：`None`。

        返回:
            无返回值。
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


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
