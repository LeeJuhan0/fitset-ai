import os
import threading
from datetime import datetime

from app.core.clock import now_utc

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
