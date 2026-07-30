"""운동 가이드 툴 — 종목 수행 방법 설명 + 가이드 영상 presigned URL.

종목 식별은 slug다. LLM이 slug를 지어낼 수 있으므로 **로컬 종목 마스터(206종)로 먼저
검증·해석**하고, 통과한 slug만 내부 API로 상세를 조회한다 (guardrails와 같은 취지 —
payload는 실존 데이터로만 채운다).

영상 URL은 clients/s3가 요청 시점에 서명한다. 저장된 대화를 다시 열면 만료돼 있으므로
클라는 클라이언트 API 명세 §6-B로 재발급받는다.
"""
import logging

from pydantic import BaseModel, Field

from app.agent import guardrails
from app.clients.spring import get_spring_client
from app.core.errors import DomainError
from app.exercises import service as exercises_service

logger = logging.getLogger("fitset")


class GetExerciseGuide(BaseModel):
    """특정 운동 종목의 자세·수행 방법을 설명할 때 쓴다. 종목명은 한글로 넣어도 된다."""

    exercise: str = Field(
        description="종목 식별자(slug) 또는 한글 종목명. 예: barbell-bench-press, 바벨 벤치프레스",
    )


async def run(user_id: str, args: dict) -> tuple[str, dict | None]:
    """종목 가이드를 조회해 (LLM에 돌려줄 설명 근거, exerciseGif payload)를 반환한다."""
    slug = guardrails.resolve_exercise_slug(args.get("exercise", ""))
    if slug is None:
        hints = guardrails.suggest_exercises(args.get("exercise", ""))
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
        # 영상이 없어도 설명은 할 수 있다 — payload만 비운다
        logger.info("guide video unavailable: %s", slug)
        video = None

    detail: dict = {}
    try:
        detail = await get_spring_client().get_exercise(slug)
    except DomainError:
        # 내부 API가 죽어도 로컬 마스터 이름만으로 답변은 가능하다
        logger.warning("exercise detail lookup failed: %s", slug, exc_info=True)

    name = detail.get("name") or guardrails.exercise_name(slug) or slug
    instructions = detail.get("instructions") or []
    steps = " / ".join(instructions) if instructions else "(수행 방법 데이터 없음)"
    summary = (
        f"종목 '{name}' (slug: {slug})\n"
        f"장비: {detail.get('equipment', '미상')} · 난이도: {detail.get('difficulty', '미상')}\n"
        f"주동근: {', '.join(detail.get('primaryMuscles', [])) or '미상'}\n"
        f"수행 방법: {steps}\n"
        "위 수행 방법만 근거로 설명한다. 데이터가 없으면 동작을 지어내지 말고 그렇게 말한다."
    )

    payload = {
        "slug": slug,
        "exerciseName": name,
        "videoUrl": video["videoUrl"] if video else None,
        "expiresAt": video["expiresAt"] if video else None,
    }
    return summary, {"response_scheme": "exerciseGif", "payload": payload}
