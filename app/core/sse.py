"""SSE 전송 유틸 — 하트비트 삽입과 media_type 고정 응답. core 레이어.

이벤트의 의미는 모른다 — (kind, value) 튜플을 그대로 흘리고, 무토큰 구간에만
(PING, None)을 끼워 넣는다. 프레임 직렬화(event/data 라인)는 각 라우터가 맡는다.
"""
import asyncio

from fastapi.responses import StreamingResponse

# 무토큰 구간(툴 실행) 연결 유지 — 클라이언트 API 명세 §4.6 규약 2는 "15초 이내 간격"이다
HEARTBEAT_SECONDS = 15

# 하트비트 표식 — 의미 이벤트가 아니라 전송 계층 어휘라 여기 둔다
PING = "ping"


async def with_heartbeat(first: tuple, events, terminal_kinds: tuple):
    """의미 이벤트 사이의 무토큰 구간마다 (PING, None)을 끼워 넣는다.

    terminal_kinds의 이벤트 뒤에는 더 이상 이벤트가 오지 않으므로 즉시 종료한다.
    다음 이벤트를 태스크로 미리 걸어 두고 HEARTBEAT_SECONDS씩 기다린다 —
    wait_for는 타임아웃 시 대기를 취소해 제너레이터를 깨뜨리므로 wait를 쓴다.
    """
    event = first
    while True:
        yield event
        if event[0] in terminal_kinds:
            return
        next_event = asyncio.ensure_future(anext(events))
        while True:
            finished, _ = await asyncio.wait({next_event}, timeout=HEARTBEAT_SECONDS)
            if finished:
                break
            yield PING, None
        try:
            event = next_event.result()
        except StopAsyncIteration:
            return


class SseResponse(StreamingResponse):
    """media_type이 고정된 스트리밍 응답 — OpenAPI가 스키마를 이 미디어 타입에 매단다.

    FastAPI는 response_class.media_type을 응답 스키마의 키로 쓴다. 기본 StreamingResponse는
    media_type이 없어 application/json으로 문서화되므로, SSE임을 문서에도 드러내려고 고정한다.
    """

    media_type = "text/event-stream"
