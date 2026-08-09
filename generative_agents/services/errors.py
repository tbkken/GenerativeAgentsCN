"""Transport-neutral application errors."""

from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def not_found(kind: str, object_id: str) -> ServiceError:
    return ServiceError(
        f"{kind.upper()}_NOT_FOUND",
        f"{kind} 不存在",
        status_code=404,
        details={"id": object_id},
    )
