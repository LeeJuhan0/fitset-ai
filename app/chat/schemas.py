from typing import Literal

from pydantic import Field

from app.core.schemas import CamelModel, ErrorResponse, ResponseScheme

Role = Literal["user", "assistant"]


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
    role: Role
    content: str
    response_scheme: ResponseScheme | None
    payload: dict | None
    created_at: str


class MessagePageData(CamelModel):
    items: list[MessageOut]
    next_cursor: str | None


class MessageSendRequest(CamelModel):
    content: str = Field(min_length=1, max_length=1000)


class MessageSendData(CamelModel):
    message: MessageOut
    thread_title: str | None


class MessageDeltaData(CamelModel):
    text: str


class MessageStreamEvent(CamelModel):
    delta: MessageDeltaData | None = None
    done: MessageSendData | None = None
    error: ErrorResponse | None = None
