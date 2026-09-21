import logging

from pynamodb.exceptions import UpdateError

from app.chat.domain import MessageRecord, ThreadRecord, UserSummaryRecord

logger = logging.getLogger("fitset")

_CONTEXT_FIELDS = ["message_id", "role", "content", "created_at"]


def _condition_failed(error: UpdateError) -> bool:
    """조건부 쓰기 조건 실패 오류 여부 판정"""
    return error.cause_response_code == "ConditionalCheckFailedException"


def list_threads(user_id: str) -> list[ThreadRecord]:
    """유저 전체 스레드 목록 파티션 조회"""
    return list(ThreadRecord.query(user_id))


def get_thread(user_id: str, thread_id: str) -> ThreadRecord | None:
    """유저 스레드 단건 강한 일관성 조회"""
    try:
        return ThreadRecord.get(user_id, thread_id, consistent_read=True)
    except ThreadRecord.DoesNotExist:
        return None


def put_thread(record: ThreadRecord) -> None:
    """신규 스레드 레코드 DynamoDB 저장"""
    record.save()


def delete_thread(user_id: str, thread_id: str) -> None:
    """스레드 항목 단건 삭제 메시지는 별도"""
    ThreadRecord(user_id, thread_id).delete()


def touch_thread(
    user_id: str,
    thread_id: str,
    last_message_at: str,
    expires_at: int,
    title: str | None = None,
) -> None:
    """활동 시각 만료 기한 제목 조건부 갱신"""
    actions = [
        ThreadRecord.last_message_at.set(last_message_at),
        ThreadRecord.expires_at.set(expires_at),
    ]
    if title is not None:
        actions.append(ThreadRecord.title.set(ThreadRecord.title | title))
    try:
        ThreadRecord(user_id, thread_id).update(
            actions=actions, condition=ThreadRecord.thread_id.exists()
        )
    except UpdateError as error:
        if not _condition_failed(error):
            raise
        logger.info("touch skipped, thread already deleted: %s", thread_id)


def save_summary(user_id: str, thread_id: str, summary_text: str, summary_upto: str) -> None:
    """스레드 요약과 반영 지점 전진 조건부 갱신"""
    try:
        ThreadRecord(user_id, thread_id).update(
            actions=[
                ThreadRecord.summary_text.set(summary_text),
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


def put_message(message: MessageRecord) -> None:
    """메시지 레코드 단건 DynamoDB 저장"""
    message.save()


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


def count_messages(thread_id: str, after: str | None) -> int:
    """기준 ULID 이후 메시지 수 COUNT 조회"""
    condition = None
    if after is not None:
        condition = MessageRecord.message_id > after
    return MessageRecord.count(thread_id, range_key_condition=condition)


def delete_messages(thread_id: str) -> None:
    """스레드 소속 메시지 키 조회 후 배치 삭제"""
    keys = list(MessageRecord.query(thread_id, attributes_to_get=["thread_id", "message_id"]))
    with MessageRecord.batch_write() as batch:
        for key in keys:
            batch.delete(key)
    return None


def get_user_summary(user_id: str) -> str | None:
    """유저 장기 요약 텍스트 단건 조회"""
    try:
        return UserSummaryRecord.get(user_id).summary_text
    except UserSummaryRecord.DoesNotExist:
        return None


def save_user_summary(user_id: str, summary_text: str, updated_at: str) -> None:
    """유저 장기 요약 upsert 생성 시각은 최초 1회"""
    UserSummaryRecord(user_id).update(
        actions=[
            UserSummaryRecord.summary_text.set(summary_text),
            UserSummaryRecord.updated_at.set(updated_at),
            UserSummaryRecord.created_at.set(UserSummaryRecord.created_at | updated_at),
        ]
    )
