"""routines/repository.search 유닛 테스트 — 룰 필터 5개의 SQL 표현과 바인딩 값.

domain.passes_filters 가 하던 판정이 WHERE 절로 옮겨가므로, 절이 하나라도 빠지면
필터가 조용히 풀린다. 실제 판정은 CI 의 pgvector 통합 테스트(5단계)가 맡는다.
"""
import numpy as np

from app.routines import repository


def _filters(**overrides):
    base = dict(muscle_groups=["chest", "triceps"], avoided={"shoulders"}, level="intermediate",
                minutes=60, tolerance=0.2, home_only=False)
    base.update(overrides)
    return repository.SearchFilters(**base)


def test_sql_has_every_rule_filter_and_cosine_order():
    sql = repository.SEARCH_SQL
    assert "muscle_groups && %(muscles)s" in sql
    assert "NOT (muscle_groups && %(avoided)s)" in sql
    assert "level <= %(level)s" in sql
    assert "minutes IS NULL OR minutes BETWEEN %(lo)s AND %(hi)s" in sql
    assert "%(home_only)s = false OR bodyweight_only" in sql
    assert "ORDER BY embedding <=> %(query)s" in sql
    assert "LIMIT %(limit)s" in sql


def test_bind_maps_level_and_minute_window():
    params = _filters().bind()
    assert params["level"] == 1
    assert (params["lo"], params["hi"]) == (48, 72)
    assert params["avoided"] == ["shoulders"]
    assert params["home_only"] is False


def test_bind_empty_avoided_is_always_true_clause():
    # 기피 재시도는 avoided 를 비워 다시 부르는 것뿐이라 분기가 없다
    assert _filters(avoided=set()).bind()["avoided"] == []


def test_search_passes_vector_and_limit(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"], captured["params"] = sql, params
        return [{"slug": "a", "exercise_names": ["x"], "body": {"slug": "a"}}]

    monkeypatch.setattr(repository.postgres, "fetch_all", fake_fetch_all)
    rows = repository.search(_filters(), [0.1] * 4, limit=30)
    assert rows[0]["slug"] == "a"
    assert captured["params"]["limit"] == 30
    assert captured["params"]["query"].dtype == np.float32
    assert captured["params"]["muscles"] == ["chest", "triceps"]
