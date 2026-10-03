import base64

import boto3
import pytest

from app.chat.models import MessageRecord
from app.core import crypto


def _raw_item(thread_id: str, message_id: str) -> dict:
    """복호화 거치지 않은 저장 원본 조회"""
    key = {"thread_id": {"S": thread_id}, "message_id": {"S": message_id}}
    return _client().get_item(TableName=MessageRecord.Meta.table_name, Key=key)["Item"]


def _client():
    """moto DynamoDB 저수준 클라이언트"""
    return boto3.client("dynamodb", region_name=MessageRecord.Meta.region)


@pytest.mark.usefixtures("dynamo_tables")
def test_메시지_본문과_페이로드는_암호문으로_저장되고_평문으로_읽힌다():
    MessageRecord(thread_id="t", message_id="m", role="assistant", content="스쿼트 100kg", payload={"slug": "squat", "sets": [1, 2]}).save()

    raw = _raw_item("t", "m")
    loaded = MessageRecord.get("t", "m")

    assert raw["content"]["S"].startswith("enc:v1:")
    assert "스쿼트" not in raw["content"]["S"]
    assert raw["payload"]["S"].startswith("enc:v1:")
    assert loaded.content == "스쿼트 100kg"
    assert loaded.payload == {"slug": "squat", "sets": [1, 2]}


@pytest.mark.usefixtures("dynamo_tables")
def test_암호화_이전_평문_문자열과_맵_항목도_그대로_읽힌다():
    _client().put_item(
        TableName=MessageRecord.Meta.table_name,
        Item={
            "thread_id": {"S": "t"},
            "message_id": {"S": "m"},
            "role": {"S": "assistant"},
            "content": {"S": "기존 평문"},
            "payload": {"M": {"slug": {"S": "squat"}, "sets": {"L": [{"N": "1"}]}}},
        },
    )

    loaded = MessageRecord.get("t", "m")

    assert loaded.content == "기존 평문"
    assert loaded.payload == {"slug": "squat", "sets": [1]}


def test_키_교체_후_새_데이터는_새_버전으로_쓰고_이전_버전_데이터도_읽힌다(settings):
    old = crypto.encrypt("이전 키 메시지")
    settings.chat_encryption_keys = {"v1": settings.chat_encryption_keys["v1"], "v2": base64.b64encode(b"\x01" * 32).decode()}
    settings.chat_encryption_key_version = "v2"

    new = crypto.encrypt("새 키 메시지")

    assert old.startswith("enc:v1:")
    assert new.startswith("enc:v2:")
    assert crypto.decrypt(old) == "이전 키 메시지"
    assert crypto.decrypt(new) == "새 키 메시지"
