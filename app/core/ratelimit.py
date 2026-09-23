import threading
import time

MAX_BUCKETS = 10000


class RateLimiter:
    def __init__(self, per_minute: int):
        """분당 충전량 기준 토큰버킷 용량과 저장소 초기화"""
        self.rate = per_minute / 60.0
        self.capacity = float(per_minute)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """경과 시간만큼 충전 후 토큰 1개 소비 시도"""
        current = time.monotonic() if now is None else now
        with self._lock:
            tokens, updated = self._buckets.get(key, (self.capacity, current))
            tokens = min(self.capacity, tokens + (current - updated) * self.rate)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            self._buckets[key] = (tokens, current)
            if len(self._buckets) > MAX_BUCKETS:
                self._prune(current)
            return allowed

    def _prune(self, current: float) -> None:
        """가득 찬 유휴 버킷 제거로 메모리 상한 유지"""
        self._buckets = {
            key: (tokens, updated)
            for key, (tokens, updated) in self._buckets.items()
            if tokens + (current - updated) * self.rate < self.capacity
        }

