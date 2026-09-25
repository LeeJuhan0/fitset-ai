import math

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.routines.models import Routine


def _embedding(similarity: float, dimension: int) -> list[float]:
    """llm 기본 쿼리 벡터와 지정 코사인 유사도인 단위 벡터"""
    vector = [0.0] * dimension
    vector[0] = similarity
    vector[1] = math.sqrt(1 - similarity**2)
    return vector


@pytest.fixture
def add_routine(pg_engine, settings: Settings):
    """루틴 검색 행을 실제 pgvector 에 적재"""
    def insert(
        slug: str,
        muscle_groups: list[str],
        exercises: list[tuple[str, str]],
        level: int = 0,
        minutes: int | None = 60,
        equipment: tuple[str, ...] = ("barbell",),
        similarity: float = 0.9,
    ) -> Routine:
        """루틴 1건 적재 후 행 반환"""
        body = {
            "slug": slug,
            "name": slug,
            "minutes_per_routine": minutes,
            "exercises": [
                {
                    "slug": exercise_slug,
                    "exercise_name": exercise_name,
                    "order_index": index,
                    "sets": [{"order_index": order, "reps": 5, "weight": None} for order in range(3)],
                }
                for index, (exercise_slug, exercise_name) in enumerate(exercises)
            ],
        }
        routine = Routine(
            slug=slug,
            name=slug,
            level=level,
            minutes=minutes,
            muscle_groups=muscle_groups,
            equipment=list(equipment),
            bodyweight_only=list(equipment) == ["bodyweight"],
            exercise_count=len(exercises),
            exercise_names=[name for _, name in exercises],
            body=body,
            embedding=_embedding(similarity, settings.embed_dimension),
            embedding_model="test",
        )
        with Session(pg_engine, expire_on_commit=False) as session:
            session.add(routine)
            session.commit()
        return routine
    return insert


@pytest.fixture
def leg_routine(add_routine, barbell_squat):
    """초급 60분 하체 바벨 스쿼트 루틴"""
    return add_routine("leg-day", ["quadriceps", "glutes"], [("barbell-squat", "바벨 스쿼트")])
