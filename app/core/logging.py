"""로깅 설정과 traceId 자동 첨부."""
import logging
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


class _TraceIdFilter(logging.Filter):
    """모든 로그 레코드에 trace_id 속성을 붙인다."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = trace_id_var.get()
        return True


def configure_logging() -> None:
    """root 로거의 레벨과 포맷을 설정하고 traceId 필터를 부착한다."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s [%(trace_id)s] %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_TraceIdFilter())
