import asyncio

from fastapi.responses import StreamingResponse

HEARTBEAT_SECONDS = 15

PING = "ping"


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
