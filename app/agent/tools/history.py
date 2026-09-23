import asyncio
import logging
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select

from app.clients import mysql
from app.core import clock
from app.exercises import repository as exercise_catalog
from app.exercises.models import Exercise
from app.workouts.models import WorkoutHistory, WorkoutHistoryExercise, WorkoutHistorySet

logger = logging.getLogger("fitset")

MAX_ROWS = 50


def _since(days: int) -> datetime:
    """조회 기간 하한 naive UTC 시각 계산"""
    return (clock.now_utc() - timedelta(days=days)).replace(tzinfo=None)


def _user_sets(user_id: bytes, days: int) -> Select:
    """유저와 기간으로 좁힌 세트 조회 공통 조인 골격"""
    return (
        select()
        .select_from(WorkoutHistory)
        .join(WorkoutHistoryExercise, WorkoutHistoryExercise.workout_history_id == WorkoutHistory.id)
        .join(Exercise, Exercise.id == WorkoutHistoryExercise.exercise_id)
        .join(WorkoutHistorySet, WorkoutHistorySet.workout_exercise_history_id == WorkoutHistoryExercise.id)
        .where(WorkoutHistory.user_id == user_id, WorkoutHistory.started_at >= _since(days))
    )


def _session_summary(user_id: bytes, days: int, limit: int) -> Select:
    """최근 기간 날짜별 세션 수와 운동 시간 집계"""
    workout_date = func.date(WorkoutHistory.started_at).label("workout_date")
    active = WorkoutHistory.active_duration_seconds
    return (
        select(
            workout_date,
            func.count(func.distinct(WorkoutHistory.id)).label("sessions"),
            func.sum(active).label("active_seconds"),
        )
        .where(WorkoutHistory.user_id == user_id, WorkoutHistory.started_at >= _since(days))
        .group_by(workout_date)
        .order_by(workout_date.desc())
        .limit(limit)
    )


def _exercise_sets(user_id: bytes, days: int, limit: int, slug: str) -> Select:
    """특정 종목 최근 세트별 실측값 조회"""
    return (
        _user_sets(user_id, days)
        .with_only_columns(
            func.date(WorkoutHistory.started_at).label("workout_date"),
            WorkoutHistorySet.order_index,
            WorkoutHistorySet.weight.label("weight_kg"),
            WorkoutHistorySet.reps,
            WorkoutHistorySet.duration_seconds,
        )
        .where(Exercise.slug == slug)
        .order_by(WorkoutHistory.started_at.desc(), WorkoutHistorySet.order_index)
        .limit(limit)
    )


def _exercise_pr(user_id: bytes, days: int, limit: int, slug: str) -> Select:
    """특정 종목 최고 중량과 추정 1RM 최대치 조회"""
    return (
        _user_sets(user_id, days)
        .with_only_columns(
            func.max(WorkoutHistorySet.weight).label("max_weight_kg"),
            func.max(WorkoutHistorySet.weight * (1 + WorkoutHistorySet.reps / 30)).label("best_e1rm_kg"),
            func.max(WorkoutHistorySet.reps).label("max_reps"),
            func.count().label("total_sets"),
        )
        .where(Exercise.slug == slug, WorkoutHistorySet.weight > 0)
    )


def _volume_by_exercise(user_id: bytes, days: int, limit: int) -> Select:
    """기간 내 종목별 총볼륨 상위 조회"""
    total_volume = func.sum(WorkoutHistorySet.weight * WorkoutHistorySet.reps).label("total_volume_kg")
    return (
        _user_sets(user_id, days)
        .with_only_columns(Exercise.slug, Exercise.name, total_volume, func.count().label("total_sets"))
        .group_by(Exercise.id, Exercise.slug, Exercise.name)
        .order_by(total_volume.desc())
        .limit(limit)
    )


_TEMPLATES: dict[str, tuple] = {
    "session_summary": (_session_summary, False),
    "exercise_sets": (_exercise_sets, True),
    "exercise_pr": (_exercise_pr, True),
    "volume_by_exercise": (_volume_by_exercise, False),
}


class QueryWorkoutHistory(BaseModel):
    """유저의 운동 기록 원본을 조회할 때 쓴다 — 특정 종목의 세트 기록·최고 기록(PR),
    기간별 세션 요약, 볼륨 상위 종목. 차트가 아니라 수치·상세 내역으로 답할 때 사용한다."""

    template: Literal["session_summary", "exercise_sets", "exercise_pr", "volume_by_exercise"] = Field(
        description=(
            "session_summary=기간 날짜별 세션 요약, exercise_sets=특정 종목 세트 기록, "
            "exercise_pr=특정 종목 최고 기록, volume_by_exercise=볼륨 상위 종목"
        ),
    )
    days: int = Field(default=28, ge=1, le=365, description="조회 기간(일). 예: 최근 일주일=7")
    exercise: str | None = Field(
        default=None,
        description="종목 slug 또는 한글 종목명 — exercise_sets·exercise_pr에 필수",
    )
    limit: int = Field(default=10, ge=1, le=MAX_ROWS, description="반환 행 수 상한")


def _build_statement(query: "QueryWorkoutHistory", user_id: bytes, slug: str | None) -> Select:
    """템플릿 빌더 호출 종목 템플릿만 slug 전달"""
    builder, needs_exercise = _TEMPLATES[query.template]
    if needs_exercise:
        return builder(user_id, query.days, query.limit, slug)
    return builder(user_id, query.days, query.limit)


async def run(user_id: str, args: dict) -> tuple[str, dict | None]:
    """템플릿 선택 실행 후 결과 표 텍스트 반환"""
    if not mysql.is_configured():
        return (
            "기록 DB 직접 조회가 이 환경에 설정되어 있지 않습니다. "
            "기록·추이 질문이면 DrawChart 도구로 대신 답한다."
        ), None

    query = QueryWorkoutHistory(**args)
    _, needs_exercise = _TEMPLATES[query.template]

    slug = None
    if needs_exercise:
        slug = exercise_catalog.resolve_exercise_slug(query.exercise or "")
        if slug is None:
            hints = exercise_catalog.suggest_exercises(query.exercise or "")
            if hints:
                return (
                    f"'{query.exercise}'와 정확히 일치하는 종목이 없습니다. "
                    f"비슷한 종목: {', '.join(hints)}. 이 중 하나로 다시 호출하거나 사용자에게 확인한다."
                ), None
            return (
                f"{query.template} 템플릿에는 종목이 필요합니다. "
                "exercise 인자에 종목명을 넣어 다시 호출한다."
            ), None

    stmt = _build_statement(query, mysql.uuid_bytes(user_id), slug)
    rows = await asyncio.to_thread(mysql.fetch_all, stmt)
    if not rows or all(value is None for value in rows[0].values()):
        return (
            f"최근 {query.days}일 조회 결과가 없습니다. "
            "기록이 없다는 사실을 그대로 말하고, 수치를 지어내지 않는다."
        ), None
    return _render(query, slug, rows), None


def _render(query: QueryWorkoutHistory, slug: str | None, rows: list[dict]) -> str:
    """조회 행 목록을 LLM 인용용 표 텍스트로 변환"""
    header = f"[{query.template}] 최근 {query.days}일"
    if slug is not None:
        header += f" · 종목 {exercise_catalog.exercise_name(slug) or slug} ({slug})"
    columns = list(rows[0].keys())
    lines = [" | ".join(columns)]
    for row in rows[:MAX_ROWS]:
        lines.append(" | ".join(_cell(row[column]) for column in columns))
    return (
        f"{header}\n" + "\n".join(lines) + "\n"
        "위 조회 결과의 숫자만 인용해 답한다. 없는 값을 추정하지 않는다."
    )


def _cell(value) -> str:
    """셀 값 문자열화 Decimal 불필요 소수 제거"""
    if value is None:
        return "-"
    if isinstance(value, float) or str(type(value).__name__) == "Decimal":
        return f"{float(value):g}"
    return str(value)
