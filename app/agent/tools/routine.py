import logging
from typing import Annotated, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from app.core.schemas import ResponseScheme
from app.agent.tools import failures
from app.core.exceptions import DomainError
from app.routines import service as routines_service
from app.routines.schemas import RoutineGenerateRequest

logger = logging.getLogger("fitset")

Level = Literal["beginner", "intermediate", "advanced"]
Muscle = Literal[
    "back", "biceps", "calves", "chest", "core", "forearms",
    "glutes", "hamstrings", "quadriceps", "shoulders", "trapezius", "triceps",
]


class RecommendRoutine(BaseModel):
    """운동 루틴을 새로 추천한다. 유저가 루틴을 짜달라거나 새 조건으로 다시 추천해달라고 할 때 쓴다.

    운동 시간, 수준, 타겟 부위 중 대화에서 알 수 없는 것이 있으면 이 도구를 부르지 말고
    모르는 항목 전부를 질문 하나에 모아 되묻는다. 값을 지어내지 않고, 셋 다 파악된 뒤에만 부른다.

    운동 목적(goal)은 인자가 아니다 — 서버가 유저 프로필에서 가져온다.
    유저가 특정 목적을 말하면 context에 그대로 옮긴다.
    """

    level: Level = Field(description="유저 수준. 대화에서 파악한 값만 쓴다")
    muscle_groups: list[Muscle] = Field(min_length=1, max_length=12, description="타겟 부위. 대화에서 파악한 값만 쓴다")
    minutes: int = Field(ge=10, le=180, description="목표 운동 시간(분). 대화에서 파악한 값만 쓴다")
    context: str | None = Field(
        default=None, max_length=200,
        description="통증·기피·선호·목표 등 자유 서술. 유저가 말한 제약을 그대로 옮긴다",
    )
    include_warmup: bool = Field(default=True, description="워밍업 세트 포함 여부")


async def run(user_id: str, args: dict) -> tuple[str, dict | None]:
    """루틴 생성 서비스 호출 후 요약과 페이로드 반환"""
    params = RecommendRoutine(**args)
    request = RoutineGenerateRequest(
        level=params.level,
        muscle_groups=params.muscle_groups,
        minutes=params.minutes,
        context=params.context,
        include_warmup=params.include_warmup,
    )
    try:
        routine = await routines_service.generate_routine(user_id, request)
    except DomainError as exc:
        if failures.is_server_fault(exc):
            logger.warning("recommend_routine server fault: %s", exc.code, exc_info=True)
            return failures.SERVER_FAULT_NOTE, None
        logger.info("recommend_routine declined: %s", exc.code)
        return f"루틴을 만들지 못했습니다. 사유: {exc.message}", None

    payload = routine.model_dump(by_alias=True)
    names = ", ".join(exercise.exercise_name for exercise in routine.exercises)
    summary = (
        f"루틴 '{routine.name}' 생성 완료 "
        f"({routine.estimated_minutes}분, {len(routine.exercises)}종목: {names}). "
        "구성은 앱이 카드로 보여주므로 답변에서는 왜 이렇게 골랐는지만 짧게 설명한다."
    )
    return summary, {"response_scheme": ResponseScheme.ROUTINE, "payload": payload}


class _RecommendRoutineInput(RecommendRoutine):
    user_id: Annotated[str, InjectedState("user_id")]


@tool(
    "RecommendRoutine",
    args_schema=_RecommendRoutineInput,
    description=RecommendRoutine.__doc__,
    response_format="content_and_artifact",
)
async def recommend_routine(user_id: str, **args) -> tuple[str, dict | None]:
    """루틴 추천 도구 진입점, run 위임"""
    return await run(user_id, args)
