"""DomainError -> HTTP 映射与全局异常处理。

错误体永不携带堆栈、绝对路径、密钥或他人数据（01 §10.1）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from study_companion.api.envelope import failure
from study_companion.contracts import DomainError

logger = logging.getLogger(__name__)

STATUS_BY_CODE: dict[str, int] = {
    "VALIDATION_ERROR": 400,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "CONTEXT_MISMATCH": 403,
    "NOT_FOUND": 404,
    "KB_NOT_FOUND": 404,
    "KB_NOT_PUBLISHED": 422,
    "CONFLICT": 409,
    "REVISION_CONFLICT": 409,
    "RESULT_NOT_READY": 409,
    "DEPENDENCY_STALE": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "JOB_RETRY_NOT_AVAILABLE": 409,
    "PLAN_SUSPENDED": 409,
    "UNPROCESSABLE": 422,
    "TASK_UNCLEAR": 422,
    "TASK_NOT_AVAILABLE": 422,
    "MATERIAL_INVALID": 422,
    "LESSON_SCHEMA_INVALID": 422,
    "MATH_INVALID": 422,
    "ANSWER_PROTECTION": 422,
    "EXPORT_INCOMPLETE": 422,
    "EXPORT_SCOPE_INVALID": 422,
    "UPLOAD_TOO_LARGE": 413,
    "MODEL_NOT_CONFIGURED": 503,
    "MODEL_TIMEOUT": 503,
    "MODEL_CAPABILITY_UNAVAILABLE": 503,
    "MODEL_BAD_RESPONSE": 503,
    "SERVICE_UNAVAILABLE": 503,
    "EXPORT_ARTIFACT_INVALID": 503,
}

CODE_BY_STATUS: dict[int, str] = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "UPLOAD_TOO_LARGE",
    422: "UNPROCESSABLE",
    503: "SERVICE_UNAVAILABLE",
}


def status_for(code: str) -> int:
    return STATUS_BY_CODE.get(code, 500)


def error_data(exc: DomainError) -> dict[str, Any]:
    return {"code": exc.code, "message": exc.message, "details": exc.details}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=status_for(exc.code),
            content=failure(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error.get("loc", ())[1:]) or "body",
                "reason": str(error.get("msg", "invalid value")),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=400,
            content=failure("VALIDATION_ERROR", "输入字段不符合要求", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = CODE_BY_STATUS.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code
        return JSONResponse(status_code=exc.status_code, content=failure(code, message))

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=failure("INTERNAL_ERROR", "服务内部错误，请查看服务端日志"),
        )
