import asyncio

from app.agent import prompts
from app.core import clock
from app.clients import bedrock
from app.memory import repository
from app.memory.models import UserSummaryRecord


async def latest_summary(user_id: str) -> str | None:
    """시스템 프롬프트 주입용 최신 장기 요약"""
    return await asyncio.to_thread(repository.get_user_summary, user_id)


async def merge_thread_summary(user_id: str, thread_id: str, thread_summary: str, source_upto: str) -> None:
    """스레드 요약을 장기 요약에 병합해 새 이력 저장"""
    previous = await asyncio.to_thread(repository.get_user_summary, user_id)
    system, user = prompts.summarize_user(previous, thread_summary)
    merged = await asyncio.to_thread(bedrock.try_complete, "user summary", system, user, 500)
    if merged is None:
        return None
    record = UserSummaryRecord.new(user_id, merged.strip(), clock.now_utc(), thread_id, source_upto)
    await asyncio.to_thread(repository.put_user_summary, record)
    return None
