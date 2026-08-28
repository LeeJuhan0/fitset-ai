"""routines/service.generate_routine — 검색 저장소 전환 후의 흐름 테스트.

리포지토리·LLM·백엔드 조회를 전부 목으로 두고, 기피 재시도·LLM 폴백·후보 없음 세 갈래만 본다.
"""
import pytest

from app.core.errors import AiUnavailableError, NoRoutineCandidateError
from app.routines import service
from app.routines.schemas import RoutineGenerateRequest

USER = "11111111-1111-1111-1111-111111111111"


def _request(**overrides):
    base = dict(level="intermediate", muscle_groups=["chest"], minutes=60, context="어깨가 아파요", include_warmup=False)
    base.update(overrides)
    return RoutineGenerateRequest(**base)


def _row(slug):
    return {
        "slug": slug, "goal": "hypertrophy", "level": "beginner", "minutes_per_routine": 60,
        "muscle_groups": ["chest"], "exercise_names": ["푸시업"],
        "body": {"slug": slug, "name": slug, "minutes_per_routine": 60, "exercises": [
            {"slug": "push-up", "exercise_name": "푸시업", "thumbnail_url": "", "order_index": 0,
             "sets": [{"order_index": 0, "reps": 10, "weight": None}]},
        ]},
    }


@pytest.fixture
def wired(monkeypatch):
    calls = {"search": []}
    monkeypatch.setattr(service.postgres, "is_configured", lambda: True)
    monkeypatch.setattr(service.ratelimit, "routine_limiter", lambda: type("L", (), {"allow": lambda self, u: True})())
    monkeypatch.setattr(service.users_repository, "get_profile", lambda user_id: {"goal": "hypertrophy", "avoidBodyParts": ["back"]})
    monkeypatch.setattr(service.workouts_repository, "get_recent_workouts", lambda user_id, days: [])
    monkeypatch.setattr(service.llm, "embed_query", lambda text: [0.1] * 1024)
    monkeypatch.setattr(service.llm, "complete", lambda system, user, max_tokens: "1")

    async def analyze(request, goal):
        return "chest routine", {"shoulders"}, []

    monkeypatch.setattr(service, "_analyze_query", analyze)
    monkeypatch.setattr(service, "_build_response", lambda routine, request, profile, stats, meta: routine["slug"])

    def search(filters, query_vector, limit):
        calls["search"].append(filters)
        return calls["rows"](filters)

    monkeypatch.setattr(service.repository, "search", search)
    return calls


@pytest.mark.asyncio
async def test_retries_without_parsed_avoid_when_empty(wired):
    # 파싱한 기피(shoulders)로 비면 그것만 풀고 재시도, 프로필 기피(back)는 유지
    wired["rows"] = lambda f: [] if "shoulders" in f.avoided else [_row("a")]
    assert await service.generate_routine(USER, _request()) == "a"
    assert [sorted(f.avoided) for f in wired["search"]] == [["back", "shoulders"], ["back"]]


@pytest.mark.asyncio
async def test_no_candidate_after_retry_raises_409(wired):
    wired["rows"] = lambda f: []
    with pytest.raises(NoRoutineCandidateError):
        await service.generate_routine(USER, _request())


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_cosine_top1(wired, monkeypatch):
    wired["rows"] = lambda f: [_row("first"), _row("second")]
    monkeypatch.setattr(service.llm, "complete", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    assert await service.generate_routine(USER, _request()) == "first"


@pytest.mark.asyncio
async def test_unconfigured_store_is_503(monkeypatch):
    monkeypatch.setattr(service.postgres, "is_configured", lambda: False)
    monkeypatch.setattr(service.ratelimit, "routine_limiter", lambda: type("L", (), {"allow": lambda self, u: True})())
    with pytest.raises(AiUnavailableError):
        await service.generate_routine(USER, _request())
