"""FastAPI 앱 조립. 로깅과 executor 설정, traceId 미들웨어, 예외 핸들러와 라우터 등록을 담당한다.

실행은 uvicorn app.main:app. 부팅 시 DynamoDB routines를 인메모리로 전량 로드하며,
완료 전 /health는 503을 반환해 로드 중에는 트래픽을 받지 않는다.
"""
import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.handlers import register_exception_handlers
from app.core.logging import configure_logging, trace_id_var
from app.routines import router as routines_router
from app.routines.repository import get_routine_store

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
            logger.info("routine store loaded: %d routines", len(store.routines))
        except Exception:
            logger.exception("routine store load failed")

    load_task = asyncio.create_task(load_store())
    yield
    load_task.cancel()
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="FitSet AI Server", version="0.1.0", lifespan=lifespan)
register_exception_handlers(app)

TRACE_ID_HEADER = "X-Trace-Id"


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next) -> Response:
    """요청의 traceId를 정하고 로그 컨텍스트와 응답 헤더에 전파한다."""
    request.state.trace_id = request.headers.get(TRACE_ID_HEADER) or uuid.uuid4().hex
    token = trace_id_var.set(request.state.trace_id)
    try:
        response = await call_next(request)
    finally:
        trace_id_var.reset(token)
    response.headers[TRACE_ID_HEADER] = request.state.trace_id
    return response


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    """루틴 스토어 로드 완료 여부로 헬스체크에 응답한다."""
    store = get_routine_store()
    if not store.ready:
        return JSONResponse(status_code=503, content={"status": "loading"})
    return JSONResponse(content={"status": "ok", "routines": len(store.routines)})


app.include_router(routines_router.router)
