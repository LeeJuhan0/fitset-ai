from datetime import datetime

from pynamodb.attributes import NumberAttribute, UnicodeAttribute

from app.chat.utils import ttl_epoch
from app.core.clock import iso_utc
from app.core.crypto import EncryptedJSONAttribute, EncryptedUnicodeAttribute
from app.core.dynamo import DynamoModel
from app.core.ulid import new_ulid


class ThreadRecord(DynamoModel):
    class Meta:
        table_name = "chat_threads"

    user_id = UnicodeAttribute(hash_key=True)
    thread_id = UnicodeAttribute(range_key=True)
    title = EncryptedUnicodeAttribute(null=True)
    summary = EncryptedJSONAttribute(null=True)
    summary_upto = UnicodeAttribute(null=True)
    last_message_at = UnicodeAttribute(null=True)
    expires_at = NumberAttribute(null=True)
    created_at = UnicodeAttribute()

    @classmethod
    def open(cls, user_id: str, now: datetime, ttl_days: int) -> "ThreadRecord":
        """생성 시각 기준 보존 기한 포함 새 스레드 조립"""
        return cls(
            user_id=user_id,
            thread_id=new_ulid(now),
            expires_at=ttl_epoch(now, ttl_days),
            created_at=iso_utc(now),
        )

    def is_expired(self, now: datetime) -> bool:
        """메시지 보존 기한 경과 여부 만료 판정"""
        if self.expires_at is None:
            return False
        return self.expires_at <= int(now.timestamp())


class MessageRecord(DynamoModel):
    class Meta:
        table_name = "chat_messages"

    thread_id = UnicodeAttribute(hash_key=True)
    message_id = UnicodeAttribute(range_key=True)
    user_id = UnicodeAttribute(null=True)
    role = UnicodeAttribute()
    content = EncryptedUnicodeAttribute(null=True)
    response_scheme = UnicodeAttribute(null=True)
    payload = EncryptedJSONAttribute(null=True)
    created_at = UnicodeAttribute(null=True)
    deleted_at = NumberAttribute(null=True)
