"""NL2SQL 기록 조회 툴 테스트 — 템플릿 고정·바인딩 전용·종목 해석·미설정 폴백 (이슈 #36).

MySQL은 붙지 않는다 — fetch_all을 대역으로 갈아끼우고, 툴이 조립한 select 문이
계약(값은 전부 바인딩 파라미터, user_id는 BINARY(16), slug는 검증 통과분만)을
지키는지 컴파일 결과로 확인한다.
"""
from decimal import Decimal

import pytest

from app.agent import tools
from app.agent.tools import history
from app.clients import mysql
from app.exercises import repository as exercise_catalog

USER_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def configured(monkeypatch):
    """DB 설정이 있는 것처럼 만들고, 실행된 select 문을 캡처한다."""
    captured = {}

    def fake_fetch_all(stmt):
        captured["stmt"] = stmt
        return captured.get("rows", [])

    monkeypatch.setattr(mysql, "is_configured", lambda: True)
    monkeypatch.setattr(mysql, "fetch_all", fake_fetch_all)
    # 종목 해석은 로컬 마스터 의존을 끊고 결정적으로
    monkeypatch.setattr(
        exercise_catalog, "resolve_exercise_slug",
        lambda value: "barbell-bench-press" if "벤치" in value or "bench" in value else None,
    )
    monkeypatch.setattr(exercise_catalog, "suggest_exercises", lambda value, limit=5: [])
    monkeypatch.setattr(exercise_catalog, "exercise_name", lambda slug: "바벨 벤치프레스")
    return captured


def test_statements_bind_values_instead_of_inlining():
    # 모든 템플릿은 값이 바인딩 파라미터로만 들어간다 — SQL 문자열에 값이 박히면 안 된다
    user = b"\x11" * 16
    for name, (builder, needs_exercise) in history._TEMPLATES.items():
        stmt = builder(user, 7, 10, "barbell-bench-press" if needs_exercise else None)
        compiled = stmt.compile()
        sql = str(compiled)
        assert "barbell" not in sql, name
        assert user in compiled.params.values(), name
        # 기간 하한은 datetime 바인딩 값 — 7일 이내 과거여야 한다
        cutoffs = [v for v in compiled.params.values() if hasattr(v, "year")]
        assert cutoffs, name


async def test_exercise_template_binds_uuid_and_slug(configured):
    configured["rows"] = [
        {"workout_date": "2026-08-10", "order_index": 0,
         "weight_kg": Decimal("60.00"), "reps": 10, "duration_seconds": 45},
    ]
    text, artifact = await history.run(USER_ID, {
        "template": "exercise_sets", "exercise": "벤치프레스", "days": 7,
    })
    assert artifact is None
    params = configured["stmt"].compile().params
    # user_id는 문자열이 아니라 BINARY(16) 바인딩 값, slug는 해석 통과분, 기간은 datetime 컷오프
    assert mysql.uuid_bytes(USER_ID) in params.values()
    assert "barbell-bench-press" in params.values()
    assert any(hasattr(value, "year") for value in params.values())
    # Decimal(60.00)은 60으로 표기 — LLM이 그대로 인용할 표
    assert "60" in text and "바벨 벤치프레스" in text


async def test_session_summary_needs_no_exercise(configured):
    configured["rows"] = [{"workout_date": "2026-08-11", "sessions": 1, "active_seconds": 1800}]
    text, _ = await history.run(USER_ID, {"template": "session_summary", "days": 14, "limit": 30})
    params = configured["stmt"].compile().params
    assert mysql.uuid_bytes(USER_ID) in params.values()
    assert 30 in params.values()   # limit — days는 datetime 컷오프로 바인딩된다
    assert "sessions" in text


async def test_unknown_exercise_returns_guidance_without_query(configured):
    text, artifact = await history.run(USER_ID, {
        "template": "exercise_pr", "exercise": "존재하지않는종목",
    })
    assert artifact is None
    assert "stmt" not in configured   # 해석 실패 시 DB에 나가면 안 된다
    assert "종목" in text


async def test_empty_result_says_no_data(configured):
    configured["rows"] = []
    text, _ = await history.run(USER_ID, {"template": "volume_by_exercise"})
    assert "없습니다" in text
    assert "지어내지" in text


async def test_aggregate_null_row_counts_as_no_data(configured):
    # 집계 템플릿은 기록이 없어도 NULL 1행이 온다 — 빈 결과와 같게 취급해야 한다
    configured["rows"] = [{"max_weight_kg": None, "best_e1rm_kg": None, "max_reps": None, "total_sets": None}]
    text, _ = await history.run(USER_ID, {"template": "exercise_pr", "exercise": "벤치프레스"})
    assert "없습니다" in text


async def test_unconfigured_env_falls_back_politely(monkeypatch):
    monkeypatch.setattr(mysql, "is_configured", lambda: False)
    text, artifact = await history.run(USER_ID, {"template": "session_summary"})
    assert artifact is None
    assert "설정되어 있지 않습니다" in text


async def test_dispatch_rejects_out_of_range_days(configured):
    # days 상한(365) 초과 — dispatch가 예외 대신 교정 지시를 LLM에 돌려준다
    text, artifact = await tools.dispatch(USER_ID, "QueryWorkoutHistory", {
        "template": "session_summary", "days": 9999,
    })
    assert artifact is None
    assert "인자가 올바르지 않습니다" in text
    assert "days" in text
