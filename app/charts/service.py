"""차트 서비스 — metric별 기록 조회와 집계 조립 (§7 chart payload).

metric마다 필요한 조회가 다르다 (2026-08-12 내부 API 파기 — 전부 DB 직조회):
  bodyWeight·bmi              → 체중 추이 (+bmi는 프로필의 키)
  exercisePr                  → 종목별 수행 세트
  workoutDuration·Frequency·weekdayFrequency → 세션 요약
  muscleVolume·Balance·topExercises          → 최근 기록 raw

집계는 domain(순수 함수)이 하고 여기선 조회와 조립만 한다.
데이터가 부족해 차트를 못 만들면 None — 호출부(차트 툴)가 텍스트로 강등한다.
"""
import asyncio
import logging

from app.charts import domain
from app.exercises.repository import get_exercise_meta
from app.users import repository as users_repository
from app.workouts import repository as workouts_repository

logger = logging.getLogger("fitset")

# 최근 기록 raw를 쓰는 metric의 조회 상한 — 세션·세트 중첩이라 페이로드가 크다
WORKOUT_RAW_MAX_DAYS = 90


async def build_payload(
    user_id: str,
    metric: str,
    days: int,
    muscle: str | None = None,
    exercise_slug: str | None = None,
    exercise_label: str | None = None,
) -> dict | None:
    """metric별로 필요한 기록만 조회해 payload를 만든다."""
    if metric in ("bodyWeight", "bmi"):
        items = await asyncio.to_thread(users_repository.get_body_weights, user_id, days)
        if metric == "bodyWeight":
            return domain.body_weight_chart(items)
        profile = await asyncio.to_thread(users_repository.get_profile, user_id)
        return domain.bmi_chart(items, profile.get("heightCm"))

    if metric == "exercisePr":
        if exercise_slug is None:
            return None
        sets = await asyncio.to_thread(
            workouts_repository.get_exercise_sets, user_id, exercise_slug, days
        )
        return domain.exercise_pr_chart(sets, exercise_label or exercise_slug)

    if metric in ("workoutDuration", "workoutFrequency", "weekdayFrequency"):
        sessions = await asyncio.to_thread(workouts_repository.get_workout_sessions, user_id, days)
        if metric == "workoutDuration":
            return domain.workout_duration_chart(sessions)
        if metric == "workoutFrequency":
            return domain.workout_frequency_chart(sessions)
        return domain.weekday_frequency_chart(sessions)

    # 남은 3종은 종목·세트 raw가 필요하다 — 조회 상한에 맞춰 자른다
    raw_days = min(days, WORKOUT_RAW_MAX_DAYS)
    if raw_days < days:
        logger.info("clamped workout raw window %d → %d days", days, raw_days)
    workouts = await asyncio.to_thread(workouts_repository.get_recent_workouts, user_id, raw_days)
    meta = get_exercise_meta()
    if metric == "muscleVolume":
        if muscle is None:
            return None
        return domain.muscle_volume_chart(workouts, meta, muscle)
    if metric == "muscleBalance":
        return domain.muscle_balance_chart(workouts, meta)
    return domain.top_exercises_chart(workouts, meta)
