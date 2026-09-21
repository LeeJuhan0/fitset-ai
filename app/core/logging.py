import logging
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


class _TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        """로그 레코드에 현재 요청 traceId 속성 부착"""
        if not hasattr(record, "trace_id"):
            record.trace_id = trace_id_var.get()
        return True


def configure_logging() -> None:
    """루트 로거 레벨 포맷 설정과 traceId 필터 부착"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s [%(trace_id)s] %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_TraceIdFilter())
    logging.getLogger("uvicorn.access").disabled = True
