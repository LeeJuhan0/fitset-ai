"""채팅 도메인 라우터. HTTP 입출력만 담당한다.

목록 응답에 `page` 객체는 없다(§공통). 스레드 목록은 최대 10개 전체 반환(ThreadListData — items + canCreateThread),
메시지 목록만 커서 페이지네이션(MessagePageData — cursor·limit·nextCursor, §4.5).
메시지 전송은 SSE 스트리밍(§4.6) — 이벤트의 프레임 직렬화(delta·done·error)는
전송 형식이므로 여기(HTTP 계층)가 맡고, 하트비트 삽입은 core/sse, service는 의미 이벤트만 낸다.
요약 갱신은 응답을 보낸 뒤 BackgroundTasks로 돌린다 — 대화 지연에 얹지 않는다.
"""
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Response, status
from fastapi.responses import StreamingResponse

from app import deps
from app.chat import service
from app.chat.domain import StreamKind
from app.chat.schemas import (
    MessagePageData,
    MessageSendRequest,
    MessageStreamEvent,
    ThreadCreated,
    ThreadListData,
)
from app.core import sse
from app.core.schemas import ApiResponse

# 라우터의 모든 엔드포인트에 액세스 토큰 인증을 강제한다
router = APIRouter(
    prefix="/ai/v1",
    tags=["chat"],
    dependencies=[Depends(deps.get_current_user_id)],
)


@router.get("/threads", response_model=ApiResponse[ThreadListData])
async def list_threads(
    user_id: str = Depends(deps.get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[ThreadListData]:
    """스레드 목록을 최근 활동순으로 반환한다. canCreateThread로 생성 가능 여부를 함께 준다."""
    data = await service.list_threads(user_id)
    return ApiResponse(trace_id=trace_id, data=data)


@router.post("/threads", status_code=status.HTTP_201_CREATED, response_model=ApiResponse[ThreadCreated])
async def create_thread(
    user_id: str = Depends(deps.get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[ThreadCreated]:
    """스레드를 생성한다. 정원(10개) 도달 시 409 THREAD_QUOTA_EXCEEDED로 거부한다."""
    thread = await service.create_thread(user_id)
    return ApiResponse(trace_id=trace_id, data=thread)


@router.delete("/threads/{threadId}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: str = Path(alias="threadId"),
    user_id: str = Depends(deps.get_current_user_id),
) -> Response:
    """스레드와 소속 메시지를 삭제한다. 본문이 없어 traceId는 X-Trace-Id 헤더로만 나간다."""
    await service.delete_thread(user_id, thread_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/threads/{threadId}/messages", response_model=ApiResponse[MessagePageData])
async def list_messages(
    thread_id: str = Path(alias="threadId"),
    # 커서는 이전 응답의 nextCursor 그대로 — 불투명 문자열 (빈 문자열만 검증 차단)
    cursor: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=50, ge=1, le=100),
    user_id: str = Depends(deps.get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[MessagePageData]:
    """메시지 목록 — 커서 없는 첫 호출은 최신 limit개, 커서로 과거 방향 (payload 포함)."""
    data = await service.list_messages(user_id, thread_id, limit, cursor)
    return ApiResponse(trace_id=trace_id, data=data)


# 스트림을 끝내는 이벤트 — 이 둘 뒤에는 더 이상 이벤트가 오지 않는다
_TERMINAL_KINDS = (StreamKind.DONE, StreamKind.ERROR)


def _sse_frame(kind: StreamKind, value, trace_id: str) -> str:
    """의미 이벤트 → SSE 프레임 (§4.6 이벤트 3종)."""
    if kind == StreamKind.DELTA:
        data = {"text": value}
    elif kind == StreamKind.DONE:
        data = value.model_dump(by_alias=True)
    else:
        data = {"traceId": trace_id, "error": {"code": value.code, "message": value.message, "details": []}}
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# 스트리밍 응답이라 response_model로 표현할 수 없다 — Swagger에 이벤트 스키마가 비지 않게
# responses로 직접 문서화한다(모델은 MessageStreamEvent, 예시는 실제 와이어 형식)
_SSE_EXAMPLE = (
    'event: delta\ndata: {"text":"스쿼트를 빼고"}\n\n'
    'event: delta\ndata: {"text":" 레그프레스로 대체했어요."}\n\n'
    ':ping\n\n'
    'event: done\ndata: {"message":{"messageId":"01J...","role":"assistant",'
    '"content":"스쿼트를 빼고 레그프레스로 대체했어요.","responseScheme":"routine",'
    '"payload":{"slug":"...","exercises":[{"exerciseId":"11f1...","slug":"barbell-squat"}]},'
    '"createdAt":"2026-08-05T09:01:00Z"},"threadTitle":"하체 루틴 조정"}\n\n'
)
_SSE_RESPONSES = {
    200: {
        "model": MessageStreamEvent,
        "description": (
            "SSE 스트림 (text/event-stream). 이벤트 3종 — "
            "`delta`(본문 증분), `done`(정본 · payload는 여기에만), `error`(시작 후 실패). "
            "무토큰 구간에는 하트비트 코멘트(`:ping`)가 온다. traceId는 X-Trace-Id 헤더."
        ),
        "content": {"text/event-stream": {"example": _SSE_EXAMPLE}},
    },
}


@router.post(
    "/threads/{threadId}/messages",
    response_class=sse.SseResponse,
    responses=_SSE_RESPONSES,
)
async def send_message(
    request_body: MessageSendRequest,
    background_tasks: BackgroundTasks,
    thread_id: str = Path(alias="threadId"),
    user_id: str = Depends(deps.get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> StreamingResponse:
    """메시지를 보내고 챗봇 응답을 SSE로 스트리밍한다 (§4.6). 요약 갱신은 스트림 종료 후 백그라운드."""
    events = service.send_message_stream(user_id, thread_id, request_body.content)
    # 첫 이벤트를 미리 당긴다 — 검증·유저 메시지 저장이 여기서 끝나므로 도메인 예외
    # (404·409·429)와 첫 delta 전의 에이전트 실패는 스트림 없이 HTTP 오류 JSON으로 나간다
    first = await anext(events)
    first_kind, first_value = first
    if first_kind == StreamKind.ERROR:
        raise first_value

    async def sse_body():
        async for kind, value in sse.with_heartbeat(first, events, _TERMINAL_KINDS):
            if kind == sse.PING:
                yield ":ping\n\n"
                continue
            yield _sse_frame(kind, value, trace_id)

    background_tasks.add_task(service.refresh_summaries, user_id, thread_id)
    return sse.SseResponse(
        sse_body(),
        # 중간 프록시 버퍼링 금지 — 조각이 모여서 한 번에 가면 스트리밍이 무의미하다
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=background_tasks,
    )
