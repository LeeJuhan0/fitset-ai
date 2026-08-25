"""운동 기록 직접 조회 툴 — 템플릿 기반 NL2SQL (이슈 #36).

LLM에게 SQL을 짓게 하지 않는다 — 조회 4종은 backend_schema 엔티티 기반 select()로
서버가 고정해 두고, LLM은 템플릿 이름과 파라미터(종목·기간·상위 N)만 고른다.
값은 전부 바인딩 파라미터로만 주입되므로 인젝션 표면이 없고, 컬럼·테이블명은
엔티티 선언(ERD 사본)과 어긋나면 임포트 시점에 드러난다.

user_id는 툴 스키마에 없다 — JWT에서 뽑은 값을 그래프가 주입한다 (사칭 방지,
tools/__init__ 규약). 종목명은 로컬 마스터 206종으로 slug를 해석해 통과한 것만
바인딩한다 (exercise 툴과 동일 패턴).
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select

from app.clients import mysql
from app.core import clock
from app.exercises import repository as exercise_catalog
from app.exercises.domain import Exercise
from app.workouts.domain import WorkoutHistory, WorkoutHistoryExercise, WorkoutHistorySet

logger = logging.getLogger("fitset")

# 유저에게 그대로 읽어줄 수 있는 행 수 상한 — LLM 컨텍스트 낭비 방지
MAX_ROWS = 50


def _since(days: int) -> datetime:
    """기간 하한 — DB가 naive UTC 저장이라 tz를 벗겨 비교한다 (repository들과 동일 규약)."""
    return (clock.now_utc() - timedelta(days=days)).replace(tzinfo=None)


def _user_sets(user_id: bytes, days: int) -> Select:
    """세트 조회의 공통 골격 — 유저·기간으로 좁힌 history→exercise→set 조인."""
    return (
        select()
        .select_from(WorkoutHistory)
        .join(WorkoutHistoryExercise, WorkoutHistoryExercise.workout_history_id == WorkoutHistory.id)
        .join(Exercise, Exercise.id == WorkoutHistoryExercise.exercise_id)
        .join(WorkoutHistorySet, WorkoutHistorySet.workout_history_exercise_id == WorkoutHistoryExercise.id)
        .where(WorkoutHistory.user_id == user_id, WorkoutHistory.started_at >= _since(days))
    )


def _session_summary(user_id: bytes, days: int, limit: int, slug: str | None) -> Select:
    """최근 N일 날짜별 세션 수·순수 운동 시간."""
    workout_date = func.date(WorkoutHistory.started_at).label("workout_date")
    # 순수 운동 시간 = 전체 경과 - 일시정지 (#99 active_duration_seconds → pause_seconds)
    active = mysql.seconds_between(
        WorkoutHistory.started_at, WorkoutHistory.ended_at
    ) - WorkoutHistory.pause_seconds
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


def _exercise_sets(user_id: bytes, days: int, limit: int, slug: str | None) -> Select:
    """특정 종목의 최근 세트별 실측값."""
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


def _exercise_pr(user_id: bytes, days: int, limit: int, slug: str | None) -> Select:
    """특정 종목 최고 중량·Epley e1RM 최대치 (무게 추천.md와 같은 공식)."""
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


def _volume_by_exercise(user_id: bytes, days: int, limit: int, slug: str | None) -> Select:
    """기간 내 종목별 총볼륨(무게×횟수) 상위."""
    total_volume = func.sum(WorkoutHistorySet.weight * WorkoutHistorySet.reps).label("total_volume_kg")
    return (
        _user_sets(user_id, days)
        .with_only_columns(Exercise.slug, Exercise.name, total_volume, func.count().label("total_sets"))
        .group_by(Exercise.id, Exercise.slug, Exercise.name)
        .order_by(total_volume.desc())
        .limit(limit)
    )


# 템플릿 레지스트리 — (select 빌더, 종목 필요 여부)
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


async def run(user_id: str, args: dict) -> tuple[str, dict | None]:
    """템플릿을 골라 실행하고 (LLM에 돌려줄 결과 표, None)을 반환한다. payload 없음(§7 text)."""
    if not mysql.is_configured():
        return (
            "기록 DB 직접 조회가 이 환경에 설정되어 있지 않습니다. "
            "기록·추이 질문이면 DrawChart 도구로 대신 답한다."
        ), None

    query = QueryWorkoutHistory(**args)
    builder, needs_exercise = _TEMPLATES[query.template]

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

    stmt = builder(mysql.uuid_bytes(user_id), query.days, query.limit, slug)
    rows = await asyncio.to_thread(mysql.fetch_all, stmt)
    if not rows or all(value is None for value in rows[0].values()):
        # 집계 템플릿은 기록이 없어도 NULL 1행이 온다 — 빈 결과와 같게 취급
        return (
            f"최근 {query.days}일 조회 결과가 없습니다. "
            "기록이 없다는 사실을 그대로 말하고, 수치를 지어내지 않는다."
        ), None
    return _render(query, slug, rows), None


def _render(query: QueryWorkoutHistory, slug: str | None, rows: list[dict]) -> str:
    """행 목록 → LLM이 인용할 결과 표 텍스트. 숫자는 조회 결과만 싣는다."""
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
    """셀 값 문자열화 — MySQL Decimal은 불필요한 소수 없이 표기한다."""
    if value is None:
        return "-"
    if isinstance(value, float) or str(type(value).__name__) == "Decimal":
        return f"{float(value):g}"
    return str(value)
