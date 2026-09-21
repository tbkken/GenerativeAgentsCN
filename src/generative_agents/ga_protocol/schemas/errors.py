"""Transport-neutral operation diagnostics."""
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
