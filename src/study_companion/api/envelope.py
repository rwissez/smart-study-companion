"""Response envelope shared by every JSON endpoint.

Shape is fixed by docs/team/08: success carries ``data`` + ``warnings``,
failure carries ``error``. ``request_id`` comes from a contextvar set by the
middleware so route handlers never have to thread it through.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

SCHEMA_VERSION = "1.0"

_request_id: ContextVar[str] = ContextVar("request_id", default="req-unset")


def set_request_id(value: str) -> None:
    _request_id.set(value)


def current_request_id() -> str:
    return _request_id.get()


def success(data: Any, warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": current_request_id(),
        "data": data,
        "warnings": warnings or [],
    }


def failure(
    code: str, message: str, details: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": current_request_id(),
        "error": {"code": code, "message": message, "details": details or []},
    }
