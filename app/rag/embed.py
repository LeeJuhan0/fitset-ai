import asyncio

from app.clients import bedrock
from app.core.exceptions import AiUnavailableError


async def query_vector(description: str) -> list[float]:
    """묘사문 쿼리 임베딩, 실패 시 503"""
    vector = await asyncio.to_thread(bedrock.try_embed_query, description)
    if vector is None:
        raise AiUnavailableError()
    return vector
