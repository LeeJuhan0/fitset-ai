"""채팅 비즈니스 흐름 — 클라이언트 API 명세 §2~§6.

언제 무엇을 읽고 쓸지만 결정한다. DynamoDB 명령은 repository, 시각·정렬·만료 계산은
domain, LLM 호출은 agent가 맡는다. HTTPException 대신 도메인 예외를 던진다(계층 규칙).

repository는 전부 동기 함수라 asyncio.to_thread로 감싼다 — 이벤트 루프를 막지 않는다.
"""
import asyncio
import logging
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage

from app.agent import graph, prompts
from app.chat import domain, repository
from app.chat.schemas import (
    MessageOut,
    MessagePageData,
    MessageSendData,
    ThreadCreated,
    ThreadOut,
)
from app.core import clock, llm, ratelimit
from app.core.config import get_settings
from app.core.errors import (
    AiUnavailableError,
    DomainError,
    RateLimitedError,
    ThreadExpiredError,
    ThreadFullError,
    ThreadNotFoundError,
    ThreadQuotaExceededError,
)

logger = logging.getLogger("fitset")


async def list_threads(user_id: str) -> list[ThreadOut]:
    """스레드 목록 — 최대 10개, 최근 활동순 (§2).

    만료 스레드도 숨기지 않고 needsDeletion을 켜서 내보낸다 — 항목 삭제는 유저 몫이다(§4).
    정원까지 잘라서 반환한다(read-repair) — 동시 생성 TOCTOU 경쟁으로 저장소에
    11개가 생겨도 계약(최대 10)을 지킨다.
    """
    settings = get_settings()
    now = clock.now_utc()
    threads = await asyncio.to_thread(repository.list_threads, user_id)
    visible = domain.sorted_by_activity(threads)[: settings.max_threads_per_user]
    return [
        ThreadOut(
            thread_id=thread.thread_id,
            title=thread.title,
            last_message_at=thread.last_message_at,
            needs_deletion=thread.is_expired(now),
        )
        for thread in visible
    ]


async def create_thread(user_id: str) -> ThreadCreated:
    """스레드 생성 — 정원(10개) 도달 시 409로 거부 (§3). 만료 스레드도 정원을 차지한다."""
    settings = get_settings()
    now = clock.now_utc()
    threads = await asyncio.to_thread(repository.list_threads, user_id)
    if len(threads) >= settings.max_threads_per_user:
        raise ThreadQuotaExceededError()

    record = domain.ThreadRecord.open(user_id, now, settings.thread_ttl_days)
    await asyncio.to_thread(repository.put_thread, record)
    return ThreadCreated(thread_id=record.thread_id, created_at=record.created_at)


async def delete_thread(user_id: str, thread_id: str) -> None:
    """스레드 + 소속 메시지 전체 삭제 (§4). 없으면 404. 만료 스레드의 유일한 항목 삭제 경로."""
    await _load_thread(user_id, thread_id, allow_expired=True)
    await _purge_thread(user_id, thread_id)
    return None


async def list_messages(user_id: str, thread_id: str, limit: int, cursor: str | None) -> MessagePageData:
    """메시지 목록 — 커서 없는 첫 호출은 최신 limit개, 커서로 과거 방향 (§4.5). payload 포함.

    만료 스레드는 여기서 메시지를 지연 삭제하고 빈 페이지를 준다 — 스레드 항목은
    남겨 클라가 needsDeletion 안내를 띄우고, 항목 삭제는 유저의 DELETE(§4)만 한다.
    """
    thread = await _load_thread(user_id, thread_id, allow_expired=True)
    if thread.is_expired(clock.now_utc()):
        await asyncio.to_thread(repository.delete_messages, thread_id)
        return MessagePageData(items=[], next_cursor=None)
    items, next_cursor = await asyncio.to_thread(repository.messages_page, thread_id, limit, cursor)
    return MessagePageData(
        items=[MessageOut(**domain.to_message_view(item)) for item in items],
        next_cursor=next_cursor,
    )


@dataclass
class _Turn:
    """전송 턴의 준비물 — 프롬프트·최근 대화·병렬 제목 태스크."""

    system_prompt: str
    history: list
    title_task: asyncio.Task | None

    def cancel_title(self) -> None:
        if self.title_task is not None:
            self.title_task.cancel()

    async def title(self) -> str | None:
        return await self.title_task if self.title_task is not None else None


async def send_message_stream(user_id: str, thread_id: str, content: str):
    """메시지 전송 SSE 턴 (§4.6) — (StreamKind, 값) 튜플을 낸다 (DELTA=str, DONE=MessageSendData, ERROR=예외).

    검증과 유저 메시지 저장은 첫 이벤트 전에 끝난다 — 라우터가 첫 이벤트를 미리 당기므로
    여기서 던진 도메인 예외(404·409·429)와 첫 delta 전의 실패는 HTTP JSON으로 나간다.
    첫 delta 이후의 실패만 error 이벤트로 스트림에 실린다(§4.6 오류 규약).
    """
    turn = await _begin_turn(user_id, thread_id, content)

    result = None
    try:
        # graph 이벤트 2종 — DELTA(본문 조각)는 그대로 흘리고, 마지막 RESULT(AgentResult)만 잡는다
        async for kind, value in graph.run_turn_stream(user_id, turn.system_prompt, turn.history):
            if kind == graph.GraphEvent.DELTA:
                yield domain.StreamKind.DELTA, value
                continue
            result = value
    except Exception:
        logger.exception("agent stream failed")
        result = None
    if result is None:
        # 실패 턴은 assistant를 저장하지 않는다 — user 메시지는 남는다(§4.6 규약 5, 멱등성 ⑧)
        turn.cancel_title()
        yield domain.StreamKind.ERROR, AiUnavailableError()
        return

    try:
        data = await _persist_answer(user_id, thread_id, result, turn)
    except Exception:
        # 본문은 이미 흘러갔다 — 저장 실패를 숨기지 않고 error로 알린다(클라는 재시도 UI)
        logger.exception("assistant persist failed")
        yield domain.StreamKind.ERROR, DomainError()
        return
    yield domain.StreamKind.DONE, data


async def _begin_turn(user_id: str, thread_id: str, content: str) -> _Turn:
    """턴 준비 — 레이트리밋·스레드·상한 검증, user 메시지 저장, 프롬프트·제목 태스크 조립."""
    settings = get_settings()
    # 어떤 조회보다 먼저 — 한 턴이 Bedrock 호출 여러 번이라 abuse는 여기서 끊는다
    if not ratelimit.chat_limiter().allow(user_id):
        raise RateLimitedError()
    thread = await _load_thread(user_id, thread_id)

    user_summary, recent, total_messages = await asyncio.gather(
        asyncio.to_thread(repository.get_user_summary, user_id),
        asyncio.to_thread(repository.recent_messages, thread_id, settings.chat_context_turns),
        # 상한 검사용 전체 수 — COUNT 쿼리(키만 스캔)라 상한값 이상 커지지 않는다
        asyncio.to_thread(repository.count_messages, thread_id, None),
    )
    if total_messages >= settings.max_messages_per_thread:
        raise ThreadFullError()

    now = clock.now_utc()
    await asyncio.to_thread(
        repository.put_message,
        {
            "thread_id": thread_id,
            "message_id": domain.new_ulid(now),
            "user_id": user_id,
            "role": "user",
            "content": content,
            "response_scheme": None,
            "payload": None,
            "created_at": clock.iso_utc(now),
        },
    )

    return _Turn(
        system_prompt=prompts.chat_system(user_summary, thread.summary_text),
        history=[*_to_lc_messages(recent), HumanMessage(content=content)],
        # 제목이 없으면 첫 발화 기준으로 만든다 — 에이전트 스트림과 병렬이라 지연이 늘지 않는다
        title_task=asyncio.create_task(_make_title(content)) if thread.title is None else None,
    )


async def _persist_answer(
    user_id: str, thread_id: str, result: graph.AgentResult, turn: _Turn
) -> MessageSendData:
    """assistant 메시지 저장과 활동 갱신 — done 이벤트에 실을 payload를 만든다."""
    settings = get_settings()
    title = await turn.title()
    answered_at = clock.now_utc()
    assistant_message = {
        "thread_id": thread_id,
        "message_id": domain.new_ulid(answered_at),
        "user_id": user_id,
        "role": "assistant",
        "content": result.content,
        "response_scheme": result.response_scheme,
        "payload": result.payload,
        "created_at": clock.iso_utc(answered_at),
    }
    await asyncio.to_thread(repository.put_message, assistant_message)
    await asyncio.to_thread(
        repository.touch_thread,
        user_id,
        thread_id,
        clock.iso_utc(answered_at),
        domain.ttl_epoch(answered_at, settings.thread_ttl_days),
        title,
    )
    return MessageSendData(
        message=MessageOut(**domain.to_message_view(assistant_message)),
        thread_title=title,
    )


async def refresh_summaries(user_id: str, thread_id: str) -> None:
    """스레드 압축 + 유저 장기 요약 갱신 — 응답 경로와 분리된 백그라운드 작업."""
    settings = get_settings()
    thread = await asyncio.to_thread(repository.get_thread, user_id, thread_id)
    if thread is None:
        return None

    # 임계 검사는 COUNT 쿼리 — 매 전송마다 도는 경로라 파티션 전체를 읽지 않는다
    pending = await asyncio.to_thread(
        repository.count_messages, thread_id, thread.summary_upto
    )
    if not domain.needs_summary(pending, settings.summary_trigger_turns):
        return None

    # 임계 도달 시에만 미요약 꼬리를 읽는다 — 재요약은 (기존 요약 + 신규 메시지)의
    # 증분 병합이라 전체 대화를 실을 이유가 없다 (프롬프트가 O(대화 길이)로 커진다)
    fresh = await asyncio.to_thread(
        repository.messages_after, thread_id, thread.summary_upto
    )
    if not fresh:
        return None

    transcript = "\n".join(f"{item['role']}: {item.get('content', '')}" for item in fresh)
    system, user = prompts.summarize_thread(thread.summary_text, transcript)
    try:
        summary_text = await asyncio.to_thread(llm.complete, system, user, 600)
    except Exception:
        # 요약 실패는 대화를 막지 않는다 — 다음 턴에 다시 시도된다
        logger.warning("thread summary failed: %s", thread_id, exc_info=True)
        return None

    summary_text = summary_text.strip()
    await asyncio.to_thread(
        repository.save_summary, user_id, thread_id, summary_text, fresh[-1]["message_id"]
    )

    previous = await asyncio.to_thread(repository.get_user_summary, user_id)
    system, user = prompts.summarize_user(previous, summary_text)
    try:
        merged = await asyncio.to_thread(llm.complete, system, user, 500)
    except Exception:
        logger.warning("user summary failed: %s", user_id, exc_info=True)
        return None
    await asyncio.to_thread(
        repository.save_user_summary, user_id, merged.strip(), clock.iso_utc(clock.now_utc())
    )
    return None


async def _load_thread(
    user_id: str, thread_id: str, allow_expired: bool = False
) -> domain.ThreadRecord:
    """스레드 조회 — 없으면 404, 만료면 409 THREAD_EXPIRED(전송 차단).

    만료 스레드는 목록에 보이는 실체라 404가 아닌 409로 구분한다. 조회·삭제처럼
    만료 후에도 허용할 경로만 allow_expired로 통과시킨다.
    PK가 (user_id, thread_id)라 타 유저 접근은 여기서 404가 된다.
    """
    thread = await asyncio.to_thread(repository.get_thread, user_id, thread_id)
    if thread is None:
        raise ThreadNotFoundError()
    if not allow_expired and thread.is_expired(clock.now_utc()):
        raise ThreadExpiredError()
    return thread


async def _purge_thread(user_id: str, thread_id: str) -> None:
    """스레드 항목 → 메시지 순으로 지운다. 메시지 삭제가 실패해도 스레드는 이미 사라진다.

    남은 고아 메시지는 일 1회 배치가 정리한다(document-structure 삭제 경로 ②)
    — 클라에 500을 돌려주고 재시도시키는 것보다 낫다.
    """
    await asyncio.to_thread(repository.delete_thread, user_id, thread_id)
    try:
        await asyncio.to_thread(repository.delete_messages, thread_id)
    except Exception:
        logger.exception("message purge failed, leaving orphans for batch: %s", thread_id)
    return None


async def _make_title(first_message: str) -> str:
    """첫 발화 → 스레드 제목. LLM 실패 시 앞 N자로 폴백한다."""
    settings = get_settings()
    system, user = prompts.make_title(first_message)
    try:
        title = await asyncio.to_thread(llm.complete, system, user, 40)
    except Exception:
        logger.warning("title LLM failed, falling back to prefix", exc_info=True)
        return domain.fallback_title(first_message, settings.chat_title_max_length)
    title = title.strip().strip('"')
    if not title:
        return domain.fallback_title(first_message, settings.chat_title_max_length)
    return domain.fallback_title(title, settings.chat_title_max_length)


def _to_lc_messages(items: list[dict]) -> list:
    """저장 메시지 → LangChain 메시지. payload는 컨텍스트에 싣지 않는다(투영에서 제외됨)."""
    converted = []
    for item in items:
        text = item.get("content") or ""
        if item.get("role") == "assistant":
            converted.append(AIMessage(content=text))
        else:
            converted.append(HumanMessage(content=text))
    return converted
