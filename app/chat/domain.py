"""채팅 도메인 순수 로직 — I/O 없는 함수·상수만.

ULID 생성, TTL 계산, 만료 판정, 정원 초과 스레드 선별, 요약 시점 판정을 모은다.
저장 항목(dict)을 받아 계산만 하고 읽기·쓰기는 repository가 담당한다 (계층 규칙).
"""
import os
import threading
from datetime import datetime, timedelta

from app.core.clock import iso_utc, now_utc   # noqa: F401 — 기존 domain.iso_utc 참조 유지 (core/clock 이관)

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


def is_expired(thread: dict, now: datetime) -> bool:
    """만료 판정 — DynamoDB TTL 삭제는 며칠 지연될 수 있어 조회 단계에서 숨긴다."""
    expires_at = thread.get("expires_at")
    if expires_at is None:
        return False
    return int(expires_at) <= int(now.timestamp())


def _activity_key(thread: dict) -> tuple[str, str]:
    """정렬 키 — 마지막 활동, 동률이면 thread_id(ULID에 생성 시각 내장).

    한 번도 안 쓴 스레드는 last_message_at이 전부 null이라 키가 동률이 된다. 이때
    thread_id로 갈라주지 않으면 정렬이 입력 순서에 좌우돼 **가장 오래된 스레드 대신
    가장 최근 것이 LRU 삭제 대상이 된다**.
    """
    return thread.get("last_message_at") or "", thread.get("thread_id") or ""


def active_threads(threads: list[dict], now: datetime) -> list[dict]:
    """만료 제외 + 최근 활동순 정렬 — 목록 응답과 정원 판정이 같은 순서를 공유한다."""
    alive = [thread for thread in threads if not is_expired(thread, now)]
    return sorted(alive, key=_activity_key, reverse=True)


def overflow_threads(active: list[dict], limit: int) -> list[dict]:
    """새 스레드 자리를 만들기 위해 지울 스레드 — 활동순 뒤쪽(가장 오래 미활동)부터."""
    if len(active) < limit:
        return []
    return active[limit - 1:]


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
