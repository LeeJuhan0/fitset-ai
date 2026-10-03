import asyncio

from app.agent import prompts
from app.core import clock
from app.clients import bedrock
from app.memory import repository, utils
from app.memory.models import UserSummaryRecord
from app.memory.schemas import UserPreferences


async def latest_preferences(user_id: str) -> UserPreferences | None:
    """루틴 추천용 최신 운동 편향"""
    record = await asyncio.to_thread(repository.latest_user_summary, user_id)
    if record is None or record.preferences is None:
        return None
    return _preferences(record)


async def merge_preferences(user_id: str, thread_id: str, transcript: str, source_upto: str) -> None:
    """새 원본 메시지로 운동 편향 병합 후 새 이력 저장"""
    previous = await asyncio.to_thread(repository.latest_user_summary, user_id)
    system, user = prompts.summarize_user(_previous_json(previous), transcript)
    merged = await asyncio.to_thread(bedrock.try_structured, "user preferences", UserPreferences, system, user)
    if merged is None:
        return None
    user_text = "\n".join(line for line in transcript.splitlines() if line.startswith("user:"))
    merged = utils.grounded(merged, user_text, _previous_preferences(previous))
    record = UserSummaryRecord.new(user_id, merged.model_dump(), clock.now_utc(), thread_id, source_upto)
    await asyncio.to_thread(repository.put_user_summary, record)
    return None


def _preferences(record: UserSummaryRecord) -> UserPreferences:
    """저장된 운동 편향 Map 을 스키마 객체로"""
    return UserPreferences(**record.preferences)


def _previous_preferences(record: UserSummaryRecord | None) -> UserPreferences | None:
    """이전 이력의 운동 편향, 없으면 None"""
    if record is None or record.preferences is None:
        return None
    return _preferences(record)


def _previous_json(record: UserSummaryRecord | None) -> str | None:
    """이전 이력의 운동 편향 JSON, 없으면 None"""
    previous = _previous_preferences(record)
    if previous is None:
        return None
    return previous.model_dump_json()
