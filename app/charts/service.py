"""차트 서비스 — metric별 내부 API 조회와 집계 조립 (§7 chart payload).

metric마다 필요한 내부 API가 다르다 (내부 API 명세 §4-B.1):
  bodyWeight·bmi              → §4.4 체중 추이 (+bmi는 §4.1 프로필의 키)
  exercisePr                  → §4.5 종목별 수행 세트
  workoutDuration·Frequency·weekdayFrequency → §4.6 세션 요약
  muscleVolume·Balance·topExercises          → §4.2 최근 기록 raw

집계는 domain(순수 함수)이 하고 여기선 조회와 조립만 한다.
데이터가 부족해 차트를 못 만들면 None — 호출부(차트 툴)가 텍스트로 강등한다.
"""
import logging

from app.charts import domain
from app.clients.spring import get_spring_client
from app.exercises.repository import get_exercise_meta

logger = logging.getLogger("fitset")

# §4.2(최근 기록 raw)를 쓰는 metric은 백엔드 days 상한(협의 포인트 ③)에 묶인다
WORKOUT_RAW_MAX_DAYS = 90


async def build_payload(
    user_id: str,
    metric: str,
    days: int,
    muscle: str | None = None,
    exercise_slug: str | None = None,
    exercise_label: str | None = None,
) -> dict | None:
    """metric별로 필요한 내부 API만 호출해 payload를 만든다."""
    client = get_spring_client()

    if metric in ("bodyWeight", "bmi"):
        items = await client.get_body_weights(user_id, days)
        if metric == "bodyWeight":
            return domain.body_weight_chart(items)
        profile = await client.get_profile(user_id)
        return domain.bmi_chart(items, profile.get("heightCm"))

    if metric == "exercisePr":
        if exercise_slug is None:
            return None
        sets = await client.get_exercise_sets(user_id, exercise_slug, days)
        return domain.exercise_pr_chart(sets, exercise_label or exercise_slug)

    if metric in ("workoutDuration", "workoutFrequency", "weekdayFrequency"):
        sessions = await client.get_workout_sessions(user_id, days)
        if metric == "workoutDuration":
            return domain.workout_duration_chart(sessions)
        if metric == "workoutFrequency":
            return domain.workout_frequency_chart(sessions)
        return domain.weekday_frequency_chart(sessions)

    # 남은 3종은 종목·세트 raw가 필요해 §4.2를 쓴다 — 백엔드 days 상한에 맞춰 자른다
    raw_days = min(days, WORKOUT_RAW_MAX_DAYS)
    if raw_days < days:
        logger.info("clamped workout raw window %d → %d days", days, raw_days)
    workouts = await client.get_recent_workouts(user_id, raw_days)
    meta = get_exercise_meta()
    if metric == "muscleVolume":
        if muscle is None:
            return None
        return domain.muscle_volume_chart(workouts, meta, muscle)
    if metric == "muscleBalance":
        return domain.muscle_balance_chart(workouts, meta)
    return domain.top_exercises_chart(workouts, meta)
