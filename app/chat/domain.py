"""채팅 도메인 순수 로직 — I/O 없는 함수·상수·레코드만.

ULID 생성, TTL 계산, 스레드 레코드(만료 판정 포함), 요약 시점 판정을 모은다.
계산만 하고 읽기·쓰기는 repository가 담당한다 (계층 규칙).
"""
import os
import threading
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel

from app.core.clock import iso_utc, now_utc


class StreamKind(StrEnum):
    """전송 스트림 의미 이벤트 어휘 — service가 내고 router가 SSE 프레임으로 직렬화한다."""

    DELTA = "delta"
    DONE = "done"
    ERROR = "error"

# Crockford Base32 — ULID 표준 알파벳 (I·L·O·U 제외로 오독 방지)
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_TIME_CHARS = 10     # 48비트 밀리초 타임스탬프
ULID_RANDOM_CHARS = 16   # 80비트 난수

# 단조 증가 보장용 — 같은 밀리초 안에서는 난수부가 순서를 정해버려 한 요청에서 만든
# 유저·어시스턴트 메시지의 SK 순서가 뒤집힐 수 있다. 직전 값보다 항상 크게 만든다.
g_last_ulid = ""
g_ulid_lock = threading.Lock()


def _base32(value: int, length: int) -> str:
    """정수를 Crockford Base32 고정 길이 문자열로 인코딩한다."""
    chars = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        chars.append(CROCKFORD[remainder])
    return "".join(reversed(chars))


def _increment(value: str) -> str:
    """Base32 문자열 +1 — 자리올림이 나면 앞자리로 넘긴다."""
    chars = list(value)
    for index in range(len(chars) - 1, -1, -1):
        position = CROCKFORD.index(chars[index])
        if position < len(CROCKFORD) - 1:
            chars[index] = CROCKFORD[position + 1]
            return "".join(chars)
        chars[index] = CROCKFORD[0]
    return "".join(chars)


def new_ulid(moment: datetime | None = None) -> str:
    """ULID 생성 — 앞 48비트가 밀리초라 사전순 정렬이 곧 시간순 정렬(SK 겸용).

    같은 밀리초에 두 번 부르면 난수부 대소가 무작위라 정렬이 뒤집힌다. 직전 발급값보다
    작거나 같으면 +1 해서 **단조 증가**를 보장한다 — 한 요청에서 만드는 유저·어시스턴트
    메시지가 항상 이 순서로 조회되어야 하기 때문.
    """
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
    """TTL 속성값 — 마지막 활동 + N일의 epoch 초. 메시지 수신마다 갱신해 타이머를 리셋한다."""
    return int((last_message_at + timedelta(days=days)).timestamp())


class ThreadRecord(BaseModel):
    """chat_threads 아이템 1건 — 저장 형태의 정본 (document-structure.md와 1:1).

    service↔repository 사이의 계약을 dict 대신 타입으로 강제한다 — 키 오타·필드 누락은
    생성 시점에, 저장 아이템의 타입 어긋남은 from_item에서 즉시 잡힌다.
    """

    user_id: str
    thread_id: str
    title: str | None = None
    summary_text: str | None = None
    summary_upto: str | None = None
    last_message_at: str | None = None
    expires_at: int | None = None
    created_at: str

    @classmethod
    def open(cls, user_id: str, now: datetime, ttl_days: int) -> "ThreadRecord":
        """새 스레드 조립 — 빈 스레드도 방치되면 지워지도록 생성 시각 기준 TTL을 건다."""
        return cls(
            user_id=user_id,
            thread_id=new_ulid(now),
            expires_at=ttl_epoch(now, ttl_days),
            created_at=iso_utc(now),
        )

    @classmethod
    def from_item(cls, item: dict) -> "ThreadRecord":
        """DynamoDB 아이템 → 레코드 — 스키마 드리프트를 읽는 즉시 드러낸다."""
        return cls.model_validate(item)

    def to_item(self) -> dict:
        """저장용 dict — None 필드는 속성 자체를 빼 sparse로 저장한다.

        NULL 타입으로 저장하면 if_not_exists가 NULL을 '있는 값'으로 보고 title을
        영영 채우지 못한다. 읽는 쪽은 전부 기본값 필드라 부재와 null이 동치다.
        """
        return self.model_dump(exclude_none=True)

    def is_expired(self, now: datetime) -> bool:
        """만료 판정 — DynamoDB TTL 삭제는 며칠 지연될 수 있어 조회 단계에서 숨긴다."""
        if self.expires_at is None:
            return False
        return self.expires_at <= int(now.timestamp())


def _activity_key(thread: ThreadRecord) -> tuple[str, str]:
    """정렬 키 — 마지막 활동, 동률이면 thread_id(ULID에 생성 시각 내장).

    한 번도 안 쓴 스레드는 last_message_at이 전부 null이라 키가 동률이 된다. 이때
    thread_id로 갈라주지 않으면 정렬이 입력 순서에 좌우돼 **목록 순서와 read-repair
    잘림 대상이 요청마다 달라진다**.
    """
    return thread.last_message_at or "", thread.thread_id


def active_threads(threads: list[ThreadRecord], now: datetime) -> list[ThreadRecord]:
    """만료 제외 + 최근 활동순 정렬 — 목록 응답과 정원 판정이 같은 순서를 공유한다."""
    alive = [thread for thread in threads if not thread.is_expired(now)]
    return sorted(alive, key=_activity_key, reverse=True)


def fallback_title(content: str, max_length: int) -> str:
    """제목 생성 LLM 실패 시 폴백 — 첫 발화 앞 N자."""
    stripped = " ".join(content.split())
    if len(stripped) <= max_length:
        return stripped
    return stripped[:max_length].rstrip() + "…"


def needs_summary(count: int, threshold: int) -> bool:
    """미요약 메시지가 임계치에 도달했는지 — 백그라운드 재요약 트리거."""
    return count >= threshold


def to_message_view(item: dict) -> dict:
    """저장 항목 → 응답용 dict. user 메시지는 §7 계약대로 scheme·payload를 null로 강제한다."""
    is_user = item.get("role") == "user"
    return {
        "message_id": item["message_id"],
        "role": item["role"],
        "content": item.get("content", ""),
        "response_scheme": None if is_user else item.get("response_scheme"),
        "payload": None if is_user else item.get("payload"),
        "created_at": item.get("created_at", ""),
    }
