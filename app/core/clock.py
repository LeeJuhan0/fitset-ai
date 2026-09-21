from datetime import datetime, timezone


def now_utc() -> datetime:
    """현재 UTC 시각 반환 테스트 주입 지점"""
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime) -> str:
    """시각을 Z 접미 ISO 8601 문자열로 변환"""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
