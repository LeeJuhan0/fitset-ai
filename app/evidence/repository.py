import numpy as np
from sqlalchemy import func, select

from app.clients import postgres
from app.evidence.models import EvidenceChunk, EvidenceSituation


def search(query_vector: list[float], limit: int, max_distance: float) -> list[dict]:
    """상황 문장 코사인 거리로 가까운 근거 청크 조회"""
    vector = np.asarray(query_vector, dtype=np.float32)
    best = (
        select(EvidenceSituation.chunk_id, func.min(EvidenceSituation.embedding.cosine_distance(vector)).label("distance"))
        .group_by(EvidenceSituation.chunk_id)
        .subquery()
    )
    stmt = (
        select(
            EvidenceChunk.id, EvidenceChunk.paper, EvidenceChunk.title,
            EvidenceChunk.population, EvidenceChunk.method, EvidenceChunk.effect,
            EvidenceChunk.doi_url, best.c.distance,
        )
        .join(best, best.c.chunk_id == EvidenceChunk.id)
        .where(best.c.distance < max_distance)
        .order_by(best.c.distance)
        .limit(limit)
    )
    return postgres.fetch_all(stmt)
