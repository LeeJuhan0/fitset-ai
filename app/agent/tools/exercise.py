import asyncio
import logging
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from app.agent.tools import failures
from app.core.exceptions import DomainError
from app.core.schemas import ResponseScheme
from app.exercises import repository as exercise_catalog
from app.exercises import service as exercises_service

logger = logging.getLogger("fitset")


class GetExerciseGuide(BaseModel):
    """특정 운동 종목의 자세·수행 방법을 설명할 때 쓴다. 종목명은 한글로 넣어도 된다."""

    exercise: str = Field(
        description="종목 식별자(slug) 또는 한글 종목명. 예: barbell-bench-press, 바벨 벤치프레스",
    )


async def run(user_id: str, args: dict) -> tuple[str, dict | None]:
    """종목 가이드 조회 후 설명 근거와 영상 페이로드 반환"""
    slug = exercise_catalog.resolve_exercise_slug(args.get("exercise", ""))
    if slug is None:
        hints = exercise_catalog.suggest_exercises(args.get("exercise", ""))
        if hints:
            return (
                f"'{args.get('exercise')}'와 정확히 일치하는 종목이 없습니다. "
                f"비슷한 종목: {', '.join(hints)}. 이 중 하나로 다시 호출하거나 사용자에게 확인한다."
            ), None
        return (
            f"'{args.get('exercise')}'에 해당하는 종목을 찾지 못했습니다. "
            "비슷한 이름의 종목을 되물어 확인한다."
        ), None

    try:
        video = await exercises_service.get_video(slug)
    except DomainError:
        logger.info("guide video unavailable: %s", slug)
        video = None

    detail: dict = {}
    detail_fault = False
    try:
        detail = await asyncio.to_thread(exercise_catalog.get_exercise, slug)
    except DomainError as exc:
        logger.warning("exercise detail lookup failed: %s", slug, exc_info=True)
        detail_fault = failures.is_server_fault(exc)

    name = detail.get("name") or exercise_catalog.exercise_name(slug) or slug
    instructions = detail.get("instructions") or []
    steps = " / ".join(instructions) if instructions else "(수행 방법 데이터 없음)"
    summary = (
        f"종목 '{name}' (slug: {slug})\n"
        f"장비: {detail.get('equipment', '미상')} · 난이도: {detail.get('difficulty', '미상')}\n"
        f"주동근: {', '.join(detail.get('primaryMuscles', [])) or '미상'}\n"
        f"수행 방법: {steps}\n"
        "위 수행 방법만 근거로 설명한다. 데이터가 없으면 동작을 지어내지 말고 그렇게 말한다."
    )
    if detail_fault:
        summary += (
            "\n상세 데이터는 서버 일시 장애로 빠졌다 — 유저의 계정·로그인 문제가 아니므로 "
            "계정 연결이나 재로그인을 언급하지 않는다."
        )

    payload = {
        "exerciseId": exercise_catalog.exercise_id(slug),
        "slug": slug,
        "exerciseName": name,
        "videoUrl": video["videoUrl"] if video else None,
        "expiresAt": video["expiresAt"] if video else None,
    }
    return summary, {"response_scheme": ResponseScheme.EXERCISE_GIF, "payload": payload}


class _GetExerciseGuideInput(GetExerciseGuide):
    user_id: Annotated[str, InjectedState("user_id")]


@tool(
    "GetExerciseGuide",
    args_schema=_GetExerciseGuideInput,
    description=GetExerciseGuide.__doc__,
    response_format="content_and_artifact",
)
async def get_exercise_guide(user_id: str, **args) -> tuple[str, dict | None]:
    """종목 가이드 도구 진입점, run 위임"""
    return await run(user_id, args)
