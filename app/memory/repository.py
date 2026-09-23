from app.memory.models import UserSummaryRecord


def get_user_summary(user_id: str) -> str | None:
    """유저 최신 장기 요약 텍스트 역순 Query 1건"""
    latest = UserSummaryRecord.query(user_id, scan_index_forward=False, limit=1)
    record = next(iter(latest), None)
    if record is None:
        return None
    return record.summary_text


def put_user_summary(record: UserSummaryRecord) -> None:
    """유저 장기 요약 이력 새 아이템 저장"""
    record.save()
