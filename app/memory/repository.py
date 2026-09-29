from app.core import dynamo
from app.memory.models import UserSummaryRecord


@dynamo.storage_errors
def latest_user_summary(user_id: str) -> UserSummaryRecord | None:
    """유저 최신 장기 요약 이력 역순 Query 1건"""
    latest = UserSummaryRecord.query(user_id, scan_index_forward=False, limit=1)
    return next(iter(latest), None)


@dynamo.storage_errors
def put_user_summary(record: UserSummaryRecord) -> None:
    """유저 장기 요약 이력 새 아이템 저장"""
    record.save()
