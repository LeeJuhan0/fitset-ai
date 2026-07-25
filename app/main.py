"""Composition Root — FastAPI 앱 조립, traceId 미들웨어, 전역 예외 핸들러, 라우터 등록.

실행: uvicorn app.main:app  (변수 `app`이 ASGI 진입점)
인증: ELB는 TLS 종료만 — 이 서버가 SSM 공개키로 JWT(RS256)를 직접 검증한다 (비즈니스 규칙 §9).
부팅 시 DynamoDB `routines`를 인메모리로 전량 로드하며, 완료 전 /health는 503(로드 중)을 반환해
ECS/ALB 헬스체크가 준비 완료까지 트래픽을 넣지 않게 한다.

응답 형식은 팀 API 설계 규약(01)을 따른다:
성공 {traceId, data} / 실패 {traceId, error: {code, message, details}}.
성공 응답은 라우터의 response_model=ApiResponse[...] (core/schemas.py)가 검증하고,
실패 응답은 여기의 전역 예외 핸들러가 조립한다 — HTTP 상태코드는 그대로 유지.
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder             # 검증 에러의 원본 입력값 → JSON 직렬화 가능 형태
from fastapi.exceptions import RequestValidationError     # 요청 바디/쿼리 검증 실패
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException   # FastAPI HTTPException의 부모(라우트 404 포함)

from app.core.errors import DomainError
from app.routines import router as routines_router
from app.routines.repository import get_routine_store

logger = logging.getLogger("fitset")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """부팅 시 루틴 스토어(전량 Scan + 임베딩)를 백그라운드 스레드에서 로드한다."""
    store = get_routine_store()

    async def load_store() -> None:
        try:
            await asyncio.to_thread(store.load)
            logger.info("routine store loaded: %d routines", len(store.routines))
        except Exception:
            logger.exception("routine store load failed — /health stays 503")

    load_task = asyncio.create_task(load_store())
    yield
    load_task.cancel()


app = FastAPI(title="FitSet AI Server", version="0.1.0", lifespan=lifespan)

TRACE_ID_HEADER = "X-Trace-Id"

# 상태코드 → 시맨틱 에러 코드 기본 매핑(01 규약 §4 카탈로그).
# 도메인 예외(core/errors.py)는 DomainError 핸들러가 자체 code로 번역한다.
_DEFAULT_ERROR_CODES = {
    400: "INVALID_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
}
_FALLBACK_ERROR_CODE = "INTERNAL_ERROR"


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next) -> Response:
    """게이트웨이가 준 X-Trace-Id를 전파하고, 없으면 서버가 생성한다.

    핸들러·라우터는 request.state.trace_id (deps.get_trace_id)로 읽는다.
    응답 헤더에도 실어 클라·게이트웨이 로그와 상호 추적을 가능하게 한다.
    """
    request.state.trace_id = request.headers.get(TRACE_ID_HEADER) or uuid.uuid4().hex
    response = await call_next(request)
    response.headers[TRACE_ID_HEADER] = request.state.trace_id
    return response


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list | None = None,
) -> JSONResponse:
    """실패 응답 조립 — {traceId, error: {code, message, details}} (규약 §3.3)."""
    return JSONResponse(
        status_code=status_code,
        content={
            "traceId": getattr(request.state, "trace_id", ""),
            "error": {"code": code, "message": message, "details": details or []},
        },
    )


@app.exception_handler(DomainError)
async def domain_exception_handler(request: Request, exc: DomainError) -> JSONResponse:
    """service가 던진 도메인 예외 → 시맨틱 코드 그대로 실패 응답으로 번역한다."""
    return _error_response(request, exc.status_code, exc.code, exc.message)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """라우터가 던진 HTTPException과 라우트 없음(404)을 실패 응답으로 변환한다.

    detail이 {"code": ..., "message": ...} dict면 시맨틱 코드를 그대로 쓰고,
    문자열이면 상태코드 기본 매핑으로 code를 정한다.
    """
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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """요청 스키마 검증 실패 — 400 INVALID_REQUEST, 필드별 사유는 details에 담는다(규약 §3.4)."""
    details = [
        {
            "field": ".".join(str(part) for part in err["loc"] if part != "body"),
            "value": jsonable_encoder(err.get("input")),
            "reason": err["msg"],
        }
        for err in exc.errors()
    ]
    return _error_response(request, 400, "INVALID_REQUEST", "요청값이 올바르지 않습니다.", details)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """예상 못 한 예외(DynamoDB·LLM·스프링 API 호출 실패 등) — 내부 정보는 숨긴다."""
    return _error_response(request, 500, _FALLBACK_ERROR_CODE, "서버 내부 오류가 발생했습니다.")


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    """ECS/ALB 헬스체크 — 루틴 스토어 로드 완료 전에는 503으로 트래픽을 막는다."""
    store = get_routine_store()
    if not store.ready:
        return JSONResponse(status_code=503, content={"status": "loading"})
    return JSONResponse(content={"status": "ok", "routines": len(store.routines)})


# ── 라우터 등록 (임포트는 모듈 단위 컨벤션) ──
app.include_router(routines_router.router)
# chat·exercises 도메인은 다음 구현 범위에서 등록
