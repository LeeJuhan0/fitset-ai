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


def _message(role: MessageRole, minutes_ago: int, content: str | None, **fields) -> MessageRecord:
    """저장하지 않은 메시지 레코드 조립"""
    moment = NOW - timedelta(minutes=minutes_ago)
    return MessageRecord(
        thread_id="thread",
        message_id=new_ulid(moment),
        user_id=USER_ID,
        role=role,
        content=content,
        created_at=iso_utc(moment),
        **fields,
    )


@pytest.fixture
def unanswered_history() -> list[MessageRecord]:
    """답변 실패로 유저 메시지만 남은 턴 섞인 이력"""
    return [
        _message(MessageRole.USER, 10, "스쿼트 몇 세트 해요"),
        _message(MessageRole.ASSISTANT, 9, "5세트요"),
        _message(MessageRole.USER, 8, "벤치는요"),
        _message(MessageRole.USER, 7, "데드는요"),
        _message(MessageRole.ASSISTANT, 6, "3세트요"),
        _message(MessageRole.USER, 5, "마지막 질문"),
    ]


@pytest.fixture
def chart_message() -> MessageRecord:
    """주별 운동 횟수 차트를 실은 답변 메시지"""
    payload = {"chartType": "bar", "x": ["9/14", "9/21"], "series": [{"name": "운동 횟수", "values": [2, 1]}]}
    return _message(MessageRole.ASSISTANT, 1, "이번 달 기록이에요", response_scheme="chart", payload=payload)


@pytest.fixture
def activity_threads() -> list[ThreadRecord]:
    """활동 시각 같음 다름 없음 섞인 저장 전 대화 목록"""
    return [
        ThreadRecord(user_id=USER_ID, thread_id="01A", last_message_at="2026-09-25T01:00:00Z", created_at="2026-09-24T00:00:00Z"),
        ThreadRecord(user_id=USER_ID, thread_id="01B", last_message_at="2026-09-25T02:00:00Z", created_at="2026-09-24T00:00:00Z"),
        ThreadRecord(user_id=USER_ID, thread_id="01C", last_message_at=None, created_at="2026-09-24T00:00:00Z"),
        ThreadRecord(user_id=USER_ID, thread_id="01D", last_message_at="2026-09-25T01:00:00Z", created_at="2026-09-24T00:00:00Z"),
    ]
