"""차트 툴 — 기록·추이 질문을 §7 chart payload로 만든다.

조회·집계는 charts 패키지(service·domain)가 하고 여기선 LLM 입출력 어댑터만 맡는다 —
인자 스키마(DrawChart), 종목명 해석과 재호출 유도, 실패의 LLM 지시문 변환.
"""
import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.tools import failures
from app.charts import service as charts_service
from app.core.errors import DomainError
from app.core.schemas import ResponseScheme
from app.exercises import repository as exercise_catalog

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

    exercise_slug = None
    exercise_label = None
    # 종목 해석 실패는 "기록 부족"과 다르다 — 후보를 돌려줘 LLM이 정확한 이름으로 재호출하게 한다
    if request.metric == "exercisePr":
        exercise_slug = exercise_catalog.resolve_exercise_slug(request.exercise or "")
        if exercise_slug is None:
            hints = exercise_catalog.suggest_exercises(request.exercise or "")
            if hints:
                return (
                    f"'{request.exercise}'와 정확히 일치하는 종목이 없습니다. "
                    f"비슷한 종목: {', '.join(hints)}. 이 중 하나로 다시 호출하거나 사용자에게 확인한다."
                ), None
            return f"'{request.exercise}' 종목을 찾지 못했습니다. 사용자에게 종목명을 확인한다.", None
        exercise_label = exercise_catalog.exercise_name(exercise_slug) or exercise_slug

    try:
        payload = await charts_service.build_payload(
            user_id,
            request.metric,
            request.days,
            muscle=request.muscle,
            exercise_slug=exercise_slug,
            exercise_label=exercise_label,
        )
    except DomainError as exc:
        if failures.is_server_fault(exc):
            logger.warning("draw_chart server fault: %s", exc.code)
            return failures.SERVER_FAULT_NOTE, None
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
    return summary, {"response_scheme": ResponseScheme.CHART, "payload": payload}
