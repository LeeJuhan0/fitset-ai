"""routines/service.generate_routine — 검색 저장소 전환 후의 흐름 테스트.

리포지토리·LLM·백엔드 조회를 전부 목으로 두고, 기피 재시도·LLM 폴백·후보 없음 세 갈래만 본다.
"""
import pytest
from botocore.exceptions import ClientError

from app.core.exceptions import AiUnavailableError
from app.routines.exceptions import NoRoutineCandidateError
from app.clients import bedrock
from app.rag import prompts, query, retrieve
from app.routines import service
from app.routines.schemas import RoutineGenerateRequest


def _bedrock_down() -> ClientError:
    """Bedrock 장애와 같은 형태의 예외"""
    return ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "down"}}, "Converse")


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
    monkeypatch.setattr(service, "routine_limiter", lambda: type("L", (), {"allow": lambda self, u: True})())
    monkeypatch.setattr(service.users_repository, "get_profile", lambda user_id: {"goal": "hypertrophy", "avoidBodyParts": ["back"]})
    monkeypatch.setattr(service.workouts_repository, "get_recent_workouts", lambda user_id, days: [])
    monkeypatch.setattr(bedrock, "embed_query", lambda text: [0.1] * 1024)
    monkeypatch.setattr(bedrock, "structured", lambda schema, system, user: prompts.PickedRoutine(number=1))

    async def analyze(goal, level, muscle_groups, minutes, context):
        return "chest routine", {"shoulders"}, []

    monkeypatch.setattr(query, "analyze", analyze)
    monkeypatch.setattr(service, "_build_response", lambda routine, request, stats, meta: routine["slug"])

    def search(filters, query_vector, limit):
        calls["search"].append(filters)
        return calls["rows"](filters)

    monkeypatch.setattr(retrieve, "fetch", search)
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
    monkeypatch.setattr(bedrock, "structured", lambda *a: (_ for _ in ()).throw(_bedrock_down()))
    assert await service.generate_routine(USER, _request()) == "first"


@pytest.mark.asyncio
async def test_unconfigured_store_is_503(monkeypatch):
    monkeypatch.setattr(service.postgres, "is_configured", lambda: False)
    monkeypatch.setattr(service, "routine_limiter", lambda: type("L", (), {"allow": lambda self, u: True})())
    with pytest.raises(AiUnavailableError):
        await service.generate_routine(USER, _request())


@pytest.mark.asyncio
async def test_pick_out_of_range_falls_back_to_cosine_top1(wired, monkeypatch):
    wired["rows"] = lambda f: [_row("first"), _row("second")]
    monkeypatch.setattr(bedrock, "structured", lambda schema, system, user: prompts.PickedRoutine(number=9))
    assert await service.generate_routine(USER, _request()) == "first"


async def _analyze(request):
    """요청 필드로 쿼리 분석 호출"""
    return await query.analyze("hypertrophy", request.level, request.muscle_groups, request.minutes, request.context)


@pytest.mark.asyncio
async def test_analyze_query_filters_unknown_muscles(monkeypatch):
    analysis = prompts.QueryAnalysis(
        description="  Lower body routine with barbell squat  ",
        avoid_muscles=["shoulders", "다리"],
        unsafe_constraints=["no overhead pressing", "  "],
    )
    monkeypatch.setattr(bedrock, "structured", lambda schema, system, user: analysis)
    description, avoided, unsafe = await _analyze(_request())
    assert description == "Lower body routine with barbell squat"
    assert avoided == {"shoulders"}
    assert unsafe == ["no overhead pressing"]


@pytest.mark.asyncio
async def test_analyze_query_falls_back_to_template_on_failure(monkeypatch):
    monkeypatch.setattr(bedrock, "structured", lambda *a: (_ for _ in ()).throw(_bedrock_down()))
    description, avoided, unsafe = await _analyze(_request())
    assert description == "가슴 중심의 중급 근비대 운동 루틴, 약 60분."
    assert (avoided, unsafe) == (set(), [])


@pytest.mark.asyncio
async def test_code_bug_in_llm_call_is_not_swallowed_as_fallback(monkeypatch):
    # 코드 버그(TypeError)는 LLM 장애처럼 폴백으로 숨기지 않고 드러낸다
    monkeypatch.setattr(bedrock, "structured", lambda *a: (_ for _ in ()).throw(TypeError("bug")))
    with pytest.raises(TypeError):
        await _analyze(_request())
