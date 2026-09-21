from dataclasses import dataclass

import numpy as np
from sqlalchemy import or_, select

from app.clients import postgres
from app.routines.domain import LEVEL_ORDER, Routine

LEVEL_NAME = {order: name for name, order in LEVEL_ORDER.items()}


@dataclass(frozen=True)
class SearchFilters:
    muscle_groups: list[str]
    avoided: set[str]
    level: str
    minutes: int
    tolerance: float
    home_only: bool

    def minute_window(self) -> tuple[int, int]:
        """요청 시간 허용 오차 적용 정수 분 범위 계산"""
        span = int(self.minutes * self.tolerance)
        return self.minutes - span, self.minutes + span


def search_statement(filters: SearchFilters, query_vector, limit: int):
    """룰 필터 WHERE 와 코사인 정렬 SELECT 문 조립"""
    lo, hi = filters.minute_window()
    stmt = (
        select(Routine.slug, Routine.goal, Routine.level, Routine.minutes,
               Routine.muscle_groups, Routine.exercise_names, Routine.body)
        .where(
            Routine.muscle_groups.overlap(list(filters.muscle_groups)),
            ~Routine.muscle_groups.overlap(sorted(filters.avoided)),
            Routine.level <= LEVEL_ORDER[filters.level],
            or_(Routine.minutes.is_(None), Routine.minutes.between(lo, hi)),
        )
        .order_by(Routine.embedding.cosine_distance(np.asarray(query_vector, dtype=np.float32)))
        .limit(limit)
    )
    if filters.home_only:
        stmt = stmt.where(Routine.bodyweight_only.is_(True))
    return stmt


def search(filters: SearchFilters, query_vector, limit: int) -> list[dict]:
    """룰 필터 통과 루틴 코사인 유사도 상위 조회"""
    rows = postgres.fetch_all(search_statement(filters, query_vector, limit))
    return [
        {**row, "level": LEVEL_NAME.get(row["level"], "beginner"), "minutes_per_routine": row.pop("minutes")}
        for row in rows
    ]
