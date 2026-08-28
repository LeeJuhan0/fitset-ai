"""routines/repository.search 유닛 테스트 — 룰 필터 5개의 SQL 표현.

domain.passes_filters 가 하던 판정이 WHERE 절로 옮겨가므로, 절이 하나라도 빠지면
필터가 조용히 풀린다. 실제 판정은 CI 의 pgvector 통합 테스트(5단계)가 맡는다.
"""
from sqlalchemy.dialects import postgresql

from app.routines import repository


def _filters(**overrides):
    base = dict(muscle_groups=["chest", "triceps"], avoided={"shoulders"}, level="intermediate",
                minutes=60, tolerance=0.2, home_only=False)
    base.update(overrides)
    return repository.SearchFilters(**base)


def _compile(stmt) -> tuple[str, dict]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def test_statement_has_every_rule_filter_and_cosine_order():
    sql, params = _compile(repository.search_statement(_filters(), [0.1] * 4, 30))
    assert "routines.muscle_groups && " in sql
    assert "NOT (routines.muscle_groups && " in sql
    assert "routines.level <= " in sql
    assert "routines.minutes IS NULL OR routines.minutes BETWEEN" in sql
    assert "routines.embedding <=> " in sql
    assert "LIMIT" in sql
    assert params["level_1"] == 1
    assert (params["minutes_1"], params["minutes_2"]) == (48, 72)
    assert params["muscle_groups_1"] == ["chest", "triceps"]
    assert params["muscle_groups_2"] == ["shoulders"]


def test_home_only_adds_bodyweight_clause():
    sql, _ = _compile(repository.search_statement(_filters(home_only=True), [0.1] * 4, 30))
    assert "routines.bodyweight_only IS true" in sql
    sql, _ = _compile(repository.search_statement(_filters(home_only=False), [0.1] * 4, 30))
    assert "bodyweight_only" not in sql


def test_empty_avoided_is_always_true_clause():
    # 기피 재시도는 avoided 를 비워 다시 부르는 것뿐이라 분기가 없다
    _, params = _compile(repository.search_statement(_filters(avoided=set()), [0.1] * 4, 30))
    assert params["muscle_groups_2"] == []


def test_search_passes_statement_to_client(monkeypatch):
    captured = {}

    def fake_fetch_all(stmt):
        captured["stmt"] = stmt
        return []

    monkeypatch.setattr(repository.postgres, "fetch_all", fake_fetch_all)
    repository.search(_filters(), [0.1] * 4, limit=30)
    assert "routines" in str(captured["stmt"])
