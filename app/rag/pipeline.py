from app.rag import embed, rerank, retrieve
from app.rag.retrieve import SearchFilters


async def find_routine(description: str, filters: SearchFilters, parsed_avoided: set[str]) -> dict | None:
    """임베딩, 룰 필터 코사인 검색, LLM 선택 순서로 루틴 1건"""
    vector = await embed.query_vector(description)
    top = await retrieve.top_candidates(filters, parsed_avoided, vector)
    if not top:
        return None
    return await rerank.choose(top, description)
