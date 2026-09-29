import asyncio
import logging
from dataclasses import dataclass, replace

import numpy as np
from sqlalchemy import or_, select

from app.clients import postgres
from app.core.config import get_settings
from app.routines.models import Routine
from app.routines.utils import LEVEL_ORDER

logger = logging.getLogger("fitset")

LEVEL_NAME = {order: name for name, order in LEVEL_ORDER.items()}


@dataclass(frozen=True)
class SearchFilters:
    muscle_groups: list[str]
    avoided: set[str]
    level: str
    minutes: int
    tolerance: float
    home_only: bool
    excluded_exercises: tuple[str, ...] = ()

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


def fetch(filters: SearchFilters, query_vector, limit: int) -> list[dict]:
    """룰 필터 통과 루틴 코사인 유사도 상위 조회"""
    rows = postgres.fetch_all(search_statement(filters, query_vector, limit))
    return [
        {**row, "level": LEVEL_NAME.get(row["level"], "beginner"), "minutes_per_routine": row.pop("minutes")}
        for row in rows
    ]


async def top_candidates(filters: SearchFilters, parsed_avoided: set[str], query_vector) -> list[dict]:
    """코사인 상위 후보, 문맥 기피로 비면 그것만 풀어 재검색"""
    limit = get_settings().cosine_top_k
    strict = replace(filters, avoided=filters.avoided | parsed_avoided)
    top = await asyncio.to_thread(fetch, strict, query_vector, limit)
    if not top and parsed_avoided:
        logger.info("parsed avoid %s emptied candidates, retrying without it", sorted(parsed_avoided))
        top = await asyncio.to_thread(fetch, filters, query_vector, limit)
    return without_excluded(top, filters.excluded_exercises)


def without_excluded(rows: list[dict], excluded: tuple[str, ...]) -> list[dict]:
    """기피 거절 종목 든 루틴 제외, 다 빠지면 원본 유지"""
    if not excluded:
        return rows
    kept = [row for row in rows if not _has_excluded(row.get("exercise_names") or [], excluded)]
    if kept:
        return kept
    logger.info("excluded exercises %s emptied candidates, keeping all", list(excluded))
    return rows


def _has_excluded(names: list[str], excluded: tuple[str, ...]) -> bool:
    """종목명 부분 일치로 제외 대상 포함 여부"""
    return any(word in name or name in word for name in names for word in excluded)
