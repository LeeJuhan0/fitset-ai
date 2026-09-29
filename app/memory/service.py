import asyncio

from app.agent import prompts
from app.core import clock
from app.clients import bedrock
from app.memory import repository, utils
from app.memory.models import UserSummaryRecord
from app.memory.schemas import UserPreferences


async def latest_summary(user_id: str) -> str | None:
    """시스템 프롬프트 주입용 최신 운동 편향 문장"""
    record = await asyncio.to_thread(repository.latest_user_summary, user_id)
    if record is None or record.preferences is None:
        return None
    return utils.render_preferences(_preferences(record))


async def merge_thread_summary(user_id: str, thread_id: str, thread_summary: str, source_upto: str) -> None:
    """스레드 요약을 운동 편향에 병합해 새 이력 저장"""
    previous = await asyncio.to_thread(repository.latest_user_summary, user_id)
    system, user = prompts.summarize_user(_previous_json(previous), thread_summary)
    merged = await asyncio.to_thread(bedrock.try_structured, "user preferences", UserPreferences, system, user)
    if merged is None:
        return None
    record = UserSummaryRecord.new(user_id, merged.model_dump(), clock.now_utc(), thread_id, source_upto)
    await asyncio.to_thread(repository.put_user_summary, record)
    return None


def _preferences(record: UserSummaryRecord) -> UserPreferences:
    """저장된 운동 편향 Map 을 스키마 객체로"""
    return UserPreferences(**record.preferences.as_dict())


def _previous_json(record: UserSummaryRecord | None) -> str | None:
    """이전 이력의 운동 편향 JSON, 없으면 None"""
    if record is None or record.preferences is None:
        return None
    return _preferences(record).model_dump_json()
