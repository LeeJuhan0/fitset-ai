"""시각 유틸 — UTC 기준을 한곳으로 모은다. core 레이어.

팀 규약 표기는 Z 접미(`2026-07-23T09:12:00Z`) — pydantic의 datetime 직렬화(+00:00)와
달라 문자열 조립을 여기서 직접 한다. 도메인을 가리지 않는 공용이라 core에 둔다
(감사 F18 — exercises가 chat.domain을 시간 때문에 임포트하던 수평 의존 해소).
"""
from datetime import datetime, timezone


def now_utc() -> datetime:
    """현재 UTC 시각 — 시각 기준을 한곳으로 모아 테스트에서 주입할 수 있게 한다."""
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime) -> str:
    """ISO 8601 UTC 문자열 — 팀 규약 표기(Z 접미)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
