import asyncio
import json

from fastapi.responses import StreamingResponse

from app.chat.enums import StreamKind

HEARTBEAT_SECONDS = 15

PING = "ping"

TERMINAL_KINDS = (StreamKind.DONE, StreamKind.ERROR)


def frame(kind: StreamKind, value, trace_id: str) -> str:
    """의미 이벤트를 SSE 프레임 문자열로 직렬화"""
    if kind == StreamKind.DELTA:
        data = {"text": value}
    elif kind == StreamKind.DONE:
        data = value.model_dump(by_alias=True)
    else:
        data = {"traceId": trace_id, "error": {"code": value.code, "message": value.message, "details": []}}
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def with_heartbeat(first: tuple, events, terminal_kinds: tuple):
    """이벤트 사이 무토큰 구간에 하트비트 핑 삽입"""
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
    media_type = "text/event-stream"
