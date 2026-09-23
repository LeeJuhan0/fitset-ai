import asyncio
import logging
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage

from app.agent import graph, prompts
from app.chat import repository, utils
from app.chat.enums import MessageRole, StreamKind
from app.chat.models import MessageRecord, ThreadRecord, UserSummaryRecord
from app.chat.exceptions import (
    ThreadExpiredError,
    ThreadFullError,
    ThreadNotFoundError,
    ThreadQuotaExceededError,
)
from app.chat.schemas import (
    MessageOut,
    MessagePageData,
    MessageSendData,
    ThreadCreated,
    ThreadListData,
    ThreadOut,
)
from app.core import clock, llm, ratelimit
from app.core.ulid import new_ulid
from app.core.config import get_settings
from app.core.exceptions import AiUnavailableError, DomainError, RateLimitedError

logger = logging.getLogger("fitset")


async def list_threads(user_id: str) -> ThreadListData:
    """최근 활동순 최대 정원 스레드 목록 조회"""
    settings = get_settings()
    now = clock.now_utc()
    threads = await asyncio.to_thread(repository.list_threads, user_id)
    visible = utils.sorted_by_activity(threads)[: settings.max_threads_per_user]
    return ThreadListData(
        items=[
            ThreadOut(
                thread_id=thread.thread_id,
                title=thread.title,
                last_message_at=thread.last_message_at,
                needs_deletion=thread.is_expired(now),
            )
            for thread in visible
        ],
        can_create_thread=len(threads) < settings.max_threads_per_user,
    )


async def create_thread(user_id: str) -> ThreadCreated:
    """정원 검사 후 새 스레드 생성"""
    settings = get_settings()
    now = clock.now_utc()
    threads = await asyncio.to_thread(repository.list_threads, user_id)
    if len(threads) >= settings.max_threads_per_user:
        raise ThreadQuotaExceededError()

    record = ThreadRecord.open(user_id, now, settings.thread_ttl_days)
    await asyncio.to_thread(repository.put_thread, record)
    return ThreadCreated(thread_id=record.thread_id, created_at=record.created_at)


async def delete_thread(user_id: str, thread_id: str) -> None:
    """스레드와 소속 메시지 전체 삭제"""
    await _load_thread(user_id, thread_id, allow_expired=True)
    await _purge_thread(user_id, thread_id)
    return None


async def list_messages(user_id: str, thread_id: str, limit: int, cursor: str | None) -> MessagePageData:
    """커서 기반 메시지 목록 만료 스레드는 지연 삭제"""
    thread = await _load_thread(user_id, thread_id, allow_expired=True)
    if thread.is_expired(clock.now_utc()):
        await asyncio.to_thread(repository.delete_messages, thread_id)
        return MessagePageData(items=[], next_cursor=None)
    items, next_cursor = await asyncio.to_thread(repository.messages_page, thread_id, limit, cursor)
    return MessagePageData(
        items=[MessageOut(**utils.to_message_view(item)) for item in items],
        next_cursor=next_cursor,
    )


@dataclass
class _Turn:
    system_prompt: str
    history: list
    title_task: asyncio.Task | None

    def cancel_title(self) -> None:
        """진행 중인 제목 생성 태스크 취소"""
        if self.title_task is not None:
            self.title_task.cancel()

    async def title(self) -> str | None:
        """제목 생성 태스크 완료 대기 후 제목 반환"""
        return await self.title_task if self.title_task is not None else None


async def send_message_stream(user_id: str, thread_id: str, content: str):
    """메시지 전송 턴 실행 스트림 이벤트 튜플 방출"""
    turn = await _begin_turn(user_id, thread_id, content)

    result = None
    try:
        async for kind, value in graph.run_turn_stream(user_id, turn.system_prompt, turn.history):
            if kind == graph.GraphEvent.DELTA:
                yield StreamKind.DELTA, value
                continue
            result = value
    except Exception:
        logger.exception("agent stream failed")
        result = None
    if result is None:
        turn.cancel_title()
        yield StreamKind.ERROR, AiUnavailableError()
        return

    try:
        data = await _persist_answer(user_id, thread_id, result, turn)
    except Exception:
        logger.exception("assistant persist failed")
        yield StreamKind.ERROR, DomainError()
        return
    yield StreamKind.DONE, data


async def _begin_turn(user_id: str, thread_id: str, content: str) -> _Turn:
    """레이트리밋 스레드 상한 검증 후 유저 메시지 저장"""
    settings = get_settings()
    if not ratelimit.chat_limiter().allow(user_id):
        raise RateLimitedError()
    thread = await _load_thread(user_id, thread_id)

    user_summary, recent, total_messages = await asyncio.gather(
        asyncio.to_thread(repository.get_user_summary, user_id),
        asyncio.to_thread(repository.recent_messages, thread_id, settings.chat_context_turns),
        asyncio.to_thread(repository.count_messages, thread_id, None),
    )
    if total_messages >= settings.max_messages_per_thread:
        raise ThreadFullError()

    now = clock.now_utc()
    await asyncio.to_thread(
        repository.put_message,
        MessageRecord(
            thread_id=thread_id,
            message_id=new_ulid(now),
            user_id=user_id,
            role=MessageRole.USER,
            content=content,
            created_at=clock.iso_utc(now),
        ),
    )

    return _Turn(
        system_prompt=prompts.chat_system(user_summary, thread.summary_text),
        history=[*_to_lc_messages(recent), HumanMessage(content=content)],
        title_task=asyncio.create_task(_make_title(content)) if thread.title is None else None,
    )


async def _persist_answer(
    user_id: str, thread_id: str, result: graph.AgentResult, turn: _Turn
) -> MessageSendData:
    """어시스턴트 메시지 저장과 스레드 활동 시각 갱신"""
    settings = get_settings()
    title = await turn.title()
    answered_at = clock.now_utc()
    assistant_message = MessageRecord(
        thread_id=thread_id,
        message_id=new_ulid(answered_at),
        user_id=user_id,
        role=MessageRole.ASSISTANT,
        content=result.content,
        response_scheme=result.response_scheme,
        payload=result.payload,
        created_at=clock.iso_utc(answered_at),
    )
    await asyncio.to_thread(repository.put_message, assistant_message)
    await asyncio.to_thread(
        repository.touch_thread,
        user_id,
        thread_id,
        clock.iso_utc(answered_at),
        utils.ttl_epoch(answered_at, settings.thread_ttl_days),
        title,
    )
    return MessageSendData(
        message=MessageOut(**utils.to_message_view(assistant_message)),
        thread_title=title,
    )


async def refresh_summaries(user_id: str, thread_id: str) -> None:
    """스레드 압축 요약과 유저 장기 요약 백그라운드 갱신"""
    settings = get_settings()
    thread = await asyncio.to_thread(repository.get_thread, user_id, thread_id)
    if thread is None:
        return None

    pending = await asyncio.to_thread(
        repository.count_messages, thread_id, thread.summary_upto
    )
    if not utils.needs_summary(pending, settings.summary_trigger_turns):
        return None

    fresh = await asyncio.to_thread(
        repository.messages_after, thread_id, thread.summary_upto
    )
    if not fresh:
        return None

    transcript = "\n".join(f"{item.role}: {item.content or ''}" for item in fresh)
    system, user = prompts.summarize_thread(thread.summary_text, transcript)
    summary_text = await asyncio.to_thread(llm.try_complete, "thread summary", system, user, 600)
    if summary_text is None:
        return None

    summary_text = summary_text.strip()
    await asyncio.to_thread(
        repository.save_summary, user_id, thread_id, summary_text, fresh[-1].message_id
    )

    previous = await asyncio.to_thread(repository.get_user_summary, user_id)
    system, user = prompts.summarize_user(previous, summary_text)
    merged = await asyncio.to_thread(llm.try_complete, "user summary", system, user, 500)
    if merged is None:
        return None
    await asyncio.to_thread(
        repository.put_user_summary,
        UserSummaryRecord.new(
            user_id, merged.strip(), clock.now_utc(), thread_id, fresh[-1].message_id
        ),
    )
    return None


async def _load_thread(
    user_id: str, thread_id: str, allow_expired: bool = False
) -> ThreadRecord:
    """스레드 조회 부재는 404 만료는 409"""
    thread = await asyncio.to_thread(repository.get_thread, user_id, thread_id)
    if thread is None:
        raise ThreadNotFoundError()
    if not allow_expired and thread.is_expired(clock.now_utc()):
        raise ThreadExpiredError()
    return thread


async def _purge_thread(user_id: str, thread_id: str) -> None:
    """스레드 항목 삭제 후 소속 메시지 정리"""
    await asyncio.to_thread(repository.delete_thread, user_id, thread_id)
    try:
        await asyncio.to_thread(repository.delete_messages, thread_id)
    except Exception:
        logger.exception("message purge failed, leaving orphans for batch: %s", thread_id)
    return None


async def _make_title(first_message: str) -> str:
    """첫 발화로 스레드 제목 생성 실패 시 폴백"""
    settings = get_settings()
    system, user = prompts.make_title(first_message)
    title = await asyncio.to_thread(llm.try_complete, "title", system, user, 40)
    if title is None:
        return utils.fallback_title(first_message, settings.chat_title_max_length)
    title = title.strip().strip('"')
    if not title:
        return utils.fallback_title(first_message, settings.chat_title_max_length)
    return utils.fallback_title(title, settings.chat_title_max_length)


def _to_lc_messages(items: list[MessageRecord]) -> list:
    """저장 메시지를 LangChain 메시지 목록으로 변환"""
    converted = []
    for item in items:
        text = item.content or ""
        if item.role == MessageRole.ASSISTANT:
            converted.append(AIMessage(content=text))
        else:
            converted.append(HumanMessage(content=text))
    return converted
