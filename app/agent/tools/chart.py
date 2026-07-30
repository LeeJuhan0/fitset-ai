"""차트 툴 — 기록·추이 질문을 §7 chart payload로 만든다.

metric마다 필요한 내부 API가 다르다 (내부 API 명세 §4-B.1):
  bodyWeight·bmi              → §4.4 체중 추이 (+bmi는 §4.1 프로필의 키)
  exercisePr                  → §4.5 종목별 수행 세트
  workoutDuration·Frequency·weekdayFrequency → §4.6 세션 요약
  muscleVolume·Balance·topExercises          → §4.2 최근 기록 raw

집계는 agent/charts.py(순수 함수)가 하고 여기선 조회와 조립만 한다.
데이터가 모자라 차트를 못 만들면 payload를 비워 텍스트로 강등한다 — 빈 차트는 §7 위반.
"""
import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.agent import charts, guardrails
from app.clients.spring import get_spring_client
from app.core.errors import DomainError
from app.routines.repository import get_exercise_meta

logger = logging.getLogger("fitset")

Metric = Literal[
    "bodyWeight",
    "bmi",
    "exercisePr",
    "workoutDuration",
    "workoutFrequency",
    "weekdayFrequency",
    "muscleVolume",
    "muscleBalance",
    "topExercises",
]
Muscle = Literal[
    "back", "biceps", "calves", "chest", "core", "forearms",
    "glutes", "hamstrings", "quadriceps", "shoulders", "trapezius", "triceps",
]

DEFAULT_DAYS = 90
MAX_DAYS = 730
# §4.2(최근 기록 raw)를 쓰는 metric은 백엔드 days 상한(협의 포인트 ③)에 묶인다
WORKOUT_RAW_MAX_DAYS = 90


class DrawChart(BaseModel):
    """유저의 기록·추이를 차트로 보여준다. 체중·볼륨·운동 시간·빈도·PR 질문에 쓴다."""

    metric: Metric = Field(description="그릴 지표")
    days: int = Field(default=DEFAULT_DAYS, ge=7, le=MAX_DAYS, description="조회 기간(일)")
    exercise: str | None = Field(
        default=None, description="metric=exercisePr일 때 대상 종목 — slug 또는 한글명",
    )
    muscle: Muscle | None = Field(
        default=None, description="metric=muscleVolume일 때 대상 부위",
    )


async def run(user_id: str, args: dict) -> tuple[str, dict | None]:
    """차트를 만들어 (LLM에 돌려줄 요약, chart payload)를 반환한다."""
    request = DrawChart(**args)

    # 종목 해석 실패는 "기록 부족"과 다르다 — 후보를 돌려줘 LLM이 정확한 이름으로 재호출하게 한다
    if request.metric == "exercisePr":
        slug = guardrails.resolve_exercise_slug(request.exercise or "")
        if slug is None:
            hints = guardrails.suggest_exercises(request.exercise or "")
            if hints:
                return (
                    f"'{request.exercise}'와 정확히 일치하는 종목이 없습니다. "
                    f"비슷한 종목: {', '.join(hints)}. 이 중 하나로 다시 호출하거나 사용자에게 확인한다."
                ), None
            return f"'{request.exercise}' 종목을 찾지 못했습니다. 사용자에게 종목명을 확인한다.", None

    try:
        payload = await _build(user_id, request)
    except DomainError as exc:
        logger.info("draw_chart failed: %s", exc.code)
        return f"기록을 불러오지 못했습니다. 사유: {exc.message}", None

    if payload is None:
        return (
            "차트를 그릴 만한 기록이 부족합니다. 데이터가 쌓이면 다시 보여줄 수 있다고 안내한다."
        ), None

    values = payload["series"][0]["values"]
    summary = (
        f"차트 생성 완료 — {payload['title']} ({payload['yLabel']}). "
        f"구간 {payload['x'][0]}~{payload['x'][-1]}, 값 {values}. "
        "차트는 앱이 렌더링하므로 답변에서는 이 수치가 뜻하는 변화만 짧게 해석한다."
    )
    return summary, {"response_scheme": "chart", "payload": payload}


async def _build(user_id: str, request: DrawChart) -> dict | None:
    """metric별로 필요한 내부 API만 호출해 payload를 만든다."""
    client = get_spring_client()

    if request.metric in ("bodyWeight", "bmi"):
        items = await client.get_body_weights(user_id, request.days)
        if request.metric == "bodyWeight":
            return charts.body_weight_chart(items)
        profile = await client.get_profile(user_id)
        return charts.bmi_chart(items, profile.get("heightCm"))

    if request.metric == "exercisePr":
        slug = guardrails.resolve_exercise_slug(request.exercise or "")
        if slug is None:
            return None
        sets = await client.get_exercise_sets(user_id, slug, request.days)
        return charts.exercise_pr_chart(sets, guardrails.exercise_name(slug) or slug)

    if request.metric in ("workoutDuration", "workoutFrequency", "weekdayFrequency"):
        sessions = await client.get_workout_sessions(user_id, request.days)
        if request.metric == "workoutDuration":
            return charts.workout_duration_chart(sessions)
        if request.metric == "workoutFrequency":
            return charts.workout_frequency_chart(sessions)
        return charts.weekday_frequency_chart(sessions)

    # 남은 3종은 종목·세트 raw가 필요해 §4.2를 쓴다 — 백엔드 days 상한에 맞춰 자른다
    days = min(request.days, WORKOUT_RAW_MAX_DAYS)
    if days < request.days:
        logger.info("clamped workout raw window %d → %d days", request.days, days)
    workouts = await client.get_recent_workouts(user_id, days)
    meta = get_exercise_meta()
    if request.metric == "muscleVolume":
        if request.muscle is None:
            return None
        return charts.muscle_volume_chart(workouts, meta, request.muscle)
    if request.metric == "muscleBalance":
        return charts.muscle_balance_chart(workouts, meta)
    return charts.top_exercises_chart(workouts, meta)
