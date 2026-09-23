import asyncio
import logging
import random

from app.clients import bedrock
from app.core.config import get_settings
from app.rag import prompts

logger = logging.getLogger("fitset")


async def _pick_with_llm(sampled: list[dict], description: str) -> dict | None:
    """샘플 후보 중 LLM 최적 선택, 실패 시 None"""
    system, user = prompts.pick_best(sampled, description)
    picked = await asyncio.to_thread(bedrock.try_structured, "pick", prompts.PickedRoutine, system, user)
    if picked is None:
        return None
    if not 1 <= picked.number <= len(sampled):
        logger.warning("pick LLM chose out-of-range %d, falling back to cosine top-1", picked.number)
        return None
    return sampled[picked.number - 1]


async def choose(top: list[dict], description: str) -> dict:
    """상위 후보 랜덤 샘플 후 LLM 선택, 실패 시 코사인 1위"""
    sampled = random.sample(top, min(get_settings().llm_candidate_count, len(top)))
    chosen = await _pick_with_llm(sampled, description)
    if chosen is None:
        return top[0]
    return chosen
