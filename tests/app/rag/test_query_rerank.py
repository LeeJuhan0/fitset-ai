from types import SimpleNamespace

import pytest

from app.clients import bedrock
from app.core.config import Settings
from app.core.exceptions import AiUnavailableError
from app.rag import embed, query, rerank
from app.rag.prompts import PickedRoutine, QueryAnalysis

FALLBACK = "대퇴사두근 중심의 초급 근비대 운동 루틴, 약 60분."


def _analyze():
    """초급 60분 대퇴사두근 근비대 요청 분석"""
    return query.analyze("hypertrophy", "beginner", ["quadriceps"], 60, "햄스트링이 아파요")


async def test_요청_분석은_LLM_묘사문과_허용_기피_부위_안전_제약을_준다(llm: SimpleNamespace):
    llm.structured[QueryAnalysis] = QueryAnalysis(
        description=" 스쿼트 위주 하체 루틴 ", avoid_muscles=["hamstrings", "knee"], unsafe_constraints=["점프 금지", " "],
    )

    result = await _analyze()

    assert result == ("스쿼트 위주 하체 루틴", {"hamstrings"}, ["점프 금지"])


async def test_요청_분석은_LLM이_실패하면_템플릿_묘사문으로_강등한다(llm: SimpleNamespace):
    llm.structured = {}

    result = await _analyze()

    assert result == (FALLBACK, set(), [])


async def test_요청_분석은_묘사문이_비면_기피_부위까지_버리고_템플릿으로_강등한다(llm: SimpleNamespace):
    llm.structured[QueryAnalysis] = QueryAnalysis(description=" ", avoid_muscles=["hamstrings"])

    result = await _analyze()

    assert result == (FALLBACK, set(), [])


async def test_쿼리_임베딩은_묘사문_벡터를_준다(llm: SimpleNamespace):
    vector = await embed.query_vector("스쿼트 위주 하체 루틴")

    assert vector == llm.embedding


async def test_쿼리_임베딩이_실패하면_AI_사용_불가_예외를_던진다(monkeypatch):
    def fail(text: str) -> list[float]:
        """Bedrock 응답 형식 오류 재현"""
        raise ValueError("embeddings")

    monkeypatch.setattr(bedrock, "embed_query", fail)

    with pytest.raises(AiUnavailableError):
        await embed.query_vector("스쿼트 위주 하체 루틴")


@pytest.mark.usefixtures("ordered_sample")
@pytest.mark.parametrize("number, expected_slug", [(1, "leg-best"), (2, "leg-second"), (3, "leg-third")])
async def test_최종_선택은_LLM이_고른_번호의_후보를_준다(
    llm: SimpleNamespace,
    candidates: list[dict],
    number: int,
    expected_slug: str,
):
    llm.structured[PickedRoutine] = PickedRoutine(number=number)

    chosen = await rerank.choose(candidates, "하체 루틴")

    assert chosen["slug"] == expected_slug


@pytest.mark.usefixtures("ordered_sample")
@pytest.mark.parametrize("picked", [None, PickedRoutine(number=0), PickedRoutine(number=4)])
async def test_최종_선택은_LLM_실패나_범위_밖_번호면_코사인_1위를_준다(
    llm: SimpleNamespace,
    candidates: list[dict],
    picked,
):
    llm.structured[PickedRoutine] = picked

    chosen = await rerank.choose(candidates, "하체 루틴")

    assert chosen["slug"] == "leg-best"


@pytest.mark.usefixtures("ordered_sample")
async def test_최종_선택은_설정한_후보_수만큼만_LLM에_보인다(
    llm: SimpleNamespace,
    settings: Settings,
    candidates: list[dict],
):
    settings.llm_candidate_count = 2
    llm.structured[PickedRoutine] = PickedRoutine(number=3)

    chosen = await rerank.choose(candidates, "하체 루틴")

    assert chosen["slug"] == "leg-best"
