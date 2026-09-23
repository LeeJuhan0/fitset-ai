from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pynamodb.attributes import MapAttribute

from app.chat.enums import MessageRole

if TYPE_CHECKING:
    from app.chat.models import MessageRecord, ThreadRecord


def ttl_epoch(last_message_at: datetime, days: int) -> int:
    """마지막 활동 기준 메시지 보존 기한 epoch 초 계산"""
    return int((last_message_at + timedelta(days=days)).timestamp())


def _activity_key(thread: "ThreadRecord") -> tuple[str, str]:
    """마지막 활동 시각과 스레드 아이디 정렬 키"""
    return thread.last_message_at or "", thread.thread_id


def sorted_by_activity(threads: list["ThreadRecord"]) -> list["ThreadRecord"]:
    """스레드 목록 최근 활동순 내림차순 정렬"""
    return sorted(threads, key=_activity_key, reverse=True)


def fallback_title(content: str, max_length: int) -> str:
    """제목 생성 실패 시 첫 발화 앞부분 폴백 제목"""
    stripped = " ".join(content.split())
    if len(stripped) <= max_length:
        return stripped
    return stripped[:max_length].rstrip() + "…"


def needs_summary(count: int, threshold: int) -> bool:
    """미요약 메시지 수 임계치 도달 여부 판정"""
    return count >= threshold


def _payload_dict(payload) -> dict | None:
    """페이로드 맵 속성을 순수 dict 로 변환"""
    if isinstance(payload, MapAttribute):
        return payload.as_dict()
    return payload


def to_message_view(message: "MessageRecord") -> dict:
    """저장 메시지를 응답용 dict 로 변환"""
    is_user = message.role == MessageRole.USER
    return {
        "message_id": message.message_id,
        "role": message.role,
        "content": message.content or "",
        "response_scheme": None if is_user else message.response_scheme,
        "payload": None if is_user else _payload_dict(message.payload),
        "created_at": message.created_at or "",
    }
