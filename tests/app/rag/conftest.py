import random

import pytest

from app.core.config import Settings
from app.rag.retrieve import SearchFilters
from tests.app.routines.conftest import add_routine  # noqa: F401


@pytest.fixture
def search_filters() -> SearchFilters:
    """중급 60분 대퇴사두근 룰 필터, 기피 없음 헬스장"""
    return SearchFilters(
        muscle_groups=["quadriceps"], avoided=set(), level="intermediate", minutes=60, tolerance=0.2, home_only=False,
    )


@pytest.fixture
def query_vector(settings: Settings) -> list[float]:
    """llm 픽스처 기본 임베딩과 같은 쿼리 벡터"""
    return [1.0] + [0.0] * (settings.embed_dimension - 1)


@pytest.fixture
def search_routines(add_routine):  # noqa: F811
    """룰 필터 통과 5건과 부위 수준 시간으로 걸러질 3건"""
    squat = [("barbell-squat", "바벨 스쿼트")]
    add_routine("leg-best", ["quadriceps", "glutes"], squat, level=0, minutes=60, similarity=0.95)
    add_routine("leg-second", ["quadriceps"], squat, level=1, minutes=70, similarity=0.9)
    add_routine("leg-hamstring", ["quadriceps", "hamstrings"], squat, level=0, minutes=50, similarity=0.85)
    add_routine("home-leg", ["quadriceps"], [("air-squat", "맨몸 스쿼트")], equipment=("bodyweight",), similarity=0.6)
    add_routine("leg-no-minutes", ["quadriceps"], squat, minutes=None, similarity=0.5)
    add_routine("leg-advanced", ["quadriceps"], squat, level=2, similarity=0.99)
    add_routine("leg-long", ["quadriceps"], squat, minutes=90, similarity=0.98)
    add_routine("chest-day", ["chest"], [("bench-press", "벤치프레스")], similarity=0.99)


@pytest.fixture
def candidates() -> list[dict]:
    """코사인 순서로 조회된 후보 루틴 세 건"""
    return [
        {"slug": "leg-best", "goal": "hypertrophy", "level": "beginner", "minutes_per_routine": 60,
         "muscle_groups": ["quadriceps", "glutes"], "exercise_names": ["바벨 스쿼트", "런지"], "body": {}},
        {"slug": "leg-second", "goal": "strength", "level": "intermediate", "minutes_per_routine": 70,
         "muscle_groups": ["quadriceps"], "exercise_names": ["프론트 스쿼트"], "body": {}},
        {"slug": "leg-third", "goal": "endurance", "level": "beginner", "minutes_per_routine": 50,
         "muscle_groups": ["hamstrings"], "exercise_names": ["루마니안 데드리프트"], "body": {}},
    ]


@pytest.fixture
def ordered_sample(monkeypatch):
    """랜덤 샘플을 앞에서부터 k개 고르기로 고정"""
    monkeypatch.setattr(random, "sample", lambda population, k: list(population)[:k])
