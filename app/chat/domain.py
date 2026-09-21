import os
import threading
from datetime import datetime, timedelta
from enum import StrEnum

from pynamodb.attributes import MapAttribute, NumberAttribute, UnicodeAttribute

from app.core.clock import iso_utc, now_utc
from app.core.dynamo import DynamoModel


class StreamKind(StrEnum):
    DELTA = "delta"
    DONE = "done"
    ERROR = "error"

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_TIME_CHARS = 10
ULID_RANDOM_CHARS = 16

g_last_ulid = ""
g_ulid_lock = threading.Lock()


def _base32(value: int, length: int) -> str:
    """정수를 Crockford Base32 고정 길이 문자열로 인코딩"""
    chars = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        chars.append(CROCKFORD[remainder])
    return "".join(reversed(chars))


def _increment(value: str) -> str:
    """Base32 문자열 1 증가 자리올림 처리"""
    chars = list(value)
    for index in range(len(chars) - 1, -1, -1):
        position = CROCKFORD.index(chars[index])
        if position < len(CROCKFORD) - 1:
            chars[index] = CROCKFORD[position + 1]
            return "".join(chars)
        chars[index] = CROCKFORD[0]
    return "".join(chars)


def new_ulid(moment: datetime | None = None) -> str:
    """밀리초 시각 내장 시간순 정렬 가능 ULID 생성"""
    global g_last_ulid
    milliseconds = int((moment or now_utc()).timestamp() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    candidate = _base32(milliseconds, ULID_TIME_CHARS) + _base32(randomness, ULID_RANDOM_CHARS)
    with g_ulid_lock:
        if candidate <= g_last_ulid:
            candidate = _increment(g_last_ulid)
        g_last_ulid = candidate
    return candidate


def ttl_epoch(last_message_at: datetime, days: int) -> int:
    """마지막 활동 기준 메시지 보존 기한 epoch 초 계산"""
    return int((last_message_at + timedelta(days=days)).timestamp())


class ThreadRecord(DynamoModel):
    class Meta:
        table_name = "chat_threads"

    user_id = UnicodeAttribute(hash_key=True)
    thread_id = UnicodeAttribute(range_key=True)
    title = UnicodeAttribute(null=True)
    summary_text = UnicodeAttribute(null=True)
    summary_upto = UnicodeAttribute(null=True)
    last_message_at = UnicodeAttribute(null=True)
    expires_at = NumberAttribute(null=True)
    created_at = UnicodeAttribute()

    @classmethod
    def open(cls, user_id: str, now: datetime, ttl_days: int) -> "ThreadRecord":
        """생성 시각 기준 보존 기한 포함 새 스레드 조립"""
        return cls(
            user_id=user_id,
            thread_id=new_ulid(now),
            expires_at=ttl_epoch(now, ttl_days),
            created_at=iso_utc(now),
        )

    def is_expired(self, now: datetime) -> bool:
        """메시지 보존 기한 경과 여부 만료 판정"""
        if self.expires_at is None:
            return False
        return self.expires_at <= int(now.timestamp())


class MessageRecord(DynamoModel):
    class Meta:
        table_name = "chat_messages"

    thread_id = UnicodeAttribute(hash_key=True)
    message_id = UnicodeAttribute(range_key=True)
    user_id = UnicodeAttribute(null=True)
    role = UnicodeAttribute()
    content = UnicodeAttribute(null=True)
    response_scheme = UnicodeAttribute(null=True)
    payload = MapAttribute(null=True)
    created_at = UnicodeAttribute(null=True)


class UserSummaryRecord(DynamoModel):
    class Meta:
        table_name = "user_summaries"

    user_id = UnicodeAttribute(hash_key=True)
    summary_text = UnicodeAttribute(null=True)
    updated_at = UnicodeAttribute(null=True)
    created_at = UnicodeAttribute(null=True)


def _activity_key(thread: ThreadRecord) -> tuple[str, str]:
    """마지막 활동 시각과 스레드 아이디 정렬 키"""
    return thread.last_message_at or "", thread.thread_id


def sorted_by_activity(threads: list[ThreadRecord]) -> list[ThreadRecord]:
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


def to_message_view(message: MessageRecord) -> dict:
    """저장 메시지를 응답용 dict 로 변환"""
    is_user = message.role == "user"
    return {
        "message_id": message.message_id,
        "role": message.role,
        "content": message.content or "",
        "response_scheme": None if is_user else message.response_scheme,
        "payload": None if is_user else _payload_dict(message.payload),
        "created_at": message.created_at or "",
    }
