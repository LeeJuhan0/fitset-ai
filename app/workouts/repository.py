"""운동 기록 조회 — 백엔드 MySQL 직조회 (구 내부 API §4.2·§4.5·§4.6 승계).

반환 형태는 구 spring 클라이언트와 동일하게 유지한다 — 소비자(routines·charts)의
로직이 데이터 출처 교체를 모르게 한다. 전 함수 동기 — 호출부가 asyncio.to_thread로 감싼다.

0 → null 변환은 구 백엔드 책임의 승계다 — ERD상 weight·duration_seconds·rest_seconds는
NOT NULL default 0이라 "0으로 수행"과 "미기록/맨몸/마지막 세트"가 구분되지 않는다.
와이어에서는 미기록을 null로 내보낸다 (구 명세 §4.5 규정).
"""
from datetime import datetime, timedelta

from sqlalchemy import select

from app.clients import mysql
from app.core import clock
from app.exercises.domain import Exercise
from app.workouts.domain import WorkoutHistory, WorkoutHistoryExercise, WorkoutHistorySet


def _cutoff(days: int) -> datetime:
    """기간 하한 — DB가 naive UTC라 tz를 벗겨 비교한다."""
    return (clock.now_utc() - timedelta(days=days)).replace(tzinfo=None)


def _nullify_zero(value):
    """NOT NULL default 0 컬럼의 0 → null (미기록·맨몸·마지막 세트)."""
    if not value:
        return None
    return float(value) if not isinstance(value, int) else value


def get_recent_workouts(user_id: str, days: int) -> list[dict]:
    """최근 운동 기록 raw (구 §4.2) — 세션 목록(종목·세트 중첩), 최신순."""
    sessions = mysql.fetch_all(
        select(WorkoutHistory.id, WorkoutHistory.started_at, WorkoutHistory.ended_at)
        .where(WorkoutHistory.user_id == mysql.uuid_bytes(user_id), WorkoutHistory.started_at >= _cutoff(days))
        .order_by(WorkoutHistory.started_at.desc())
    )
    if not sessions:
        return []

    detail_rows = mysql.fetch_all(
        select(
            WorkoutHistoryExercise.workout_history_id,
            WorkoutHistoryExercise.id.label("workout_exercise_id"),
            WorkoutHistoryExercise.order_index.label("exercise_order"),
            Exercise.slug,
            Exercise.name,
            WorkoutHistorySet.id.label("set_id"),
            WorkoutHistorySet.order_index.label("set_order"),
            WorkoutHistorySet.weight,
            WorkoutHistorySet.reps,
            WorkoutHistorySet.duration_seconds,
            WorkoutHistorySet.rest_seconds,
        )
        .select_from(WorkoutHistoryExercise)
        .join(Exercise, Exercise.id == WorkoutHistoryExercise.exercise_id)
        # 세트 없는 운동도 exercises 목록에는 나와야 한다 — outer join 후 세트 행만 걸러 담는다
        .join(WorkoutHistorySet, WorkoutHistorySet.workout_history_exercise_id == WorkoutHistoryExercise.id, isouter=True)
        .where(WorkoutHistoryExercise.workout_history_id.in_([row["id"] for row in sessions]))
        .order_by(WorkoutHistoryExercise.order_index, WorkoutHistorySet.order_index)
    )

    exercises_by_workout: dict[bytes, dict[bytes, dict]] = {}
    for row in detail_rows:
        exercises = exercises_by_workout.setdefault(row["workout_history_id"], {})
        exercise = exercises.setdefault(
            row["workout_exercise_id"],
            {
                "id": mysql.uuid_str(row["workout_exercise_id"]),
                "slug": row["slug"],
                "exerciseName": row["name"],
                "orderIndex": row["exercise_order"],
                "sets": [],
            },
        )
        if row["set_id"] is None:
            continue
        exercise["sets"].append(
            {
                "id": mysql.uuid_str(row["set_id"]),
                "orderIndex": row["set_order"],
                "weight": _nullify_zero(row["weight"]),
                "reps": row["reps"],
                "durationSec": _nullify_zero(row["duration_seconds"]),
                "restSec": _nullify_zero(row["rest_seconds"]),
            }
        )

    return [
        {
            "id": mysql.uuid_str(session["id"]),
            "startedAt": mysql.iso_from_db(session["started_at"]),
            "endedAt": mysql.iso_from_db(session["ended_at"]),
            "exercises": list(exercises_by_workout.get(session["id"], {}).values()),
        }
        for session in sessions
    ]


def get_exercise_sets(user_id: str, slug: str, days: int) -> list[dict]:
    """종목별 수행 세트 시계열 (구 §4.5) — 세션 중첩 없는 평탄 목록, performedAt 오름차순."""
    rows = mysql.fetch_all(
        select(
            WorkoutHistory.id.label("workout_id"),
            WorkoutHistory.started_at,
            WorkoutHistorySet.order_index,
            WorkoutHistorySet.weight,
            WorkoutHistorySet.reps,
            WorkoutHistorySet.duration_seconds,
            WorkoutHistorySet.rest_seconds,
        )
        .select_from(WorkoutHistory)
        .join(WorkoutHistoryExercise, WorkoutHistoryExercise.workout_history_id == WorkoutHistory.id)
        .join(Exercise, Exercise.id == WorkoutHistoryExercise.exercise_id)
        .join(WorkoutHistorySet, WorkoutHistorySet.workout_history_exercise_id == WorkoutHistoryExercise.id)
        .where(
            WorkoutHistory.user_id == mysql.uuid_bytes(user_id),
            Exercise.slug == slug,
            WorkoutHistory.started_at >= _cutoff(days),
        )
        .order_by(WorkoutHistory.started_at, WorkoutHistorySet.order_index)
    )
    return [
        {
            "workoutId": mysql.uuid_str(row["workout_id"]),
            "performedAt": mysql.iso_from_db(row["started_at"]),
            "orderIndex": row["order_index"],
            "weight": _nullify_zero(row["weight"]),
            "reps": row["reps"],
            "durationSec": _nullify_zero(row["duration_seconds"]),
            "restSec": _nullify_zero(row["rest_seconds"]),
        }
        for row in rows
    ]


def get_workout_sessions(user_id: str, days: int) -> list[dict]:
    """세션 요약 목록 (구 §4.6) — 종목·세트 없이 세션 메타만. 시간·빈도 차트용."""
    rows = mysql.fetch_all(
        select(WorkoutHistory.id, WorkoutHistory.started_at, WorkoutHistory.ended_at, WorkoutHistory.pause_seconds)
        .where(WorkoutHistory.user_id == mysql.uuid_bytes(user_id), WorkoutHistory.started_at >= _cutoff(days))
        .order_by(WorkoutHistory.started_at)
    )
    return [
        {
            "id": mysql.uuid_str(row["id"]),
            "startedAt": mysql.iso_from_db(row["started_at"]),
            "endedAt": mysql.iso_from_db(row["ended_at"]),
            # 순수 운동 시간 = 전체 경과 - 일시정지 (#99에서 pause_seconds로 대체됨)
            "activeDurationSeconds": _active_seconds(row["started_at"], row["ended_at"], row["pause_seconds"]),
        }
        for row in rows
    ]


def _active_seconds(started_at: datetime, ended_at: datetime, pause_seconds: int) -> int:
    """세션 순수 운동 시간(초) — 음수 방어 포함."""
    elapsed = int((ended_at - started_at).total_seconds())
    return max(0, elapsed - (pause_seconds or 0))
