import logging
from typing import Annotated, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from app.agent.tools import failures
from app.charts import service as charts_service
from app.core.exceptions import DomainError
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
    """차트 페이로드 생성 후 요약과 아티팩트 반환"""
    request = DrawChart(**args)

    exercise_slug = None
    exercise_label = None
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


class _DrawChartInput(DrawChart):
    user_id: Annotated[str, InjectedState("user_id")]


@tool(
    "DrawChart",
    args_schema=_DrawChartInput,
    description=DrawChart.__doc__,
    response_format="content_and_artifact",
)
async def draw_chart(user_id: str, **args) -> tuple[str, dict | None]:
    """차트 도구 진입점, run 위임"""
    return await run(user_id, args)
