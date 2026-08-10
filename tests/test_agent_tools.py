"""툴 디스패치 견고성 테스트 — 로컬 실측 버그(503) 회귀 방지.

LLM이 만든 툴 인자는 신뢰할 수 없다: 선택 필드 생략·enum 밖 값이 일상적으로 온다.
어떤 실패도 예외로 올라가 턴을 죽이면 안 되고(유저에게 503), LLM에 오류 내용을
돌려줘 고쳐 재시도하게 해야 한다.
"""
import pytest

from app.agent import tools
from app.agent.tools import routine
from app.routines import service as routines_service

USER_ID = "11111111-1111-1111-1111-111111111111"


async def test_missing_optional_field_uses_tool_default(monkeypatch):
    # 실측 사례 — LLM이 include_warmup을 생략하면 RoutineGenerateRequest 직행 시
    # 필수 필드 누락으로 ValidationError → 503이었다. 툴 스키마 기본값이 채워져야 한다.
    captured = {}

    async def fake_generate(user_id, request):
        captured["request"] = request
        raise routines_service.NoRoutineCandidateError()   # payload 조립은 이 테스트 관심사 아님

    monkeypatch.setattr(routines_service, "generate_routine", fake_generate)
    text, artifact = await routine.run(USER_ID, {
        "level": "intermediate",
        "muscle_groups": ["quadriceps", "hamstrings", "glutes"], "minutes": 50,
        "context": "어깨 통증 — 상체 제외",
        # include_warmup 생략 (LLM이 실제로 이렇게 보냈다)
    })
    assert captured["request"].include_warmup is True
    assert artifact is None                    # 후보 없음 → 정중한 실패 텍스트
    assert "루틴을 만들지 못했습니다" in text


async def test_dispatch_returns_validation_error_to_llm():
    # enum 밖 부위 — 예외 대신 교정 지시 텍스트가 LLM에 돌아가야 한다
    text, artifact = await tools.dispatch(USER_ID, "RecommendRoutine", {
        "level": "intermediate",
        "muscle_groups": ["다리"], "minutes": 50,
    })
    assert artifact is None
    assert "인자가 올바르지 않습니다" in text
    assert "muscle_groups" in text


async def test_dispatch_survives_unexpected_tool_crash(monkeypatch):
    async def boom(user_id, args):
        raise RuntimeError("db exploded")

    monkeypatch.setitem(tools.TOOL_RUNNERS, "DrawChart", boom)
    text, artifact = await tools.dispatch(USER_ID, "DrawChart", {"metric": "bodyWeight"})
    assert artifact is None
    assert "오류가 발생" in text


async def test_dispatch_unknown_tool_name():
    text, artifact = await tools.dispatch(USER_ID, "NoSuchTool", {})
    assert artifact is None
    assert "없습니다" in text


def test_suggest_exercises_partial_korean_name():
    # Nova 실측 — 작은 모델은 "스쿼트"처럼 부분 이름을 넘긴다. 후보가 나와야 한 왕복에 복구된다
    from app.exercises import repository as exercise_catalog

    hints = exercise_catalog.suggest_exercises("스쿼트")
    assert hints and any("스쿼트" in name for name in hints)
    assert exercise_catalog.suggest_exercises("벤치프레스")
    assert exercise_catalog.suggest_exercises("존재하지않는운동종목") == []


async def test_chart_unresolved_exercise_returns_hints_not_empty_data(monkeypatch):
    from app.agent.tools import chart

    text, artifact = await chart.run(USER_ID, {"metric": "exercisePr", "exercise": "벤치프레스"})
    assert artifact is None
    assert "비슷한 종목" in text            # "기록 부족"이 아니라 재호출 유도여야 한다
    assert "벤치프레스" in text


async def test_routine_server_fault_forbids_account_blame(monkeypatch):
    # 내부 API 장애(5xx)를 "계정 문제, 재로그인" 창작으로 번역하지 못하게 지시가 붙어야 한다 (prod 실측)
    from app.core.errors import DomainError

    async def fake_generate(user_id, request):
        raise DomainError("내부 API 호출에 실패했습니다.")

    monkeypatch.setattr(routines_service, "generate_routine", fake_generate)
    text, artifact = await routine.run(USER_ID, {
        "level": "intermediate", "muscle_groups": ["chest"], "minutes": 50,
    })
    assert artifact is None
    assert "서버 일시 장애" in text
    assert "재로그인 확인을 요구하지 말고" in text


async def test_chart_server_fault_forbids_account_blame(monkeypatch):
    from types import SimpleNamespace

    from app.agent.tools import chart
    from app.charts import service as charts_service
    from app.core.errors import DomainError

    async def fail_body_weights(user_id, days):
        raise DomainError("내부 API 호출에 실패했습니다.")

    stub = SimpleNamespace(get_body_weights=fail_body_weights)
    monkeypatch.setattr(charts_service, "get_spring_client", lambda: stub)
    text, artifact = await chart.run(USER_ID, {"metric": "bodyWeight"})
    assert artifact is None
    assert "서버 일시 장애" in text
    assert "재로그인 확인을 요구하지 말고" in text
