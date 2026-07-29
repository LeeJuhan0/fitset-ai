"""전역 예외 핸들러. 예외를 팀 규약 실패 응답으로 번역한다."""
import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import DomainError

logger = logging.getLogger("fitset")

_DEFAULT_ERROR_CODES = {
    400: "INVALID_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
}
_FALLBACK_ERROR_CODE = "INTERNAL_ERROR"


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list | None = None,
) -> JSONResponse:
    """실패 응답 JSON을 조립한다."""
    return JSONResponse(
        status_code=status_code,
        content={
            "traceId": getattr(request.state, "trace_id", ""),
            "error": {"code": code, "message": message, "details": details or []},
        },
    )


async def _domain_exception_handler(request: Request, exc: DomainError) -> JSONResponse:
    """도메인 예외를 시맨틱 코드 그대로 실패 응답으로 번역한다."""
    return _error_response(request, exc.status_code, exc.code, exc.message)


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """HTTPException과 라우트 없음 404를 실패 응답으로 변환한다."""
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return _error_response(
            request,
            exc.status_code,
            exc.detail["code"],
            exc.detail.get("message", ""),
            exc.detail.get("details"),
        )
    code = _DEFAULT_ERROR_CODES.get(exc.status_code, _FALLBACK_ERROR_CODE)
    return _error_response(request, exc.status_code, code, str(exc.detail))


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """요청 스키마 검증 실패를 400 응답으로 변환한다."""
    details = [
        {
            "field": ".".join(str(part) for part in err["loc"] if part != "body"),
            "value": jsonable_encoder(err.get("input")),
            "reason": err["msg"],
        }
        for err in exc.errors()
    ]
    return _error_response(request, 400, "INVALID_REQUEST", "요청값이 올바르지 않습니다.", details)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """예상 못 한 예외를 로그에 남기고 500 응답으로 변환한다."""
    logger.exception(
        "unhandled error: %s %s", request.method, request.url.path,
        exc_info=exc, extra={"trace_id": getattr(request.state, "trace_id", "-")},
    )
    return _error_response(
        request, status.HTTP_500_INTERNAL_SERVER_ERROR, _FALLBACK_ERROR_CODE,
        "서버 내부 오류가 발생했습니다.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """전역 예외 핸들러를 앱에 일괄 등록한다."""
    app.add_exception_handler(DomainError, _domain_exception_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
