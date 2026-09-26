from types import SimpleNamespace

import pytest

from app.clients import bedrock
from app.core.clock import iso_utc
from app.core.exceptions import DomainError
from app.memory import repository, service
from app.memory.models import UserSummaryRecord
from tests.conftest import NOW, USER_ID


@pytest.mark.usefixtures("summary_history", "other_user_summary")
def test_장기_요약은_해당_사용자의_가장_최근_이력을_준다():
    summary = repository.get_user_summary(USER_ID)

    assert summary == "스쿼트 위주, 무릎 통증 있음"


@pytest.mark.usefixtures("other_user_summary")
def test_장기_요약은_이력이_없으면_없다():
    summary = repository.get_user_summary(USER_ID)

    assert summary is None


@pytest.mark.usefixtures("summary_history")
def test_장기_요약_저장은_이전_이력을_남기고_새_이력을_최신으로_만든다():
    record = UserSummaryRecord.new(USER_ID, "새 요약", NOW, "thread", "upto")

    repository.put_user_summary(record)

    assert repository.get_user_summary(USER_ID) == "새 요약"
    assert UserSummaryRecord.count(USER_ID) == 3


def test_장기_요약_저장소_실패는_도메인_예외로_바꾼다(dynamo_tables):
    UserSummaryRecord.delete_table()

    with pytest.raises(DomainError):
        repository.get_user_summary(USER_ID)


@pytest.mark.usefixtures("summary_history")
async def test_시스템_프롬프트용_장기_요약은_최신_이력이다():
    summary = await service.latest_summary(USER_ID)

    assert summary == "스쿼트 위주, 무릎 통증 있음"


@pytest.mark.usefixtures("summary_history")
async def test_대화_요약_병합은_이전_장기_요약과_합친_새_이력을_저장한다(llm: SimpleNamespace, summary_prompts: list[str]):
    llm.text = "  스쿼트 위주, 무릎 통증 있음, 벤치 시작  "

    await service.merge_thread_summary(USER_ID, "thread-1", "벤치프레스를 시작함", "message-9")

    assert "<previous_summary>스쿼트 위주, 무릎 통증 있음</previous_summary>" in summary_prompts[0]
    assert "<recent_thread>벤치프레스를 시작함</recent_thread>" in summary_prompts[0]
    latest = next(iter(UserSummaryRecord.query(USER_ID, scan_index_forward=False, limit=1)))
    assert latest.summary_text == "스쿼트 위주, 무릎 통증 있음, 벤치 시작"
    assert (latest.source_thread_id, latest.source_upto, latest.created_at) == ("thread-1", "message-9", iso_utc(NOW))


async def test_첫_대화_요약_병합은_이전_요약_없음으로_묻는다(dynamo_tables, llm: SimpleNamespace, summary_prompts: list[str]):
    llm.text = "벤치 시작"

    await service.merge_thread_summary(USER_ID, "thread-1", "벤치프레스를 시작함", "message-9")

    assert "<previous_summary>(없음)</previous_summary>" in summary_prompts[0]
    assert repository.get_user_summary(USER_ID) == "벤치 시작"


@pytest.mark.usefixtures("summary_history")
async def test_대화_요약_병합은_LLM_실패_시_이력을_늘리지_않는다(monkeypatch):
    def fail(system: str, user: str, max_tokens=None) -> str:
        """Bedrock 응답 형식 오류 재현"""
        raise KeyError("output")

    monkeypatch.setattr(bedrock, "complete", fail)

    await service.merge_thread_summary(USER_ID, "thread-1", "벤치프레스를 시작함", "message-9")

    assert UserSummaryRecord.count(USER_ID) == 2
