"""루틴 추천 툴 — 기존 루틴 워크로드(POST /v1/routines)를 그대로 재사용한다.

챗봇이 루틴을 새로 구성하지 않는다. routines.service.generate_routine을 호출해
룰 필터 → 임베딩 랭킹 → LLM 최종 선택 파이프라인을 타므로, 홈 화면 생성 결과와
챗봇 추천 결과가 같은 규칙을 따른다. payload도 클라이언트 API 명세 §1과 동일 구조다.
"""
import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.core.errors import DomainError
from app.routines import service as routines_service
from app.routines.schemas import RoutineGenerateRequest

logger = logging.getLogger("fitset")

Level = Literal["beginner", "intermediate", "advanced"]
Muscle = Literal[
    "back", "biceps", "calves", "chest", "core", "forearms",
    "glutes", "hamstrings", "quadriceps", "shoulders", "trapezius", "triceps",
]


class RecommendRoutine(BaseModel):
    """운동 루틴을 새로 추천한다. 유저가 루틴을 짜달라거나 기존 루틴 변경을 요청할 때 쓴다.

    운동 목적(goal)은 인자가 아니다 — 서버가 유저 프로필에서 가져온다.
    유저가 특정 목적을 말하면 context에 그대로 옮긴다.
    """

    # 세 필수 인자는 추측 금지 — 대화에서 알 수 없으면 시스템 프롬프트 규칙에 따라
    # 호출 전에 모르는 항목을 질문 하나로 모아 되묻는다 (직렬 3회 왕복 방지)
    level: Level = Field(description="유저 수준. 대화에서 파악한 값만 쓴다")
    muscle_groups: list[Muscle] = Field(min_length=1, max_length=12, description="타겟 부위. 대화에서 파악한 값만 쓴다")
    minutes: int = Field(ge=10, le=180, description="목표 운동 시간(분). 대화에서 파악한 값만 쓴다")
    context: str | None = Field(
        default=None, max_length=200,
        description="통증·기피·선호·목표 등 자유 서술. 유저가 말한 제약을 그대로 옮긴다",
    )
    include_warmup: bool = Field(default=True, description="워밍업 세트 포함 여부")


async def run(user_id: str, args: dict) -> tuple[str, dict | None]:
    """루틴을 생성해 (LLM에 돌려줄 요약, routine payload)를 반환한다."""
    # 툴 스키마(RecommendRoutine)로 먼저 검증해 기본값을 채운다 — LLM이 선택 필드를
    # 생략하면 곧장 RoutineGenerateRequest를 만들 때 필수 필드 누락으로 터진다
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
        # 후보 없음·가드레일 차단은 정상적인 결과다 — 대화를 끊지 않고 LLM이 사유를 설명하게 한다
        logger.info("recommend_routine declined: %s", exc.code)
        return f"루틴을 만들지 못했습니다. 사유: {exc.message}", None

    payload = routine.model_dump(by_alias=True)
    names = ", ".join(exercise.exercise_name for exercise in routine.exercises)
    summary = (
        f"루틴 '{routine.name}' 생성 완료 "
        f"({routine.estimated_minutes}분, {len(routine.exercises)}종목: {names}). "
        "구성은 앱이 카드로 보여주므로 답변에서는 왜 이렇게 골랐는지만 짧게 설명한다."
    )
    return summary, {"response_scheme": "routine", "payload": payload}
