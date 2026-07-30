"""로깅 설정과 traceId 자동 첨부."""
import logging
from contextvars import ContextVar

"""ContextVar는 값을 담는 상자가 아니라 "열쇠"다 — 실제 값은 asyncio가 태스크마다
들고 다니는 숨은 딕셔너리(Context)에 저장되고, set/get은 지금 실행 중인 태스크의
딕셔너리에 이 열쇠로 쓰고 읽는다. 그래서 전역처럼 보여도 요청끼리 값이 안 섞인다.

  입력: 요청A 미들웨어가 set("aaa1"), 동시에 요청B 미들웨어가 set("bbb2")

  trace_id_var (열쇠 1개, 전역)
       ├─ 요청A 태스크의 Context: {trace_id_var: "aaa1"}   ← A 안의 get() → "aaa1"
       ├─ 요청B 태스크의 Context: {trace_id_var: "bbb2"}   ← B 안의 get() → "bbb2"
       └─ 요청 밖(부팅 로그 등)의 Context: {}              ← get() → default "-"
"""
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
