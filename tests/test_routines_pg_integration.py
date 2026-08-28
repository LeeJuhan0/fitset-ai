"""루틴 검색 통합 테스트 — 실제 Postgres(pgvector) 에서 룰 필터 5개와 코사인 정렬을 검증한다.

PG_TEST_DSN 이 없으면 전부 건너뛴다. CI 는 pgvector/pgvector:pg17 서비스 컨테이너로 채우고,
로컬은 docker run -e POSTGRES_PASSWORD=x -e POSTGRES_USER=admin -e POSTGRES_DB=fitset -p 55432:5432 pgvector/pgvector:pg17
뒤 PG_TEST_DSN=postgresql://admin:x@localhost:55432/fitset 으로 돌린다.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import make_url

from app.clients import postgres
from app.core.errors import DomainError
from app.routines import repository
from app.routines.domain import Routine

DSN = os.environ.get("PG_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PG_TEST_DSN 미설정")

DDL = Path(__file__).resolve().parents[1] / "scripts" / "sql" / "routines_pgvector.sql"
DIM = 1024


def _unit(seed: int) -> np.ndarray:
    vector = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
    return vector / np.linalg.norm(vector)


QUERY = _unit(0)

# 쿼리와의 유사도를 seed 로 조절한다. near 는 쿼리에 가깝고 far 는 무작위.
def _near(weight: float) -> np.ndarray:
    vector = weight * QUERY + (1 - weight) * _unit(99)
    return vector / np.linalg.norm(vector)


ROWS = [
    # slug, level, minutes, muscle_groups, equipment, bodyweight_only, embedding
    ("chest-close", 0, 60, ["chest", "triceps"], ["barbell"], False, _near(0.9)),
    ("chest-far", 0, 60, ["chest"], ["dumbbell"], False, _near(0.2)),
    ("chest-advanced", 2, 60, ["chest"], ["barbell"], False, _near(0.95)),
    ("chest-long", 0, 120, ["chest"], ["barbell"], False, _near(0.95)),
    ("chest-shoulders", 0, 60, ["chest", "shoulders"], ["barbell"], False, _near(0.95)),
    ("chest-home", 0, 60, ["chest"], ["bodyweight"], True, _near(0.5)),
    ("chest-nominutes", 0, None, ["chest"], ["barbell"], False, _near(0.3)),
    ("back-only", 0, 60, ["back"], ["barbell"], False, _near(0.99)),
]


@pytest.fixture(scope="module")
def seeded():
    engine = create_engine(DSN)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS routines"))
        ddl = "\n".join(line for line in DDL.read_text().splitlines() if not line.lstrip().startswith("--"))
        for statement in ddl.split(";"):
            if statement.strip():
                conn.execute(text(statement))
        for slug, level, minutes, muscles, equipment, bodyweight_only, embedding in ROWS:
            conn.execute(
                text("""
                    INSERT INTO routines (slug, level, minutes, muscle_groups, equipment, bodyweight_only,
                                          exercise_count, exercise_names, body, embedding, embedding_model)
                    VALUES (:slug, :level, :minutes, :muscles, :equipment, :bodyweight_only,
                            1, ARRAY['푸시업'], CAST(:body AS jsonb), CAST(:embedding AS vector), 'test')
                """),
                {
                    "slug": slug, "level": level, "minutes": minutes, "muscles": muscles,
                    "equipment": equipment, "bodyweight_only": bodyweight_only,
                    "body": '{"slug": "%s", "exercises": []}' % slug,
                    "embedding": "[" + ",".join(f"{v:.6f}" for v in embedding) + "]",
                },
            )
    engine.dispose()
    yield


@pytest.fixture
def client(monkeypatch, seeded):
    url = make_url(DSN)
    monkeypatch.setattr(postgres, "get_settings", lambda: SimpleNamespace(
        pg_host=url.host, pg_port=url.port or 5432, pg_user=url.username, pg_password=url.password,
        pg_database=url.database, pg_connect_timeout=2, pg_statement_timeout=2.0, pg_pool_max=2,
    ))
    postgres._engine.cache_clear()
    yield
    postgres.close()


def _slugs(filters):
    return [row["slug"] for row in repository.search(filters, QUERY, 30)]


def _filters(**overrides):
    base = dict(muscle_groups=["chest"], avoided=set(), level="beginner", minutes=60, tolerance=0.2, home_only=False)
    base.update(overrides)
    return repository.SearchFilters(**base)


def test_muscle_intersection_and_cosine_order(client):
    slugs = _slugs(_filters())
    assert "back-only" not in slugs                       # 부위 교집합 없음
    assert "chest-advanced" not in slugs                  # 수준 상한
    assert "chest-long" not in slugs                      # 시간 ±20% 밖
    assert "chest-nominutes" in slugs                     # minutes NULL 은 통과
    assert slugs.index("chest-close") < slugs.index("chest-far")   # 코사인 내림차순


def test_avoided_excludes_any_overlap(client):
    assert "chest-shoulders" in _slugs(_filters())
    assert "chest-shoulders" not in _slugs(_filters(avoided={"shoulders"}))


def test_level_upper_bound_is_inclusive(client):
    assert "chest-advanced" in _slugs(_filters(level="advanced"))


def test_home_only_keeps_bodyweight_routines(client):
    assert _slugs(_filters(home_only=True)) == ["chest-home"]


def test_session_is_read_only(client):
    # default_transaction_read_only=on 이라 쓰기 문은 DB 가 거부하고 클라이언트가 DomainError 로 번역한다
    stmt = insert(Routine).values(
        slug="x", level=0, muscle_groups=[], bodyweight_only=False, exercise_names=[], body={},
        embedding=[0.0] * DIM,
    ).returning(Routine.slug)
    with pytest.raises(DomainError) as info:
        postgres.fetch_all(stmt)
    assert "read-only" in str(info.value.__cause__)


def test_health_ping(client):
    assert postgres.ping() is True
