from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.rag import pipeline, retrieve
from app.rag.prompts import PickedRoutine
from app.rag.retrieve import SearchFilters

RULE_PASSED = ["leg-best", "leg-second", "leg-hamstring", "home-leg", "leg-no-minutes"]


@pytest.mark.parametrize("minutes, tolerance, expected", [(60, 0.2, (48, 72)), (45, 0.2, (36, 54)), (50, 0.25, (38, 62))])
def test_시간_범위는_요청_시간에_허용_오차를_정수_분으로_적용한다(search_filters: SearchFilters, minutes, tolerance, expected):
    filters = replace(search_filters, minutes=minutes, tolerance=tolerance)

    window = filters.minute_window()

    assert window == expected


@pytest.mark.usefixtures("search_routines")
async def test_후보는_룰_필터를_통과한_루틴을_코사인_유사도순으로_준다(search_filters: SearchFilters, query_vector):
    top = await retrieve.top_candidates(search_filters, set(), query_vector)

    assert [routine["slug"] for routine in top] == RULE_PASSED
    assert {key: top[0][key] for key in ("level", "minutes_per_routine", "muscle_groups", "exercise_names")} == {
        "level": "beginner",
        "minutes_per_routine": 60,
        "muscle_groups": ["quadriceps", "glutes"],
        "exercise_names": ["바벨 스쿼트"],
    }


@pytest.mark.usefixtures("search_routines")
async def test_후보는_기피_부위가_섞인_루틴을_뺀다(search_filters: SearchFilters, query_vector):
    filters = replace(search_filters, avoided={"hamstrings"})

    top = await retrieve.top_candidates(filters, set(), query_vector)

    assert "leg-hamstring" not in [routine["slug"] for routine in top]


@pytest.mark.usefixtures("search_routines")
async def test_홈트_유저_후보는_맨몸_루틴만_준다(search_filters: SearchFilters, query_vector):
    filters = replace(search_filters, home_only=True)

    top = await retrieve.top_candidates(filters, set(), query_vector)

    assert [routine["slug"] for routine in top] == ["home-leg"]


@pytest.mark.usefixtures("search_routines")
async def test_후보는_코사인_상위_K개까지만_준다(search_filters: SearchFilters, query_vector, settings: Settings):
    settings.cosine_top_k = 2

    top = await retrieve.top_candidates(search_filters, set(), query_vector)

    assert [routine["slug"] for routine in top] == ["leg-best", "leg-second"]


@pytest.mark.usefixtures("search_routines")
async def test_문맥_기피_부위는_후보를_남기면_적용한다(search_filters: SearchFilters, query_vector):
    top = await retrieve.top_candidates(search_filters, {"glutes"}, query_vector)

    assert [routine["slug"] for routine in top] == ["leg-second", "leg-hamstring", "home-leg", "leg-no-minutes"]


@pytest.mark.usefixtures("search_routines")
async def test_문맥_기피_부위로_후보가_비면_그것만_풀어_다시_찾는다(search_filters: SearchFilters, query_vector):
    filters = replace(search_filters, avoided={"hamstrings"})

    top = await retrieve.top_candidates(filters, {"quadriceps"}, query_vector)

    assert [routine["slug"] for routine in top] == ["leg-best", "leg-second", "home-leg", "leg-no-minutes"]


@pytest.mark.usefixtures("search_routines", "ordered_sample")
async def test_루틴_검색은_임베딩_룰_필터_검색_LLM_선택을_거쳐_한_건을_준다(
    llm: SimpleNamespace,
    search_filters: SearchFilters,
):
    llm.structured[PickedRoutine] = PickedRoutine(number=2)

    routine = await pipeline.find_routine("하체 루틴", search_filters, set())

    assert routine["slug"] == "leg-second"


@pytest.mark.usefixtures("search_routines", "llm")
async def test_루틴_검색은_룰_필터를_통과한_후보가_없으면_없다(search_filters: SearchFilters):
    filters = replace(search_filters, muscle_groups=["calves"])

    routine = await pipeline.find_routine("종아리 루틴", filters, set())

    assert routine is None
