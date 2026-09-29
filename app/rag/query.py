import asyncio
import logging

from app.clients import bedrock
from app.rag import prompts
from app.routines.utils import MUSCLE_LABELS_KO, MUSCLES

logger = logging.getLogger("fitset")

MUSCLE_BY_LABEL = {label: muscle for muscle, label in MUSCLE_LABELS_KO.items()}


def clean_query_analysis(description: str, avoid_muscles: list[str], unsafe_constraints: list[str]) -> tuple[str | None, set[str], list[str]]:
    """분석 결과 정제, 한글 부위 이름을 부위 코드로"""
    if not description.strip():
        return None, set(), []
    avoid = {MUSCLE_BY_LABEL.get(muscle, muscle) for muscle in avoid_muscles} & MUSCLES
    unsafe = [constraint for constraint in unsafe_constraints if constraint.strip()]
    return description.strip(), avoid, unsafe


async def analyze(goal: str, level: str, muscle_groups: list[str], minutes: int, context: str | None) -> tuple[str, set[str], list[str]]:
    """요청 LLM 분석, 묘사문 기피 부위 안전 제약, 실패 시 템플릿"""
    system, user = prompts.describe_query(goal, level, muscle_groups, minutes, context)
    fallback = prompts.fallback_description(goal, level, muscle_groups, minutes)
    analysis = await asyncio.to_thread(bedrock.try_structured, "describe", prompts.QueryAnalysis, system, user)
    if analysis is None:
        return fallback, set(), []
    description, avoided, unsafe = clean_query_analysis(
        analysis.description, analysis.avoid_muscles, analysis.unsafe_constraints,
    )
    if description is None:
        logger.warning("describe LLM returned empty description, falling back to template")
        return fallback, set(), []
    return description, avoided, unsafe
