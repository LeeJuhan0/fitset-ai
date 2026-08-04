"""채팅 도메인 라우터. HTTP 입출력만 담당한다.

목록 응답에 `page` 객체는 없다(§공통). 스레드 목록은 최대 5개 전체 반환(ItemsData),
메시지 목록만 커서 페이지네이션(MessagePageData — cursor·limit·nextCursor, §4.5).
메시지 전송은 SSE 스트리밍(§4.6) — 이벤트 직렬화(delta·done·error)와 하트비트는
전송 형식이므로 여기(HTTP 계층)가 맡고, service는 의미 이벤트만 낸다.
요약 갱신은 응답을 보낸 뒤 BackgroundTasks로 돌린다 — 대화 지연에 얹지 않는다.
"""
import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Response, status
from fastapi.responses import StreamingResponse

from app import deps
from app.chat import service
from app.chat.schemas import (
    MessagePageData,
    MessageSendRequest,
    ThreadCreated,
    ThreadOut,
)
from app.core.schemas import ApiResponse, ItemsData

# 라우터의 모든 엔드포인트에 액세스 토큰 인증을 강제한다
router = APIRouter(
    prefix="/ai/v1",
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


# 무토큰 구간(툴 실행) 연결 유지 — §4.6 규약 2는 "15초 이내 간격"이다
HEARTBEAT_SECONDS = 15


def _sse_frame(kind: str, value, trace_id: str) -> str:
    """의미 이벤트 → SSE 프레임 (§4.6 이벤트 3종)."""
    if kind == "delta":
        data = {"text": value}
    elif kind == "done":
        data = value.model_dump(by_alias=True)
    else:
        data = {"traceId": trace_id, "error": {"code": value.code, "message": value.message, "details": []}}
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/threads/{threadId}/messages")
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
    if first[0] == "error":
        raise first[1]

    async def sse_body():
        event = first
        while True:
            yield _sse_frame(event[0], event[1], trace_id)
            if event[0] in ("done", "error"):
                return
            # wait_for는 타임아웃 시 대기를 취소해 제너레이터를 깨뜨린다 — wait로 하트비트를 낀다
            task = asyncio.ensure_future(anext(events))
            while True:
                finished, _ = await asyncio.wait({task}, timeout=HEARTBEAT_SECONDS)
                if finished:
                    break
                yield ":ping\n\n"
            try:
                event = task.result()
            except StopAsyncIteration:
                return

    background_tasks.add_task(service.refresh_summaries, user_id, thread_id)
    return StreamingResponse(
        sse_body(),
        media_type="text/event-stream",
        # 중간 프록시 버퍼링 금지 — 조각이 모여서 한 번에 가면 스트리밍이 무의미하다
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=background_tasks,
    )
