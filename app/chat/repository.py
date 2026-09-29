import logging

from pynamodb.exceptions import TransactWriteError, UpdateError
from pynamodb.transactions import TransactWrite

from app.chat.models import MessageRecord, ThreadRecord
from app.core import dynamo

logger = logging.getLogger("fitset")

_CONTEXT_FIELDS = ["message_id", "role", "content", "created_at"]


def _condition_failed(error: UpdateError) -> bool:
    """조건부 쓰기 조건 실패 오류 여부 판정"""
    return error.cause_response_code == "ConditionalCheckFailedException"


@dynamo.storage_errors
def list_threads(user_id: str) -> list[ThreadRecord]:
    """유저 전체 스레드 목록 파티션 조회"""
    return list(ThreadRecord.query(user_id))


@dynamo.storage_errors
def get_thread(user_id: str, thread_id: str) -> ThreadRecord | None:
    """유저 스레드 단건 강한 일관성 조회"""
    try:
        return ThreadRecord.get(user_id, thread_id, consistent_read=True)
    except ThreadRecord.DoesNotExist:
        return None


@dynamo.storage_errors
def put_thread(record: ThreadRecord) -> None:
    """신규 스레드 레코드 DynamoDB 저장"""
    record.save()


@dynamo.storage_errors
def delete_thread(user_id: str, thread_id: str) -> None:
    """스레드 항목 단건 삭제 메시지는 별도"""
    ThreadRecord(user_id, thread_id).delete()


def _thread_gone(error: TransactWriteError) -> bool:
    """트랜잭션 취소 사유가 스레드 조건 실패인지"""
    reasons = error.cancellation_reasons or []
    return any(reason is not None and reason.code == "ConditionalCheckFailed" for reason in reasons)


@dynamo.storage_errors
def save_turn(
    user_message: MessageRecord,
    assistant_message: MessageRecord,
    user_id: str,
    last_message_at: str,
    expires_at: int,
    title: str | None = None,
) -> bool:
    """턴 원자 저장, 유저 어시스턴트 메시지와 스레드 활동 갱신, 스레드 삭제됐으면 False"""
    actions = [
        ThreadRecord.last_message_at.set(last_message_at),
        ThreadRecord.expires_at.set(expires_at),
    ]
    if title is not None:
        actions.append(ThreadRecord.title.set(ThreadRecord.title | title))
    try:
        with TransactWrite(connection=dynamo.connection()) as transaction:
            transaction.save(user_message)
            transaction.save(assistant_message)
            transaction.update(
                ThreadRecord(user_id, user_message.thread_id),
                actions=actions,
                condition=ThreadRecord.thread_id.exists(),
            )
    except TransactWriteError as error:
        if not _thread_gone(error):
            raise
        logger.info("turn skipped, thread already deleted: %s", user_message.thread_id)
        return False
    return True


@dynamo.storage_errors
def save_summary(user_id: str, thread_id: str, summary: dict, summary_upto: str) -> None:
    """스레드 요약과 반영 지점 전진 조건부 갱신"""
    try:
        ThreadRecord(user_id, thread_id).update(
            actions=[
                ThreadRecord.summary.set(summary),
                ThreadRecord.summary_upto.set(summary_upto),
            ],
            condition=(
                ThreadRecord.thread_id.exists() & ~(ThreadRecord.summary_upto >= summary_upto)
            ),
        )
    except UpdateError as error:
        if not _condition_failed(error):
            raise
        logger.info("summary skipped, thread deleted or stale upto: %s", thread_id)


@dynamo.storage_errors
def messages_page(
    thread_id: str, limit: int, cursor: str | None = None
) -> tuple[list[MessageRecord], str | None]:
    """최신부터 과거 방향 커서 기반 메시지 페이지 조회"""
    start_key = None
    if cursor is not None:
        start_key = {"thread_id": {"S": thread_id}, "message_id": {"S": cursor}}
    result = MessageRecord.query(
        thread_id,
        scan_index_forward=False,
        limit=limit,
        page_size=limit,
        last_evaluated_key=start_key,
    )
    items = list(reversed(list(result)))
    last_key = result.last_evaluated_key or {}
    next_cursor = last_key.get("message_id", {}).get("S")
    return items, next_cursor


@dynamo.storage_errors
def recent_messages(thread_id: str, limit: int) -> list[MessageRecord]:
    """LLM 컨텍스트용 최근 메시지 페이로드 제외 시간순 조회"""
    result = MessageRecord.query(
        thread_id,
        scan_index_forward=False,
        limit=limit,
        page_size=limit,
        attributes_to_get=_CONTEXT_FIELDS,
    )
    return list(reversed(list(result)))


@dynamo.storage_errors
def messages_after(thread_id: str, summary_upto: str | None) -> list[MessageRecord]:
    """요약 반영 지점 이후 미요약 메시지 시간순 조회"""
    condition = None
    if summary_upto is not None:
        condition = MessageRecord.message_id > summary_upto
    return list(
        MessageRecord.query(
            thread_id,
            range_key_condition=condition,
            scan_index_forward=True,
            attributes_to_get=_CONTEXT_FIELDS,
        )
    )


@dynamo.storage_errors
def count_messages(thread_id: str, after: str | None) -> int:
    """기준 ULID 이후 메시지 수 COUNT 조회"""
    condition = None
    if after is not None:
        condition = MessageRecord.message_id > after
    return MessageRecord.count(thread_id, range_key_condition=condition)


@dynamo.storage_errors
def delete_messages(thread_id: str) -> None:
    """스레드 소속 메시지 키 조회 후 배치 삭제"""
    keys = list(MessageRecord.query(thread_id, attributes_to_get=["thread_id", "message_id"]))
    with MessageRecord.batch_write() as batch:
        for key in keys:
            batch.delete(key)
    return None
