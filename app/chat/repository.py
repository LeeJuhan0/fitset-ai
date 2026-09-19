"""채팅 저장소 — DynamoDB 명령이 사는 유일한 곳 (docs/document-structure.md).

chat_threads(PK=user_id, SK=thread_id) · chat_messages(PK=thread_id, SK=message_id) ·
user_summaries(PK=user_id). 모델은 domain의 PynamoDB 레코드 — 여기는 그 모델로 읽고 쓰기만 한다.
전 함수 동기 — async 서비스에서 asyncio.to_thread로 감싼다.
조합·판단은 하지 않는다(계층 규칙) — 정렬·만료 판정은 domain, 흐름은 service.

`role`·`content`·`data` 등 DynamoDB 예약어의 #별칭 우회는 PynamoDB가 표현식을 만들며 알아서 한다.

스레드 갱신은 전부 조건부 쓰기다 — UpdateItem은 기본이 upsert라, 조건 없이는
삭제와 경쟁한 갱신(메시지 전송·백그라운드 요약)이 유령 스레드를 부활시킨다.
"""
import logging

from pynamodb.exceptions import UpdateError

from app.chat.domain import MessageRecord, ThreadRecord, UserSummaryRecord

logger = logging.getLogger("fitset")

# 컨텍스트 로드용 투영 — payload는 화면 렌더링에만 쓰이므로 LLM 경로에서 뺀다
_CONTEXT_FIELDS = ["message_id", "role", "content", "created_at"]


def _condition_failed(error: UpdateError) -> bool:
    """조건부 쓰기의 조건 실패인지 — 그 외 갱신 오류는 호출부가 그대로 올린다."""
    return error.cause_response_code == "ConditionalCheckFailedException"


def list_threads(user_id: str) -> list[ThreadRecord]:
    """유저의 전체 스레드 — 최대 10개라 정렬·만료 필터는 메모리에서 한다(GSI 불필요)."""
    return list(ThreadRecord.query(user_id))


def get_thread(user_id: str, thread_id: str) -> ThreadRecord | None:
    """스레드 단건. PK가 (user_id, thread_id)라 남의 스레드는 애초에 조회되지 않는다."""
    try:
        # 생성(201) 직후 바로 온 전송이 EC 읽기로 404 나지 않게 — 단건이라 비용 무시 가능
        return ThreadRecord.get(user_id, thread_id, consistent_read=True)
    except ThreadRecord.DoesNotExist:
        return None


def put_thread(record: ThreadRecord) -> None:
    """스레드 신규 생성 — None 필드는 모델이 속성째 빼고 sparse로 저장한다."""
    record.save()


def delete_thread(user_id: str, thread_id: str) -> None:
    """스레드 항목만 삭제 — 소속 메시지는 delete_messages가 따로 지운다."""
    ThreadRecord(user_id, thread_id).delete()


def touch_thread(
    user_id: str,
    thread_id: str,
    last_message_at: str,
    expires_at: int,
    title: str | None = None,
) -> None:
    """활동 시각과 TTL을 갱신한다. 스레드가 이미 삭제됐으면 조용히 무시한다.

    attribute_exists 조건 — 전송(LLM 수십 초)과 삭제가 경쟁하면 삭제 뒤 도착한
    touch가 upsert로 유령 스레드를 부활시키므로 막는다.
    title은 if_not_exists — 첫 메시지 2건이 경쟁해도 먼저 정해진 제목이 이긴다.
    """
    actions = [
        ThreadRecord.last_message_at.set(last_message_at),
        ThreadRecord.expires_at.set(expires_at),
    ]
    if title is not None:
        # 속성 | 값 은 PynamoDB의 if_not_exists 표기다
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
    """스레드 압축 요약과 반영 지점(ULID)을 갱신한다 — 백그라운드 요약 경로.

    조건 두 가지: 스레드가 살아있고(upsert 부활 방지), summary_upto가 전진할 때만.
    NOT(summary_upto >= 새 값)은 속성 부재면 참이라 첫 요약도 통과하고, 늦게 끝난 묵은
    태스크(Lost Update·역행)는 조건 실패로 버려진다.
    """
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
    """최신부터 limit개 — 과거 방향 커서 페이지 (클라이언트 API 명세 §4.5).

    (시간 오름차순 페이지, next_cursor)를 돌려준다. 커서는 message_id(ULID)를
    ExclusiveStartKey로 그대로 쓴다 — 역순 조회라 그 키보다 과거만 읽는다.
    LastEvaluatedKey가 없으면 파티션 처음까지 읽은 것이라 next_cursor가 None이다.
    Limit에 정확히 걸려 끝나면 커서가 한 번 더 올 수 있는데, 다음 요청이
    빈 페이지 + None으로 끝나므로 계약(§4.5)을 깨지 않는다.
    """
    start_key = None
    if cursor is not None:
        start_key = {"thread_id": {"S": thread_id}, "message_id": {"S": cursor}}
    # page_size를 limit과 맞춰 Query 1회로 끝낸다 — 기본값이면 1MB까지 읽고 버린다
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
    """LLM 컨텍스트용 최근 N턴 — payload 제외 투영, 역순 조회 후 뒤집어 시간순으로 돌려준다."""
    result = MessageRecord.query(
        thread_id,
        scan_index_forward=False,
        limit=limit,
        page_size=limit,
        attributes_to_get=_CONTEXT_FIELDS,
    )
    return list(reversed(list(result)))


def put_message(message: MessageRecord) -> None:
    """메시지 1건 저장."""
    message.save()


def messages_after(thread_id: str, summary_upto: str | None) -> list[MessageRecord]:
    """summary_upto 이후 메시지 — 재요약 입력. payload 제외 투영, 시간순.

    전체 대화가 아니라 미요약 꼬리만 읽는다 — 재요약은 (기존 summary_text + 신규 메시지)의
    증분 병합이다(document-structure). 전체를 실으면 프롬프트가 대화 길이에 비례해 커진다.
    1MB 상한에 잘린 페이지는 PynamoDB 이터레이터가 이어읽는다.
    """
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
    """after(ULID) 이후 메시지 수, None이면 전체 — Select COUNT라 항목 본문을 전송받지 않는다.

    상한 검사(after=None)와 요약 임계 검사(after=summary_upto)가 함께 쓴다.
    매 전송마다 돌므로 파티션 전체를 읽지 않고, ULID 범위 조건으로 스캔 구간도 좁힌다.
    1MB 스캔 상한에 잘려도 PynamoDB count가 끝까지 이어 센다.
    """
    condition = None
    if after is not None:
        condition = MessageRecord.message_id > after
    return MessageRecord.count(thread_id, range_key_condition=condition)


def delete_messages(thread_id: str) -> None:
    """스레드 소속 메시지 전량 삭제 — 키만 조회해 배치로 지운다.

    batch_write가 25개 flush와 UnprocessedItems 재시도를 알아서 하므로
    수동 청크를 두지 않는다.
    """
    keys = list(MessageRecord.query(thread_id, attributes_to_get=["thread_id", "message_id"]))
    with MessageRecord.batch_write() as batch:
        for key in keys:
            batch.delete(key)
    return None


def get_user_summary(user_id: str) -> str | None:
    """유저 장기 요약 — 매 요청 GetItem 단건, 시스템 프롬프트에 주입한다."""
    try:
        return UserSummaryRecord.get(user_id).summary_text
    except UserSummaryRecord.DoesNotExist:
        return None


def save_user_summary(user_id: str, summary_text: str, updated_at: str) -> None:
    """유저 장기 요약 갱신 — TTL 없는 영속 항목이라 upsert만 한다."""
    UserSummaryRecord(user_id).update(
        actions=[
            UserSummaryRecord.summary_text.set(summary_text),
            UserSummaryRecord.updated_at.set(updated_at),
            # created_at은 최초 1회만 — if_not_exists로 덮어쓰지 않는다
            UserSummaryRecord.created_at.set(UserSummaryRecord.created_at | updated_at),
        ]
    )
