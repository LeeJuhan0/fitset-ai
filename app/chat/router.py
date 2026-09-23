from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Response, status
from fastapi.responses import StreamingResponse

from app import deps
from app.auth.deps import get_current_user_id
from app.chat import service, sse
from app.chat.enums import StreamKind
from app.chat.schemas import (
    MessagePageData,
    MessageSendRequest,
    SSE_RESPONSES,
    ThreadCreated,
    ThreadListData,
)
from app.core.schemas import ApiResponse

router = APIRouter(
    prefix="/ai/v1",
    tags=["chat"],
    dependencies=[Depends(get_current_user_id)],
)


@router.get("/threads", response_model=ApiResponse[ThreadListData])
async def list_threads(
    user_id: str = Depends(get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[ThreadListData]:
    """스레드 목록과 생성 가능 여부 최근 활동순 반환"""
    data = await service.list_threads(user_id)
    return ApiResponse(trace_id=trace_id, data=data)


@router.post("/threads", status_code=status.HTTP_201_CREATED, response_model=ApiResponse[ThreadCreated])
async def create_thread(
    user_id: str = Depends(get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[ThreadCreated]:
    """스레드 생성 정원 초과 시 409 응답"""
    thread = await service.create_thread(user_id)
    return ApiResponse(trace_id=trace_id, data=thread)


@router.delete("/threads/{threadId}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: str = Path(alias="threadId"),
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """스레드와 소속 메시지 삭제 후 204 응답"""
    await service.delete_thread(user_id, thread_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/threads/{threadId}/messages", response_model=ApiResponse[MessagePageData])
async def list_messages(
    thread_id: str = Path(alias="threadId"),
    cursor: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=50, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[MessagePageData]:
    """커서 기반 과거 방향 메시지 목록 반환"""
    data = await service.list_messages(user_id, thread_id, limit, cursor)
    return ApiResponse(trace_id=trace_id, data=data)


@router.post(
    "/threads/{threadId}/messages",
    response_class=sse.SseResponse,
    responses=SSE_RESPONSES,
)
async def send_message(
    request_body: MessageSendRequest,
    background_tasks: BackgroundTasks,
    thread_id: str = Path(alias="threadId"),
    user_id: str = Depends(get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> StreamingResponse:
    """메시지 전송 후 챗봇 응답 SSE 스트리밍"""
    events = service.send_message_stream(user_id, thread_id, request_body.content)
    first = await anext(events)
    first_kind, first_value = first
    if first_kind == StreamKind.ERROR:
        raise first_value

    async def sse_body():
        """하트비트 포함 이벤트를 SSE 프레임으로 순차 방출"""
        async for kind, value in sse.with_heartbeat(first, events, sse.TERMINAL_KINDS):
            if kind == sse.PING:
                yield ":ping\n\n"
                continue
            yield sse.frame(kind, value, trace_id)

    background_tasks.add_task(service.refresh_summaries, user_id, thread_id)
    return sse.SseResponse(
        sse_body(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=background_tasks,
    )
