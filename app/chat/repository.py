"""채팅 저장소 — DynamoDB 명령이 사는 유일한 곳 (docs/document-structure.md).

chat_threads(PK=user_id, SK=thread_id) · chat_messages(PK=thread_id, SK=message_id) ·
user_summaries(PK=user_id). 전 함수 동기 — async 서비스에서 asyncio.to_thread로 감싼다.
조합·판단은 하지 않는다(계층 규칙) — 정렬·만료 판정은 domain, 흐름은 service.

`role`·`content`·`data` 등은 DynamoDB 예약어라 조회·갱신 표현식에서 항상
ExpressionAttributeNames(#별칭)로 우회한다.

스레드 갱신은 전부 조건부 쓰기다 — UpdateItem은 기본이 upsert라, 조건 없이는
삭제와 경쟁한 갱신(메시지 전송·백그라운드 요약)이 유령 스레드를 부활시킨다.
"""
import logging

from boto3.dynamodb.conditions import Key

from app.core.dynamo import (
    get_chat_messages_table,
    get_chat_threads_table,
    get_user_summaries_table,
    to_plain,
    to_storable,
)

logger = logging.getLogger("fitset")

# 컨텍스트 로드용 투영 — payload는 화면 렌더링에만 쓰이므로 LLM 경로에서 뺀다
_CONTEXT_FIELDS = ("message_id", "role", "content", "created_at")


def _names(fields) -> dict:
    """예약어 회피용 ExpressionAttributeNames 매핑."""
    return {f"#{field}": field for field in fields}


def list_threads(user_id: str) -> list[dict]:
    """유저의 전체 스레드 — 최대 5개라 정렬·만료 필터는 메모리에서 한다(GSI 불필요)."""
    response = get_chat_threads_table().query(
        KeyConditionExpression=Key("user_id").eq(user_id)
    )
    return [to_plain(item) for item in response.get("Items", [])]


def get_thread(user_id: str, thread_id: str) -> dict | None:
    """스레드 단건. PK가 (user_id, thread_id)라 남의 스레드는 애초에 조회되지 않는다."""
    item = get_chat_threads_table().get_item(
        Key={"user_id": user_id, "thread_id": thread_id},
        # 생성(201) 직후 바로 온 전송이 EC 읽기로 404 나지 않게 — 단건이라 비용 무시 가능
        ConsistentRead=True,
    ).get("Item")
    return to_plain(item) if item is not None else None


def put_thread(item: dict) -> None:
    """스레드 신규 생성 — None 필드는 속성 자체를 빼고 저장한다(sparse).

    NULL 타입으로 저장하면 if_not_exists가 NULL을 '있는 값'으로 보고 title을
    영영 채우지 못한다. 읽는 쪽은 전부 .get이라 부재와 null이 동치다.
    """
    stored = {key: value for key, value in item.items() if value is not None}
    get_chat_threads_table().put_item(Item=to_storable(stored))


def delete_thread(user_id: str, thread_id: str) -> None:
    """스레드 항목만 삭제 — 소속 메시지는 delete_messages가 따로 지운다."""
    get_chat_threads_table().delete_item(Key={"user_id": user_id, "thread_id": thread_id})


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
    names = {"#last_message_at": "last_message_at", "#expires_at": "expires_at"}
    sets = ["#last_message_at = :last_message_at", "#expires_at = :expires_at"]
    values = {":last_message_at": last_message_at, ":expires_at": expires_at}
    if title is not None:
        names["#title"] = "title"
        sets.append("#title = if_not_exists(#title, :title)")
        values[":title"] = title
    table = get_chat_threads_table()
    try:
        table.update_item(
            Key={"user_id": user_id, "thread_id": thread_id},
            UpdateExpression="SET " + ", ".join(sets),
            ConditionExpression="attribute_exists(thread_id)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        logger.info("touch skipped, thread already deleted: %s", thread_id)


def save_summary(user_id: str, thread_id: str, summary_text: str, summary_upto: str) -> None:
    """스레드 압축 요약과 반영 지점(ULID)을 갱신한다 — 백그라운드 요약 경로.

    조건 두 가지: 스레드가 살아있고(upsert 부활 방지), summary_upto가 전진할 때만.
    NOT(#u >= :u)은 속성 부재면 참이라 첫 요약도 통과하고, 늦게 끝난 묵은
    태스크(Lost Update·역행)는 조건 실패로 버려진다.
    """
    fields = ["summary_text", "summary_upto"]
    table = get_chat_threads_table()
    try:
        table.update_item(
            Key={"user_id": user_id, "thread_id": thread_id},
            UpdateExpression="SET #summary_text = :summary_text, #summary_upto = :summary_upto",
            ConditionExpression=(
                "attribute_exists(thread_id) AND NOT (#summary_upto >= :summary_upto)"
            ),
            ExpressionAttributeNames=_names(fields),
            ExpressionAttributeValues={
                ":summary_text": summary_text,
                ":summary_upto": summary_upto,
            },
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        logger.info("summary skipped, thread deleted or stale upto: %s", thread_id)


def list_messages(thread_id: str) -> list[dict]:
    """대화 전체를 시간순으로 — payload 포함(화면 렌더링용). SK가 ULID라 정렬이 곧 시간순."""
    items: list[dict] = []
    kwargs = {
        "KeyConditionExpression": Key("thread_id").eq(thread_id),
        "ScanIndexForward": True,
    }
    while True:
        response = get_chat_messages_table().query(**kwargs)
        items.extend(to_plain(item) for item in response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def recent_messages(thread_id: str, limit: int) -> list[dict]:
    """LLM 컨텍스트용 최근 N턴 — payload 제외 투영, 역순 조회 후 뒤집어 시간순으로 돌려준다."""
    response = get_chat_messages_table().query(
        KeyConditionExpression=Key("thread_id").eq(thread_id),
        ScanIndexForward=False,
        Limit=limit,
        ProjectionExpression=", ".join(f"#{field}" for field in _CONTEXT_FIELDS),
        ExpressionAttributeNames=_names(_CONTEXT_FIELDS),
    )
    return [to_plain(item) for item in reversed(response.get("Items", []))]


def put_message(item: dict) -> None:
    """메시지 1건 저장."""
    get_chat_messages_table().put_item(Item=to_storable(item))


def messages_after(thread_id: str, summary_upto: str | None) -> list[dict]:
    """summary_upto 이후 메시지 — 재요약 입력. role·content만 투영, 시간순.

    전체 대화가 아니라 미요약 꼬리만 읽는다 — 재요약은 (기존 summary_text + 신규 메시지)의
    증분 병합이다(document-structure). 전체를 실으면 프롬프트가 대화 길이에 비례해 커진다.
    """
    condition = Key("thread_id").eq(thread_id)
    if summary_upto is not None:
        condition = condition & Key("message_id").gt(summary_upto)
    items: list[dict] = []
    kwargs = {
        "KeyConditionExpression": condition,
        "ScanIndexForward": True,
        "ProjectionExpression": ", ".join(f"#{field}" for field in _CONTEXT_FIELDS),
        "ExpressionAttributeNames": _names(_CONTEXT_FIELDS),
    }
    while True:
        response = get_chat_messages_table().query(**kwargs)
        items.extend(to_plain(item) for item in response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def count_unsummarized(thread_id: str, summary_upto: str | None) -> int:
    """summary_upto 이후 메시지 수 — Select COUNT라 항목 본문을 전송받지 않는다.

    요약 임계 검사는 매 전송마다 돌므로 파티션 전체를 읽지 않는다. ULID 범위 조건으로
    스캔 구간도 미요약 꼬리만으로 좁힌다 (파티션 전체 로드 → 꼬리 COUNT).
    """
    condition = Key("thread_id").eq(thread_id)
    if summary_upto is not None:
        condition = condition & Key("message_id").gt(summary_upto)
    total = 0
    kwargs = {"KeyConditionExpression": condition, "Select": "COUNT"}
    while True:
        response = get_chat_messages_table().query(**kwargs)
        total += response.get("Count", 0)
        if "LastEvaluatedKey" not in response:
            return total
        # DynamoDB 1MB 스캔 상한에 잘리면 오는 책갈피 — 이 키 다음부터 이어읽는다
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def delete_messages(thread_id: str) -> None:
    """스레드 소속 메시지 전량 삭제 — 키만 조회해 배치로 지운다.

    batch_writer가 25개 flush와 UnprocessedItems 재시도를 알아서 하므로
    수동 청크를 두지 않는다.
    """
    table = get_chat_messages_table()
    kwargs = {
        "KeyConditionExpression": Key("thread_id").eq(thread_id),
        "ProjectionExpression": "thread_id, message_id",
    }
    keys: list[dict] = []
    while True:
        response = table.query(**kwargs)
        keys.extend(
            {"thread_id": item["thread_id"], "message_id": item["message_id"]}
            for item in response.get("Items", [])
        )
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    with table.batch_writer() as batch:
        for key in keys:
            batch.delete_item(Key=key)
    return None


def get_user_summary(user_id: str) -> str | None:
    """유저 장기 요약 — 매 요청 GetItem 단건, 시스템 프롬프트에 주입한다."""
    item = get_user_summaries_table().get_item(Key={"user_id": user_id}).get("Item")
    if item is None:
        return None
    return item.get("summary_text")


def save_user_summary(user_id: str, summary_text: str, updated_at: str) -> None:
    """유저 장기 요약 갱신 — TTL 없는 영속 항목이라 upsert만 한다."""
    fields = ["summary_text", "updated_at", "created_at"]
    get_user_summaries_table().update_item(
        Key={"user_id": user_id},
        # created_at은 최초 1회만 — if_not_exists로 덮어쓰지 않는다
        UpdateExpression=(
            "SET #summary_text = :summary_text, #updated_at = :updated_at, "
            "#created_at = if_not_exists(#created_at, :updated_at)"
        ),
        ExpressionAttributeNames=_names(fields),
        ExpressionAttributeValues={
            ":summary_text": summary_text,
            ":updated_at": updated_at,
        },
    )
