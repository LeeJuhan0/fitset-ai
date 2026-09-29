from pydantic import Field

from app.chat.enums import MessageRole
from app.core.schemas import CamelModel, ErrorResponse, ResponseScheme


class ThreadOut(CamelModel):
    thread_id: str
    title: str | None
    last_message_at: str | None
    needs_deletion: bool


class ThreadListData(CamelModel):
    items: list[ThreadOut]
    can_create_thread: bool


class ThreadCreated(CamelModel):
    thread_id: str
    created_at: str


class MessageOut(CamelModel):
    message_id: str
    role: MessageRole
    content: str
    response_scheme: ResponseScheme | None
    payload: dict | None
    created_at: str


class MessagePageData(CamelModel):
    items: list[MessageOut]
    next_cursor: str | None


class MessageSendRequest(CamelModel):
    content: str = Field(min_length=1, max_length=5000)


class MessageSendData(CamelModel):
    message: MessageOut
    thread_title: str | None


class MessageDeltaData(CamelModel):
    text: str


class MessageStreamEvent(CamelModel):
    delta: MessageDeltaData | None = None
    done: MessageSendData | None = None
    error: ErrorResponse | None = None


SSE_EXAMPLE = (
    'event: delta\ndata: {"text":"스쿼트를 빼고"}\n\n'
    'event: delta\ndata: {"text":" 레그프레스로 대체했어요."}\n\n'
    ':ping\n\n'
    'event: done\ndata: {"message":{"messageId":"01J...","role":"assistant",'
    '"content":"스쿼트를 빼고 레그프레스로 대체했어요.","responseScheme":"routine",'
    '"payload":{"slug":"...","exercises":[{"exerciseId":"11f1...","slug":"barbell-squat"}]},'
    '"createdAt":"2026-08-05T09:01:00Z"},"threadTitle":"하체 루틴 조정"}\n\n'
)
SSE_RESPONSES = {
    200: {
        "model": MessageStreamEvent,
        "description": (
            "SSE 스트림 (text/event-stream). 이벤트 3종 — "
            "`delta`(본문 증분), `done`(정본 · payload는 여기에만), `error`(시작 후 실패). "
            "무토큰 구간에는 하트비트 코멘트(`:ping`)가 온다. traceId는 X-Trace-Id 헤더."
        ),
        "content": {"text/event-stream": {"example": SSE_EXAMPLE}},
    },
}
