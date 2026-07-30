"""인스턴스 로컬 레이트리밋 — 유저별 토큰버킷. core 레이어.

목적은 LLM 호출 비용 abuse 차단(감사 F17)이지 정밀한 전역 통제가 아니다.
다중 인스턴스에선 허용량이 인스턴스 수만큼 느슨해지는 것을 수용한다 —
정밀해져야 하면 ALB/WAF 레이트 룰 또는 DynamoDB 카운터로 승격.

동기 함수 — async 서비스·threadpool 의존성 어디서 불려도 되도록 threading.Lock을 쓴다.
"""
import threading
import time
from functools import lru_cache

from app.core.config import get_settings

# 버킷 dict 자체가 메모리 누수가 되지 않게 정리를 시작하는 크기
MAX_BUCKETS = 10000


class RateLimiter:
    """토큰버킷 — 분당 per_minute개 충전, 최대 보유량(burst)도 per_minute.

    allow(key="user-A") 호출 (per_minute=5 예시)
       │
       ▼
    ┌─ with self._lock ──────────────────────────────────────────────┐
    │  ① 버킷 꺼내기  _buckets["user-A"] → (토큰 2.0, 마지막 10:00:00) │
    │                 없으면 새로 생성 → (가득참 5.0, 지금)            │
    │        ▼                                                       │
    │  ② 충전  흐른 30초 × 분당5/60 = +2.5 → min(5, 2.0+2.5) = 4.5    │
    │        ▼                                                       │
    │  ③ 판정  4.5 ≥ 1 ?                                             │
    │        ├─ 예 → 1개 차감 (3.5 저장) ────────► return True        │
    │        └─ 아니오 → 그대로 저장 ────────────► return False → 429 │
    │        ▼                                                       │
    │  ④ 개수 검사  len(_buckets) > MAX_BUCKETS ?                     │
    │        └─ 예 → _prune(now): 버킷별로                            │
    │             토큰 + 흐른시간×충전속도 ≥ capacity (가득 찼을 유저)? │
    │             ├─ 예: 오래 쉰 유저 → 삭제 (재방문 시 가득 찬        │
    │             │      새 버킷으로 시작하니 지워도 동일 동작)         │
    │             └─ 아니오: 최근 사용 유저 → 유지 (차감 기록 보존)     │
    └────────────────────────────────────────────────────────────────┘

    _buckets 상태 예 (prune 직전 → 직후)
      { A: (3.5, 방금), B: (0.2, 10초 전), C: (5.0, 3일 전), D: (1.0, 어제) }
      → C·D 삭제(이미 가득/가득 찼을 시간), A·B 유지
      { A: (3.5, 방금), B: (0.2, 10초 전) }

    충전은 저장해두는 게 아니라 읽는 순간 흐른 시간으로 계산한다(그래서 마지막
    시각을 같이 저장). prune은 "지워도 결과가 안 변하는" 가득 찬 버킷만 지운다.
    """

    def __init__(self, per_minute: int):
        self.rate = per_minute / 60.0
        self.capacity = float(per_minute)
        self._buckets: dict[str, tuple[float, float]] = {}   # key → (남은 토큰, 마지막 갱신 시각)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """토큰 1개 소비를 시도한다. now 주입은 테스트용."""
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
        """가득 찬(=한동안 안 쓴) 버킷 제거 — 재생성돼도 상태가 같아 정보 손실이 없다."""
        self._buckets = {
            key: (tokens, updated)
            for key, (tokens, updated) in self._buckets.items()
            if tokens + (current - updated) * self.rate < self.capacity
        }


@lru_cache
def chat_limiter() -> RateLimiter:
    """메시지 전송(LLM 포함) 유저별 제한."""
    return RateLimiter(get_settings().chat_messages_per_minute)


@lru_cache
def routine_limiter() -> RateLimiter:
    """루틴 생성(LLM 포함) 유저별 제한."""
    return RateLimiter(get_settings().routine_generations_per_minute)
