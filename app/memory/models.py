from datetime import datetime

from pynamodb.attributes import MapAttribute, UnicodeAttribute

from app.core.clock import iso_utc
from app.core.dynamo import DynamoModel
from app.core.ulid import new_ulid


class UserSummaryRecord(DynamoModel):
    class Meta:
        table_name = "user_summaries"

    user_id = UnicodeAttribute(hash_key=True)
    summary_id = UnicodeAttribute(range_key=True)
    summary_text = UnicodeAttribute(null=True)
    personality = MapAttribute(null=True)
    created_at = UnicodeAttribute()
    source_thread_id = UnicodeAttribute(null=True)
    source_upto = UnicodeAttribute(null=True)

    @classmethod
    def new(
        cls,
        user_id: str,
        summary_text: str,
        now: datetime,
        source_thread_id: str,
        source_upto: str,
        personality: dict | None = None,
    ) -> "UserSummaryRecord":
        """생성 시각 ULID 정렬 키 포함 새 요약 이력 조립"""
        return cls(
            user_id=user_id,
            summary_id=new_ulid(now),
            summary_text=summary_text,
            personality=personality,
            created_at=iso_utc(now),
            source_thread_id=source_thread_id,
            source_upto=source_upto,
        )
