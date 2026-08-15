"""채팅 도메인 요청/응답 모델 — 클라이언트 API 명세 §2~§7.

내부 snake_case ↔ 와이어 camelCase는 CamelModel alias가 처리한다. 로직·I/O 금지.
시각 필드는 datetime이 아닌 str이다 — 팀 규약 표기가 `2026-07-23T09:12:00Z`(Z 접미)인데
pydantic의 datetime 직렬화는 `+00:00`을 붙여 계약과 어긋난다. 문자열 조립은 core/clock.iso_utc가 맡는다.
"""
from typing import Literal

from pydantic import Field

from app.core.schemas import CamelModel, ErrorResponse, ResponseScheme

Role = Literal["user", "assistant"]


class ThreadOut(CamelModel):
    thread_id: str
    title: str | None             # 첫 발화 기반 자동 생성 — 첫 메시지 전이면 null
    last_message_at: str | None   # 빈 스레드면 null
    needs_deletion: bool          # 보존 기한(14일) 경과 — 클라는 진입 시 삭제 안내를 띄운다(§2)


class ThreadListData(CamelModel):
    """스레드 목록 응답의 data (§2) — items에 생성 가능 여부가 얹힌다."""

    items: list[ThreadOut]
    can_create_thread: bool   # 정원(10개) 미달 여부 — §3 생성 시도 전에 클라가 버튼을 잠근다


class ThreadCreated(CamelModel):
    thread_id: str
    created_at: str


class MessageOut(CamelModel):
    message_id: str
    role: Role
    content: str
    response_scheme: ResponseScheme | None   # user 메시지는 항상 null
    payload: dict | None                     # §7 계약 — scheme이 payload 존재·구조를 결정
    created_at: str


class MessagePageData(CamelModel):
    items: list[MessageOut]      # 페이지 안은 시간 오름차순 — 클라가 기존 목록 위에 그대로 끼운다 (§4.5)
    next_cursor: str | None      # 더 과거 페이지 커서. 처음까지 읽었으면 null


class MessageSendRequest(CamelModel):
    content: str = Field(min_length=1, max_length=1000)


class MessageSendData(CamelModel):
    """SSE `done` 이벤트의 data — 스트림 정본 (§4.6 규약 1)."""

    message: MessageOut
    thread_title: str | None   # 제목 생성·변경 시에만 값 — 목록 화면 갱신용


class MessageDeltaData(CamelModel):
    """SSE `delta` 이벤트의 data — 답변 본문 증분. 이어 붙이면 본문이 된다."""

    text: str


class MessageStreamEvent(CamelModel):
    """SSE 이벤트 3종의 문서화용 합집합 — 실제 와이어는 `event:`/`data:` 라인이다.

    스트리밍 응답이라 response_model로 표현할 수 없어 OpenAPI 문서화 전용으로 둔다
    (라우터 responses=). 클라는 event 이름으로 분기하고 data를 아래 타입으로 파싱한다.
    """

    delta: MessageDeltaData | None = None    # event: delta
    done: MessageSendData | None = None      # event: done — payload는 여기에만 실린다
    error: ErrorResponse | None = None       # event: error — 스트림 시작 후 실패
