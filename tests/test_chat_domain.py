"""채팅 도메인 순수 로직 테스트 — ULID·TTL·레코드·정원·요약 판정."""
from datetime import datetime, timedelta, timezone

from app.chat import domain
from app.core import clock

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)


def _thread(
    thread_id: str, last_message_at: str | None, expires_at: int | None = None
) -> domain.ThreadRecord:
    return domain.ThreadRecord(
        user_id="u",
        thread_id=thread_id,
        last_message_at=last_message_at,
        expires_at=expires_at,
        created_at="2026-07-29T00:00:00Z",
    )


def test_new_ulid_is_26_chars_and_time_ordered():
    earlier = domain.new_ulid(NOW)
    later = domain.new_ulid(NOW + timedelta(seconds=1))
    assert len(earlier) == 26
    assert set(earlier) <= set(domain.CROCKFORD)
    # SK 정렬이 곧 시간순이어야 메시지 조회에 인덱스가 따로 필요 없다
    assert earlier < later


def test_new_ulid_is_monotonic_within_same_millisecond():
    # 한 요청에서 만드는 유저·어시스턴트 메시지는 같은 ms에 떨어질 수 있다.
    # 난수부에 순서를 맡기면 조회 시 대화가 뒤집힌다.
    ids = [domain.new_ulid(NOW) for _ in range(50)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_iso_utc_uses_z_suffix():
    # pydantic 기본 직렬화(+00:00)와 달라 팀 규약 표기를 직접 만든다
    assert clock.iso_utc(NOW) == "2026-07-29T12:00:00Z"


def test_ttl_epoch_adds_days():
    assert domain.ttl_epoch(NOW, 14) == int((NOW + timedelta(days=14)).timestamp())


def test_thread_record_open_sets_ttl_and_sparse_item():
    # 생성 규칙(ULID·TTL·생성 시각)은 레코드가 소유하고, None 필드는 저장 아이템에서 빠진다
    record = domain.ThreadRecord.open("u", NOW, 14)
    assert record.expires_at == domain.ttl_epoch(NOW, 14)
    assert record.created_at == "2026-07-29T12:00:00Z"
    item = record.to_item()
    assert "title" not in item
    assert "summary_upto" not in item


def test_is_expired_only_when_ttl_passed():
    past = int((NOW - timedelta(seconds=1)).timestamp())
    future = int((NOW + timedelta(days=1)).timestamp())
    assert _thread("a", None, past).is_expired(NOW) is True
    assert _thread("a", None, future).is_expired(NOW) is False
    # TTL 속성이 없는 항목은 만료로 보지 않는다
    assert _thread("a", None, None).is_expired(NOW) is False


def test_active_threads_drops_expired_and_sorts_by_recent_activity():
    past = int((NOW - timedelta(days=1)).timestamp())
    future = int((NOW + timedelta(days=1)).timestamp())
    threads = [
        _thread("old", "2026-07-01T00:00:00Z", future),
        _thread("dead", "2026-07-28T00:00:00Z", past),
        _thread("fresh", "2026-07-28T00:00:00Z", future),
    ]
    result = domain.active_threads(threads, NOW)
    assert [thread.thread_id for thread in result] == ["fresh", "old"]


def test_active_threads_puts_empty_thread_last():
    future = int((NOW + timedelta(days=1)).timestamp())
    threads = [_thread("empty", None, future), _thread("used", "2026-07-01T00:00:00Z", future)]
    result = domain.active_threads(threads, NOW)
    assert [thread.thread_id for thread in result] == ["used", "empty"]


def test_overflow_threads_evicts_only_when_at_quota():
    active = [_thread(str(i), f"2026-07-{20 + i:02d}T00:00:00Z") for i in range(5)]
    active = domain.active_threads(active, NOW)
    assert domain.overflow_threads(active[:4], 5) == []
    # 정원이 찬 상태에서 1개를 만들려면 가장 오래 미활동한 1개를 비운다
    evicted = domain.overflow_threads(active, 5)
    assert [thread.thread_id for thread in evicted] == ["0"]


def test_overflow_evicts_oldest_when_no_thread_has_activity():
    # 전부 미사용이면 last_message_at이 동률이라 thread_id(생성 시각 내장)로 갈라야 한다.
    # 안 그러면 가장 오래된 것 대신 가장 최근에 만든 스레드가 지워진다.
    active = domain.active_threads(
        [_thread(f"01A{index}", None) for index in range(5)], NOW
    )
    evicted = domain.overflow_threads(active, 5)
    assert [thread.thread_id for thread in evicted] == ["01A0"]


def test_fallback_title_truncates_and_collapses_whitespace():
    assert domain.fallback_title("  어깨   재활  루틴 ", 30) == "어깨 재활 루틴"
    assert domain.fallback_title("가" * 40, 30) == "가" * 30 + "…"


def test_to_message_view_nulls_scheme_and_payload_for_user():
    # §7 계약 — user 메시지는 responseScheme·payload가 항상 null
    stored = {
        "message_id": "M1",
        "role": "user",
        "content": "안녕",
        "response_scheme": "chart",
        "payload": {"x": []},
        "created_at": "2026-07-29T12:00:00Z",
    }
    view = domain.to_message_view(stored)
    assert view["response_scheme"] is None
    assert view["payload"] is None


def test_to_message_view_keeps_assistant_payload():
    stored = {
        "message_id": "M2",
        "role": "assistant",
        "content": "차트예요",
        "response_scheme": "chart",
        "payload": {"x": ["7/1"]},
        "created_at": "2026-07-29T12:00:00Z",
    }
    view = domain.to_message_view(stored)
    assert view["response_scheme"] == "chart"
    assert view["payload"] == {"x": ["7/1"]}
