"""FastAPI 앱 조립. 로깅과 executor 설정, traceId 미들웨어, 예외 핸들러와 라우터 등록을 담당한다.

실행은 uvicorn app.main:app. 부팅 시 DynamoDB routines를 인메모리로 전량 로드하며,
완료 전 /health는 503을 반환해 로드 중에는 트래픽을 받지 않는다.
"""
import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.chat import router as chat_router
from app.core.config import get_settings
from app.core.handlers import register_exception_handlers
from app.core.logging import configure_logging, trace_id_var
from app.exercises import router as exercises_router
from app.exercises.repository import get_exercise_catalog
from app.routines import router as routines_router
from app.routines.repository import get_exercise_meta, get_routine_store

configure_logging()
logger = logging.getLogger("fitset")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """부팅 시 기본 executor를 명시 크기로 교체하고 루틴 스토어를 백그라운드로 로드한다."""
    executor = ThreadPoolExecutor(
        max_workers=get_settings().executor_max_workers, thread_name_prefix="blocking-io"
    )
    asyncio.get_running_loop().set_default_executor(executor)

    store = get_routine_store()

    async def load_store() -> None:
        try:
            await asyncio.to_thread(store.load)
            # 종목 마스터(1.2MB JSON)·카탈로그(DynamoDB Scan) 선로딩 —
            # 첫 요청이 파싱·I/O로 이벤트 루프를 막지 않게
            await asyncio.to_thread(get_exercise_meta)
            await asyncio.to_thread(get_exercise_catalog)
            logger.info("routine store loaded: %d routines", len(store.routines))
        except Exception:
            logger.exception("routine store load failed")

    load_task = asyncio.create_task(load_store())
    yield
    load_task.cancel()
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="FitSet AI Server",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/ai/docs",
    openapi_url="/ai/openapi.json",
)
register_exception_handlers(app)

TRACE_ID_HEADER = "X-Trace-Id"


"""traceId가 한 요청의 로그 전체에 붙는 흐름 — set이 먼저, 로깅은 call_next 안에서.

  요청 도착 (X-Trace-Id: aaa1 또는 새로 발급)
     │
     ▼
  trace_id_var.set("aaa1")            ← 이 요청의 문맥에 ID를 심는다
     │
     ▼
  await call_next(request)            ← 파이프라인 전체 실행. 이 안의 모든 logger 호출은
     ├─ 라우터    logger.info(...)  →  "... [aaa1] ..."      _TraceIdFilter가 ContextVar를
     ├─ 서비스    logger.info(...)  →  "... [aaa1] ..."      읽어 로그마다 ID를 붙인다
     └─ 툴/저장소 logger.warning()  →  "... [aaa1] ..."
     │
     ▼
  trace_id_var.reset(token)           ← 문맥 반납 — 다음 요청에 안 샌다
     │
     ▼
  응답 (본문 traceId + 헤더 X-Trace-Id: aaa1)

동시에 처리 중인 요청 B는 자기 문맥의 "bbb2"를 보므로 로그가 섞여 찍혀도 ID로 구분된다.

같은 값을 request.state와 ContextVar 두 곳에 넣는 이유 — request를 가진 코드(deps·핸들러)는
state에서, 못 가진 코드(콜스택 깊은 곳의 로깅 필터)는 ContextVar에서 읽는다.
"""
@app.middleware("http")
async def trace_id_middleware(request: Request, call_next) -> Response:
    """요청의 traceId를 정해 로그 컨텍스트·응답 헤더에 전파하고, 완료를 한 줄로 남긴다.

    uvicorn 액세스 로그를 끈 대신(core/logging.py) 여기서 유일한 액세스 로그를 낸다 —
    메서드·경로·상태·소요 시간이 traceId와 함께 남아 요청 하나를 끝까지 추적할 수 있다.
    """
    request.state.trace_id = request.headers.get(TRACE_ID_HEADER) or uuid.uuid4().hex
    token = trace_id_var.set(request.state.trace_id)
    started = time.perf_counter()

    # 예외 핸들러가 못 잡은 예외로 call_next가 죽으면 status가 500인 채 finally로 간다
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers[TRACE_ID_HEADER] = request.state.trace_id
        return response
    finally:
        _log_access(request, status, started)
        trace_id_var.reset(token)


def _log_access(request: Request, status: int, started: float) -> None:
    """요청 완료 1줄. 상태에 따라 레벨을 나눠 CloudWatch 필터가 의미를 갖게 한다.

    헬스체크는 남기지 않는다 — ALB가 15초마다 두 서브넷에서 호출해 실 트래픽을 덮는다
    (2026-08-05 실측: 2시간 로그 873줄 중 489줄이 /health).
    스트리밍 응답(SSE)의 소요 시간은 전송 완료가 아니라 첫 바이트까지다.
    """
    if request.url.path in ("/health", "/ai/v1/health"):
        return
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.log(
        _level_for(status), "%s %s %d %.0fms", request.method, request.url.path, status, elapsed_ms,
    )


def _level_for(status: int) -> int:
    """상태 코드 → 로그 레벨. 5xx는 서버 장애, 4xx는 클라 잘못, 나머지는 정상."""
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    return logging.INFO


@app.get("/health", include_in_schema=False)
@app.get("/ai/v1/health", include_in_schema=False)
async def health() -> JSONResponse:
    """루틴 스토어 로드 완료 여부로 헬스체크에 응답한다."""
    store = get_routine_store()
    if not store.ready:
        return JSONResponse(status_code=503, content={"status": "loading"})
    return JSONResponse(content={"status": "ok", "routines": len(store.routines)})


app.include_router(routines_router.router)
app.include_router(chat_router.router)
app.include_router(exercises_router.router)

# 메트릭 노출(/metrics) — Alloy 사이드카가 localhost로 60초마다 수집해 Grafana Cloud로 보낸다.
# ALB는 /ai/* 만 이 서버로 라우팅하므로 /metrics 는 인터넷에 나가지 않는다(백엔드 /actuator 와 동일 원리).
Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(app, include_in_schema=False)
