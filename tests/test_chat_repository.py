"""채팅 저장소 동시성 계약 테스트 — moto로 DynamoDB를 에뮬레이션한다.

FakeStore(서비스 테스트 대역)로는 UpdateItem의 upsert 의미론과 ConditionExpression을
증명할 수 없다 — CS 감사 F1(유령 부활)·F4(요약 Lost Update)·F5(제목 덮어쓰기)·
F10(배치 삭제)의 재현·회귀 테스트는 실제 API 의미론 위에서 돈다.
"""
import boto3
import pytest
from moto import mock_aws

from app.chat import repository
from app.core import dynamo

USER_ID = "11111111-1111-1111-1111-111111111111"
FAR_FUTURE = 4102444800   # 2100-01-01 epoch — 만료되지 않는 TTL


def _clear_caches():
    dynamo._resource.cache_clear()
    dynamo.get_chat_threads_table.cache_clear()
    dynamo.get_chat_messages_table.cache_clear()
    dynamo.get_user_summaries_table.cache_clear()


@pytest.fixture
def tables(monkeypatch):
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(key, "testing")
    with mock_aws():
        # lru_cache된 리소스가 moto 밖에서 만들어졌을 수 있어 양쪽에서 비운다
        _clear_caches()
        resource = boto3.resource("dynamodb", region_name="ap-northeast-2")
        resource.create_table(
            TableName="chat_threads",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "thread_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "thread_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        resource.create_table(
            TableName="chat_messages",
            KeySchema=[
                {"AttributeName": "thread_id", "KeyType": "HASH"},
                {"AttributeName": "message_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "thread_id", "AttributeType": "S"},
                {"AttributeName": "message_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        resource.create_table(
            TableName="user_summaries",
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield
    _clear_caches()


def _thread(thread_id: str = "01THREAD") -> dict:
    return {
        "user_id": USER_ID,
        "thread_id": thread_id,
        "title": None,
        "summary_text": None,
        "summary_upto": None,
        "last_message_at": None,
        "expires_at": FAR_FUTURE,
        "created_at": "2026-07-29T12:00:00Z",
    }


def _message(message_id: str) -> dict:
    return {
        "thread_id": "01THREAD",
        "message_id": message_id,
        "user_id": USER_ID,
        "role": "user",
        "content": "안녕",
        "response_scheme": None,
        "payload": None,
        "created_at": "2026-07-29T12:00:00Z",
    }


def test_put_thread_stores_sparse_nulls(tables):
    # F5 전제 — None이 NULL 타입 속성으로 저장되면 if_not_exists가 NULL을
    # '있는 값'으로 보고 title을 영영 채우지 못한다. 속성 자체가 없어야 한다.
    repository.put_thread(_thread())
    raw = dynamo.get_chat_threads_table().get_item(
        Key={"user_id": USER_ID, "thread_id": "01THREAD"}
    )["Item"]
    assert "title" not in raw
    assert "summary_upto" not in raw


def test_touch_thread_does_not_resurrect_deleted_thread(tables):
    # F1 — UpdateItem은 기본이 upsert: 메시지 전송(LLM 수십 초)과 삭제가 경쟁하면
    # 삭제 후 도착한 touch가 유령 스레드를 만들어낸다
    repository.put_thread(_thread())
    repository.delete_thread(USER_ID, "01THREAD")
    repository.touch_thread(USER_ID, "01THREAD", "2026-07-29T12:01:00Z", FAR_FUTURE, "유령")
    assert repository.get_thread(USER_ID, "01THREAD") is None


def test_save_summary_does_not_resurrect_deleted_thread(tables):
    # F1 — 백그라운드 요약 태스크가 삭제 뒤에 도착하는 경쟁
    repository.put_thread(_thread())
    repository.delete_thread(USER_ID, "01THREAD")
    repository.save_summary(USER_ID, "01THREAD", "요약", "01MSG")
    assert repository.get_thread(USER_ID, "01THREAD") is None


def test_touch_thread_first_title_wins(tables):
    # F5 — 첫 메시지 2건이 동시에 오면 둘 다 제목을 만든다. 먼저 정해진 쪽이 이겨야
    # 나중 쓰기가 덮어쓰는 Lost Update가 없다
    repository.put_thread(_thread())
    repository.touch_thread(USER_ID, "01THREAD", "t1", FAR_FUTURE, "먼저 온 제목")
    repository.touch_thread(USER_ID, "01THREAD", "t2", FAR_FUTURE, "나중 온 제목")
    assert repository.get_thread(USER_ID, "01THREAD")["title"] == "먼저 온 제목"


def test_save_summary_ignores_stale_upto(tables):
    # F4 — 늦게 끝난 묵은 요약 태스크가 최신 summary_upto를 되돌리면(역행)
    # 다음 요약 배치가 같은 구간을 다시 요약한다
    repository.put_thread(_thread())
    repository.save_summary(USER_ID, "01THREAD", "최신 요약", "01MSGB")
    repository.save_summary(USER_ID, "01THREAD", "묵은 요약", "01MSGA")
    thread = repository.get_thread(USER_ID, "01THREAD")
    assert thread["summary_upto"] == "01MSGB"
    assert thread["summary_text"] == "최신 요약"


def test_save_summary_sets_initial_and_advances(tables):
    # 조건식이 첫 요약(속성 부재)과 정방향 전진은 막지 않아야 한다
    repository.put_thread(_thread())
    repository.save_summary(USER_ID, "01THREAD", "첫 요약", "01MSGA")
    repository.save_summary(USER_ID, "01THREAD", "둘째 요약", "01MSGB")
    thread = repository.get_thread(USER_ID, "01THREAD")
    assert thread["summary_upto"] == "01MSGB"
    assert thread["summary_text"] == "둘째 요약"


def test_messages_after_returns_tail_in_order(tables):
    # F13 — 재요약 입력은 summary_upto 이후 꼬리만, 시간순
    for index in range(5):
        repository.put_message(_message(f"01MSG{index}"))
    tail = repository.messages_after("01THREAD", "01MSG2")
    assert [item["message_id"] for item in tail] == ["01MSG3", "01MSG4"]
    assert all("content" in item for item in tail)
    assert len(repository.messages_after("01THREAD", None)) == 5


def test_count_unsummarized_counts_only_after_upto(tables):
    # F6 — 임계 검사는 COUNT 쿼리로, summary_upto 이후 범위만 스캔한다
    for index in range(5):
        repository.put_message(_message(f"01MSG{index}"))
    assert repository.count_unsummarized("01THREAD", None) == 5
    assert repository.count_unsummarized("01THREAD", "01MSG2") == 2
    assert repository.count_unsummarized("01THREAD", "01MSG4") == 0


def test_delete_messages_removes_every_page(tables):
    # F10 회귀 가드 — batch_writer 하나로 25개 초과·페이지 경계를 안전하게 지운다
    for index in range(60):
        repository.put_message(_message(f"01MSG{index:03d}"))
    repository.delete_messages("01THREAD")
    assert repository.list_messages("01THREAD") == []
