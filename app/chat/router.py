"""채팅 도메인 라우터. HTTP 입출력만 담당한다.

목록 응답은 ListData(page 포함)가 아니라 ItemsData를 쓴다 — 클라이언트 API 명세 §공통이
`page` 객체를 내보내지 않기로 확정했기 때문(스레드 최대 5개·메시지 전체 로드).
요약 갱신은 응답을 보낸 뒤 BackgroundTasks로 돌린다 — 대화 지연에 얹지 않는다.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, Path, Response, status

from app import deps
from app.chat import service
from app.chat.schemas import (
    MessageOut,
    MessageSendData,
    MessageSendRequest,
    ThreadCreated,
    ThreadOut,
)
from app.core.schemas import ApiResponse, ItemsData

# 라우터의 모든 엔드포인트에 액세스 토큰 인증을 강제한다
router = APIRouter(
    prefix="/v1",
    tags=["chat"],
    dependencies=[Depends(deps.get_current_user_id)],
)


@router.get("/threads", response_model=ApiResponse[ItemsData[ThreadOut]])
async def list_threads(
    user_id: str = Depends(deps.get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[ItemsData[ThreadOut]]:
    """스레드 목록을 최근 활동순으로 반환한다."""
    threads = await service.list_threads(user_id)
    return ApiResponse(trace_id=trace_id, data=ItemsData(items=threads))


@router.post("/threads", status_code=status.HTTP_201_CREATED, response_model=ApiResponse[ThreadCreated])
async def create_thread(
    user_id: str = Depends(deps.get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[ThreadCreated]:
    """스레드를 생성한다. 정원 초과 시 서버가 가장 오래 미활동한 스레드를 정리한다."""
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


@router.get("/threads/{threadId}/messages", response_model=ApiResponse[ItemsData[MessageOut]])
async def list_messages(
    thread_id: str = Path(alias="threadId"),
    user_id: str = Depends(deps.get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[ItemsData[MessageOut]]:
    """대화 전체를 시간순으로 반환한다 (payload 포함)."""
    messages = await service.list_messages(user_id, thread_id)
    return ApiResponse(trace_id=trace_id, data=ItemsData(items=messages))


@router.post("/threads/{threadId}/messages", response_model=ApiResponse[MessageSendData])
async def send_message(
    request_body: MessageSendRequest,
    background_tasks: BackgroundTasks,
    thread_id: str = Path(alias="threadId"),
    user_id: str = Depends(deps.get_current_user_id),
    trace_id: str = Depends(deps.get_trace_id),
) -> ApiResponse[MessageSendData]:
    """메시지를 보내고 챗봇 응답을 반환한다. 요약 갱신은 응답 후 백그라운드로 돈다."""
    data = await service.send_message(user_id, thread_id, request_body.content)
    background_tasks.add_task(service.refresh_summaries, user_id, thread_id)
    return ApiResponse(trace_id=trace_id, data=data)
