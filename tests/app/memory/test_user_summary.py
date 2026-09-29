from types import SimpleNamespace

import pytest

from app.clients import bedrock
from app.core.clock import iso_utc
from app.core.exceptions import DomainError
from app.memory import repository, service
from app.memory.models import UserSummaryRecord
from app.memory.schemas import AvoidedExercise, Environment, RejectedExercise, UserPreferences
from tests.conftest import NOW, USER_ID


@pytest.mark.usefixtures("summary_history", "other_user_summary")
def test_장기_요약은_해당_사용자의_가장_최근_이력을_준다():
    record = repository.latest_user_summary(USER_ID)

    assert record.preferences.as_dict()["training_style"] == "고중량 저반복"


@pytest.mark.usefixtures("other_user_summary")
def test_장기_요약은_이력이_없으면_없다():
    assert repository.latest_user_summary(USER_ID) is None


@pytest.mark.usefixtures("summary_history")
def test_장기_요약_저장은_이전_이력을_남기고_새_이력을_최신으로_만든다():
    record = UserSummaryRecord.new(USER_ID, UserPreferences(training_style="펌핑 위주").model_dump(), NOW, "thread", "upto")

    repository.put_user_summary(record)

    assert repository.latest_user_summary(USER_ID).preferences.as_dict()["training_style"] == "펌핑 위주"
    assert UserSummaryRecord.count(USER_ID) == 3


def test_장기_요약_저장소_실패는_도메인_예외로_바꾼다(dynamo_tables):
    UserSummaryRecord.delete_table()

    with pytest.raises(DomainError):
        repository.latest_user_summary(USER_ID)


@pytest.mark.usefixtures("summary_history")
async def test_루틴_추천용_편향은_최신_이력이다():
    assert await service.latest_preferences(USER_ID) == UserPreferences(training_style="고중량 저반복")


TRANSCRIPT = "user: 바벨 스쿼트 좋아해요. 런지는 무릎 통증 때문에 빼주세요\nassistant: 알겠어요"


def _preferences() -> UserPreferences:
    """선호, 거절, 환경, 목표가 담긴 운동 편향"""
    return UserPreferences(
        preferred_exercises=["바벨 스쿼트"],
        rejected_exercises=[RejectedExercise(name="런지", reason="무릎 통증")],
        environment=Environment(place="home", equipment=["덤벨 20kg까지"]),
        specific_goals=["벤치프레스 100kg"],
    )


@pytest.mark.usefixtures("summary_history")
async def test_대화_요약_병합은_이전_편향과_합친_새_이력을_저장한다(llm: SimpleNamespace, summary_prompts: list[str]):
    llm.structured[UserPreferences] = _preferences()

    await service.merge_preferences(USER_ID, "thread-1", TRANSCRIPT, "message-9")

    assert UserPreferences(training_style="고중량 저반복").model_dump_json() in summary_prompts[0]
    assert f"<new_messages>{TRANSCRIPT}</new_messages>" in summary_prompts[0]
    latest = repository.latest_user_summary(USER_ID)
    assert UserPreferences(**latest.preferences.as_dict()) == _preferences()
    assert "summary_text" not in latest.attribute_values
    assert await service.latest_preferences(USER_ID) == _preferences()
    assert (latest.source_thread_id, latest.source_upto, latest.created_at) == ("thread-1", "message-9", iso_utc(NOW))


async def test_이전_편향이_있으면_JSON_으로_넘겨_병합한다(dynamo_tables, llm: SimpleNamespace, summary_prompts: list[str]):
    llm.structured[UserPreferences] = _preferences()
    await service.merge_preferences(USER_ID, "thread-1", TRANSCRIPT, "message-1")
    llm.structured[UserPreferences] = UserPreferences(training_style="고중량 저반복")

    await service.merge_preferences(USER_ID, "thread-2", "user: 무겁게 적게 하는 게 좋아요", "message-2")

    assert _preferences().model_dump_json() in summary_prompts[1]
    assert await service.latest_preferences(USER_ID) == UserPreferences(training_style="고중량 저반복")


async def test_첫_병합은_이전_편향_없음으로_묻는다(dynamo_tables, llm: SimpleNamespace, summary_prompts: list[str]):
    llm.structured[UserPreferences] = UserPreferences(specific_goals=["벤치프레스 시작"])

    await service.merge_preferences(USER_ID, "thread-1", "벤치프레스를 시작함", "message-9")

    assert "<previous_preferences>(없음)</previous_preferences>" in summary_prompts[0]
    assert await service.latest_preferences(USER_ID) == UserPreferences(specific_goals=["벤치프레스 시작"])


@pytest.mark.usefixtures("summary_history")
async def test_대화_요약_병합은_LLM_실패_시_이력을_늘리지_않는다(monkeypatch):
    def fail(schema, system: str, user: str):
        """Bedrock 응답 형식 오류 재현"""
        raise KeyError("output")

    monkeypatch.setattr(bedrock, "structured", fail)

    await service.merge_preferences(USER_ID, "thread-1", "벤치프레스를 시작함", "message-9")

    assert UserSummaryRecord.count(USER_ID) == 2


async def test_유저가_말하지_않은_종목과_부위_표현은_편향에서_뺀다(dynamo_tables, llm: SimpleNamespace, summary_prompts: list[str]):
    llm.structured[UserPreferences] = UserPreferences(
        preferred_exercises=["바벨 데드리프트", "덤벨 체스트 플라이", "가슴 운동"],
        avoided_exercises=[AvoidedExercise(name="버피")],
    )
    transcript = "user: 데드리프트 좋아해. 오늘은 가슴 운동 하고 싶어\nassistant: 덤벨 체스트 플라이 어때요?"

    await service.merge_preferences(USER_ID, "thread-1", transcript, "message-1")

    assert await service.latest_preferences(USER_ID) == UserPreferences(preferred_exercises=["바벨 데드리프트"])
