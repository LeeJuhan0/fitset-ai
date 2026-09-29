from datetime import timedelta

import pytest

from app.clients import bedrock
from app.memory.models import UserSummaryRecord
from app.memory.schemas import UserPreferences
from tests.conftest import NOW, USER_ID

OTHER_USER_ID = "22222222-2222-2222-2222-222222222222"


def _summary(user_id: str, style: str, created_ago: timedelta) -> UserSummaryRecord:
    """지정 시각에 병합된 운동 편향 이력 저장"""
    record = UserSummaryRecord.new(user_id, UserPreferences(training_style=style).model_dump(), NOW - created_ago, "thread", "upto")
    record.save()
    return record


@pytest.fixture
def summary_history(dynamo_tables) -> list[UserSummaryRecord]:
    """이틀 전과 한 시간 전에 병합된 장기 요약 두 건"""
    return [
        _summary(USER_ID, "펌핑 위주", timedelta(days=2)),
        _summary(USER_ID, "고중량 저반복", timedelta(hours=1)),
    ]


@pytest.fixture
def other_user_summary(dynamo_tables) -> UserSummaryRecord:
    """다른 사용자의 방금 병합된 장기 요약"""
    return _summary(OTHER_USER_ID, "서킷", timedelta(minutes=1))


@pytest.fixture
def summary_prompts(monkeypatch, llm) -> list[str]:
    """운동 편향 병합 LLM 입력 프롬프트 기록"""
    prompts = []

    def structured(schema, system: str, user: str):
        """프롬프트 기록 후 테스트가 정한 구조 응답 반환"""
        prompts.append(user)
        return llm.structured.get(schema)

    monkeypatch.setattr(bedrock, "structured", structured)
    return prompts
