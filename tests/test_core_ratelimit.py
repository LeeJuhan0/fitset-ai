"""토큰버킷 레이트리밋 테스트 — 감사 F17. now 주입으로 시간을 결정적으로 제어한다."""
from app.core import ratelimit
from app.core.ratelimit import RateLimiter


def test_burst_up_to_capacity_then_denied():
    limiter = RateLimiter(per_minute=10)
    results = [limiter.allow("user", now=0.0) for _ in range(11)]
    assert results[:10] == [True] * 10
    assert results[10] is False


def test_refills_over_time():
    limiter = RateLimiter(per_minute=10)   # 6초당 1토큰
    for _ in range(10):
        limiter.allow("user", now=0.0)
    assert limiter.allow("user", now=0.0) is False
    assert limiter.allow("user", now=3.0) is False   # 아직 0.5토큰
    assert limiter.allow("user", now=6.1) is True    # 1토큰 충전됨


def test_keys_are_independent():
    limiter = RateLimiter(per_minute=1)
    assert limiter.allow("user-a", now=0.0) is True
    assert limiter.allow("user-a", now=0.0) is False
    # 다른 유저는 별도 버킷 — 한 유저의 소진이 남에게 번지지 않는다
    assert limiter.allow("user-b", now=0.0) is True


def test_prune_drops_only_refilled_buckets(monkeypatch):
    # 버킷 dict 자체가 메모리 누수가 되지 않는지 — 가득 찬(오래 쉰) 버킷만 지운다
    monkeypatch.setattr(ratelimit, "MAX_BUCKETS", 2)
    limiter = RateLimiter(per_minute=10)
    limiter.allow("idle", now=0.0)
    limiter.allow("active", now=600.0)
    limiter.allow("newcomer", now=600.0)   # 3개 > MAX 2 → prune 발동
    assert "idle" not in limiter._buckets      # 600초면 완충 — 지워도 정보 손실 없음
    assert "active" in limiter._buckets
    assert "newcomer" in limiter._buckets
