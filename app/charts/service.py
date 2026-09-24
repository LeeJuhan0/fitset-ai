import asyncio
import logging

from app.charts import utils
from app.exercises.repository import get_exercise_catalog
from app.users import repository as users_repository
from app.workouts import repository as workouts_repository

logger = logging.getLogger("fitset")

WORKOUT_RAW_MAX_DAYS = 90


async def build_payload(
    user_id: str,
    metric: str,
    days: int,
    muscle: str | None = None,
    exercise_slug: str | None = None,
    exercise_label: str | None = None,
) -> dict | None:
    """지표별 필요 기록 조회 후 차트 페이로드 생성"""
    if metric in ("bodyWeight", "bmi"):
        items = await asyncio.to_thread(users_repository.get_body_weights, user_id, days)
        if metric == "bodyWeight":
            return utils.body_weight_chart(items)
        profile = await asyncio.to_thread(users_repository.get_profile, user_id)
        return utils.bmi_chart(items, profile.get("heightCm"))

    if metric == "exercisePr":
        if exercise_slug is None:
            return None
        sets = await asyncio.to_thread(
            workouts_repository.get_exercise_sets, user_id, exercise_slug, days
        )
        return utils.exercise_pr_chart(sets, exercise_label or exercise_slug)

    if metric in ("workoutDuration", "workoutFrequency", "weekdayFrequency"):
        sessions = await asyncio.to_thread(workouts_repository.get_workout_sessions, user_id, days)
        if metric == "workoutDuration":
            return utils.workout_duration_chart(sessions)
        if metric == "workoutFrequency":
            return utils.workout_frequency_chart(sessions)
        return utils.weekday_frequency_chart(sessions)

    raw_days = min(days, WORKOUT_RAW_MAX_DAYS)
    if raw_days < days:
        logger.info("clamped workout raw window %d → %d days", days, raw_days)
    workouts = await asyncio.to_thread(workouts_repository.get_recent_workouts, user_id, raw_days)
    meta = get_exercise_catalog()
    if metric == "muscleVolume":
        if muscle is None:
            return None
        return utils.muscle_volume_chart(workouts, meta, muscle)
    if metric == "muscleBalance":
        return utils.muscle_balance_chart(workouts, meta)
    return utils.top_exercises_chart(workouts, meta)
