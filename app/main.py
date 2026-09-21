import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.chat import router as chat_router
from app.core import dynamo
from app.core.config import get_settings
from app.core.handlers import register_exception_handlers
from app.core.logging import configure_logging, trace_id_var
from app.exercises import router as exercises_router
from app.exercises.repository import get_exercise_catalog, get_exercise_meta
from app.routines import router as routines_router
from app.clients import postgres

configure_logging()
logger = logging.getLogger("fitset")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """부팅 시 executor 교체 테이블 바인딩 종목 데이터 선로딩"""
    executor = ThreadPoolExecutor(
        max_workers=get_settings().executor_max_workers, thread_name_prefix="blocking-io"
    )
    asyncio.get_running_loop().set_default_executor(executor)
    dynamo.bind_tables()

    async def preload() -> None:
        """종목 마스터와 카탈로그 백그라운드 선로딩"""
        try:
            await asyncio.to_thread(get_exercise_meta)
            await asyncio.to_thread(get_exercise_catalog)
        except Exception:
            logger.exception("preload failed")

    load_task = asyncio.create_task(preload())
    yield
    load_task.cancel()
    postgres.close()
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


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next) -> Response:
    """요청 traceId 발급 전파와 액세스 로그 기록"""
    request.state.trace_id = request.headers.get(TRACE_ID_HEADER) or uuid.uuid4().hex
    token = trace_id_var.set(request.state.trace_id)
    started = time.perf_counter()

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
    """헬스체크 제외 요청 완료 로그 1줄 기록"""
    if request.url.path in ("/health", "/ai/v1/health"):
        return
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.log(
        _level_for(status), "%s %s %d %.0fms", request.method, request.url.path, status, elapsed_ms,
    )


def _level_for(status: int) -> int:
    """응답 상태 코드별 로그 레벨 결정"""
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    return logging.INFO


@app.get("/health", include_in_schema=False)
@app.get("/ai/v1/health", include_in_schema=False)
async def health() -> JSONResponse:
    """프로세스 생존과 Postgres 연결 상태 헬스체크"""
    if postgres.is_configured() and not await asyncio.to_thread(postgres.ping):
        return JSONResponse(status_code=503, content={"status": "postgres unavailable"})
    return JSONResponse(content={"status": "ok"})


app.include_router(routines_router.router)
app.include_router(chat_router.router)
app.include_router(exercises_router.router)
