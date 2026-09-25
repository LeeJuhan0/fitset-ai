from datetime import timedelta

import pytest

from app.chat.enums import MessageRole
from app.chat.models import MessageRecord, ThreadRecord
from app.core.clock import iso_utc
from app.core.ulid import new_ulid
from tests.conftest import NOW, USER_ID

OTHER_USER_ID = "22222222-2222-2222-2222-222222222222"


def _open_thread(user_id: str, created_ago: timedelta, title: str | None = None) -> ThreadRecord:
    """지정 시각에 열린 스레드 저장"""
    record = ThreadRecord.open(user_id, NOW - created_ago, ttl_days=14)
    record.title = title
    record.last_message_at = iso_utc(NOW - created_ago)
    record.save()
    return record


@pytest.fixture
def thread(dynamo_tables):
    """한 시간 전에 연 제목 없는 대화"""
    return _open_thread(USER_ID, timedelta(hours=1))


@pytest.fixture
def expired_thread(dynamo_tables):
    """보존 기한 14일이 지난 대화"""
    return _open_thread(USER_ID, timedelta(days=15), title="지난 대화")


@pytest.fixture
def full_threads(dynamo_tables, settings):
    """정원만큼 채운 대화 목록"""
    return [
        _open_thread(USER_ID, timedelta(hours=hours), title=f"대화 {hours}")
        for hours in range(1, settings.max_threads_per_user + 1)
    ]


@pytest.fixture
def other_user_thread(dynamo_tables):
    """다른 사용자의 대화"""
    return _open_thread(OTHER_USER_ID, timedelta(hours=1), title="남의 대화")


@pytest.fixture
def conversation(thread):
    """세 턴 주고받은 대화 메시지 6개"""
    messages = []
    for turn in range(3):
        for offset, role in enumerate((MessageRole.USER, MessageRole.ASSISTANT)):
            moment = NOW - timedelta(minutes=30) + timedelta(minutes=turn * 2 + offset)
            message = MessageRecord(
                thread_id=thread.thread_id,
                message_id=new_ulid(moment),
                user_id=USER_ID,
                role=role,
                content=f"{turn + 1}번째 {role}",
                created_at=iso_utc(moment),
            )
            message.save()
            messages.append(message)
    return messages
