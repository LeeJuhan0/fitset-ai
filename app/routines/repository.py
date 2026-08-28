"""루틴 저장소 — Postgres(pgvector) `routines` 검색.

search() 가 룰 필터 5개와 코사인 상위 K 를 쿼리 1건으로 처리한다(docs/루틴 저장소 pgvector.md).
반환 행의 body 는 세트 상세까지 담긴 전체 루틴 dict 라 응답 조립에 그대로 쓴다.
"""
from dataclasses import dataclass

import numpy as np
from sqlalchemy import or_, select

from app.clients import postgres
from app.routines.domain import LEVEL_ORDER, Routine

LEVEL_NAME = {order: name for name, order in LEVEL_ORDER.items()}


@dataclass(frozen=True)
class SearchFilters:
    """룰 필터 입력 — service 가 요청·프로필·기록에서 조립한다."""

    muscle_groups: list[str]
    avoided: set[str]
    level: str
    minutes: int
    tolerance: float
    home_only: bool

    def minute_window(self) -> tuple[int, int]:
        """±tolerance 를 정수 분으로 닫은 범위."""
        span = int(self.minutes * self.tolerance)
        return self.minutes - span, self.minutes + span


def search_statement(filters: SearchFilters, query_vector, limit: int):
    """룰 필터 5개 WHERE + 코사인 ORDER BY + LIMIT. 기피는 빈 배열이면 항상 참이라 재시도 분기가 없다."""
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
    """룰 필터 통과 루틴을 쿼리 벡터와의 코사인 순으로 상위 limit 건. 동기 — 스레드에서 호출.

    level 은 이름으로, minutes 는 minutes_per_routine 키로 돌려 프롬프트(prompts.pick_best)와
    응답 조립이 종전 DynamoDB 항목과 같은 키를 쓰게 한다.
    """
    rows = postgres.fetch_all(search_statement(filters, query_vector, limit))
    return [
        {**row, "level": LEVEL_NAME.get(row["level"], "beginner"), "minutes_per_routine": row.pop("minutes")}
        for row in rows
    ]
