from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from app.core import dynamo
from app.memory import repository
from app.memory.models import UserSummaryRecord

USER_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def tables(monkeypatch):
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(key, "testing")
    with mock_aws():
        dynamo.bind_tables()
        boto3.resource("dynamodb", region_name="ap-northeast-2").create_table(
            TableName="user_summaries",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "summary_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "summary_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield
    dynamo.bind_tables()


def test_user_summary_appends_history_and_reads_latest(tables):
    assert repository.get_user_summary(USER_ID) is None
    first = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    repository.put_user_summary(
        UserSummaryRecord.new(USER_ID, "첫 요약", first, "01THREAD", "01MSGA", {"feedback_style": "direct"})
    )
    repository.put_user_summary(
        UserSummaryRecord.new(USER_ID, "둘째 요약", first + timedelta(days=1), "01THREAD", "01MSGB")
    )

    assert repository.get_user_summary(USER_ID) == "둘째 요약"
    history = list(UserSummaryRecord.query(USER_ID))
    assert [item.summary_text for item in history] == ["첫 요약", "둘째 요약"]
    assert history[0].personality.as_dict() == {"feedback_style": "direct"}
    assert history[1].source_upto == "01MSGB"
