from datetime import datetime, timedelta

from sqlalchemy import select

from app.clients import mysql
from app.core import clock
from app.exercises.models import Exercise
from app.workouts.models import WorkoutHistory, WorkoutHistoryExercise, WorkoutHistorySet


def _cutoff(days: int) -> datetime:
    """조회 기간 하한 naive UTC 시각 계산"""
    return (clock.now_utc() - timedelta(days=days)).replace(tzinfo=None)


def _nullify_zero(value):
    """기본값 0 컬럼의 0 을 미기록 null 로 변환"""
    if not value:
        return None
    return float(value) if not isinstance(value, int) else value


def get_recent_workouts(user_id: str, days: int) -> list[dict]:
    """최근 세션별 종목 세트 중첩 기록 최신순 조회"""
    sessions = mysql.fetch_all(
        select(WorkoutHistory.id, WorkoutHistory.started_at, WorkoutHistory.ended_at)
        .where(
            WorkoutHistory.user_id == mysql.uuid_bytes(user_id),
            WorkoutHistory.deleted_at.is_(None),
            WorkoutHistory.started_at >= _cutoff(days),
        )
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
        .join(WorkoutHistorySet, WorkoutHistorySet.workout_exercise_history_id == WorkoutHistoryExercise.id, isouter=True)
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
    """종목별 수행 세트 평탄 시계열 오름차순 조회"""
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
        .join(WorkoutHistorySet, WorkoutHistorySet.workout_exercise_history_id == WorkoutHistoryExercise.id)
        .where(
            WorkoutHistory.user_id == mysql.uuid_bytes(user_id),
            WorkoutHistory.deleted_at.is_(None),
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
    """세션 시작 종료 활동 시간 요약 목록 조회"""
    rows = mysql.fetch_all(
        select(WorkoutHistory.id, WorkoutHistory.started_at, WorkoutHistory.ended_at, WorkoutHistory.active_duration_seconds)
        .where(
            WorkoutHistory.user_id == mysql.uuid_bytes(user_id),
            WorkoutHistory.deleted_at.is_(None),
            WorkoutHistory.started_at >= _cutoff(days),
        )
        .order_by(WorkoutHistory.started_at)
    )
    return [
        {
            "id": mysql.uuid_str(row["id"]),
            "startedAt": mysql.iso_from_db(row["started_at"]),
            "endedAt": mysql.iso_from_db(row["ended_at"]),
            "activeDurationSeconds": row["active_duration_seconds"] or 0,
        }
        for row in rows
    ]
