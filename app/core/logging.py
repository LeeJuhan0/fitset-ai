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

"""실패가 로그로 저장되기까지 — 예: 채팅 중 Bedrock 호출이 죽었을 때 (service._run_agent)

  except Exception as exc:
      logger.exception("agent turn failed")        ← 우리 코드는 이 한 줄
          │
          ▼
  ① LogRecord 생성  { name:"fitset", level:ERROR, msg:"agent turn failed",
                      exc_info:(지금 예외의 타입·값·트레이스백) }
          │
          ▼
  ② _TraceIdFilter 통과
     record.trace_id ← trace_id_var.get()          ← 이 요청의 ID가 자동으로 붙는 지점
          │
          ▼
  ③ 포맷 적용  "%(asctime)s %(levelname)s %(name)s [%(trace_id)s] %(message)s"
     + exc_info가 있으면 트레이스백을 그 아래에 이어 붙임
          │
          ▼
  ④ stdout으로 출력 ──► 로컬: 터미널 / ECS: awslogs 드라이버 → CloudWatch Logs

실제 저장되는 모양:
  2026-07-29 13:51:58,713 ERROR fitset [4a31f1fc...] agent turn failed
  Traceback (most recent call last):
    File ".../app/chat/service.py", line 236, in _run_agent
      return await graph.run_turn(user_id, system_prompt, history)
    ...
  botocore.errorfactory.AccessDeniedException: An error occurred ...

레벨 규칙: logger.exception = ERROR+트레이스백(except 안에서만) /
warning(exc_info=True) = 폴백으로 응답은 살아남는 실패 / info = 정상 흐름 기록.
파일 저장 코드가 없는 건 의도 — stdout까지가 우리 책임, 수집·보관은 실행 환경 몫.
"""


class _TraceIdFilter(logging.Filter):
    """모든 로그 레코드에 trace_id 속성을 붙인다."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = trace_id_var.get()
        return True


def configure_logging() -> None:
    """root 로거의 레벨과 포맷을 설정하고 traceId 필터를 부착한다.

    uvicorn 기본 액세스 로그는 끈다 — 같은 요청이 두 줄로 남는 것을 막고,
    소요 시간·traceId가 담긴 미들웨어 로그(main.py) 한 줄만 남긴다.
    uvicorn.error(부팅·종료·예외)는 그대로 둔다.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s [%(trace_id)s] %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_TraceIdFilter())
    logging.getLogger("uvicorn.access").disabled = True
