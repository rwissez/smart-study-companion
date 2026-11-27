"""统一响应包络（wire schema_version 1.1）。

成功：``{schema_version, request_id, data, warnings}``
失败：``{schema_version, request_id, error:{code,message,details}}``

``request_id`` 由中间件写入 contextvar，路由无需层层传递。
"""

from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar
from typing import Any

SCHEMA_VERSION = "1.1"

_request_id: ContextVar[str] = ContextVar("request_id", default="req-unset")


def set_request_id(value: str) -> None:
    _request_id.set(value)


def current_request_id() -> str:
    return _request_id.get()


def warning(code: str, message: str, issue_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if issue_id is not None:
        payload["issue_id"] = issue_id
    return payload


def success(data: Any, warnings: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": current_request_id(),
        "data": data,
        "warnings": list(warnings or []),
    }


def page(
    items: Sequence[Any], total: int, page_number: int, page_size: int
) -> dict[str, Any]:
    return {
        "items": list(items),
        "total": total,
        "page": page_number,
        "page_size": page_size,
    }


def failure(
    code: str, message: str, details: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": current_request_id(),
        "error": {"code": code, "message": message, "details": details or []},
    }
