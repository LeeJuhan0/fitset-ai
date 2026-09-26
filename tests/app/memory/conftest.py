from datetime import timedelta

import pytest

from app.clients import bedrock
from app.memory.models import UserSummaryRecord
from tests.conftest import NOW, USER_ID

OTHER_USER_ID = "22222222-2222-2222-2222-222222222222"


def _summary(user_id: str, text: str, created_ago: timedelta) -> UserSummaryRecord:
    """지정 시각에 병합된 장기 요약 이력 저장"""
    record = UserSummaryRecord.new(user_id, text, NOW - created_ago, "thread", "upto")
    record.save()
    return record


@pytest.fixture
def summary_history(dynamo_tables) -> list[UserSummaryRecord]:
    """이틀 전과 한 시간 전에 병합된 장기 요약 두 건"""
    return [
        _summary(USER_ID, "스쿼트 위주로 운동함", timedelta(days=2)),
        _summary(USER_ID, "스쿼트 위주, 무릎 통증 있음", timedelta(hours=1)),
    ]


@pytest.fixture
def other_user_summary(dynamo_tables) -> UserSummaryRecord:
    """다른 사용자의 방금 병합된 장기 요약"""
    return _summary(OTHER_USER_ID, "남의 요약", timedelta(minutes=1))


@pytest.fixture
def summary_prompts(monkeypatch, llm) -> list[str]:
    """장기 요약 병합 LLM 에 들어간 유저 프롬프트 기록"""
    prompts = []

    def complete(system: str, user: str, max_tokens=None) -> str:
        """프롬프트 기록 후 테스트가 정한 응답 반환"""
        prompts.append(user)
        return llm.text

    monkeypatch.setattr(bedrock, "complete", complete)
    return prompts
