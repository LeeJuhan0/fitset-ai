import math

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agent.tools import evidence
from app.clients import bedrock
from app.evidence.models import EvidenceChunk, EvidenceSituation


def _unit(similarity: float, dimension: int) -> list[float]:
    """쿼리 벡터 [1, 0, ...] 과 지정 코사인 유사도인 단위 벡터"""
    vector = [0.0] * dimension
    vector[0] = similarity
    vector[1] = math.sqrt(1 - similarity**2)
    return vector


@pytest.fixture
def evidence_rows(pg_engine, settings, monkeypatch):
    """유사도가 다른 근거 청크 3건 적재와 쿼리 임베딩 고정"""
    dimension = settings.embed_dimension
    monkeypatch.setattr(bedrock, "try_embed_query", lambda text: _unit(1.0, dimension))
    rows = []
    for index, (paper, similarities) in enumerate([
        ("2017_Jager_ISSN-protein", [0.95, 0.6]),
        ("2013_Schoenfeld_protein-timing", [0.9]),
        ("2014_Helms_contest-prep-nutrition", [0.7]),
        ("2021_Guest_ISSN-caffeine", [0.3]),
    ]):
        chunk_id = f"chunk-{index}"
        rows.append(EvidenceChunk(
            id=chunk_id, paper=paper, title=f"제목 {index}", population="대상", method="방법", effect="효과",
            propositions=["p"], source_text=f"원문 {index}", citation="c", doi_url=f"https://doi.org/{index}", license="CC BY",
            population_embedding=_unit(0.0, dimension), embedding_model="test",
        ))
        rows += [
            EvidenceSituation(chunk_id=chunk_id, situation=f"상황 {index}", embedding=_unit(s, dimension))
            for s in similarities
        ]
    with Session(pg_engine) as session, session.begin():
        session.add_all(rows)
    yield
    with pg_engine.begin() as conn:
        conn.execute(text("TRUNCATE evidence_chunks CASCADE"))


@pytest.mark.usefixtures("evidence_rows")
async def test_1위와_가까운_청크만_거리순으로_근거와_참고_논문이_된다():
    content, artifact = await evidence.run("운동 직후 단백질을 먹어야 하는지 궁금한 사람")

    assert content.index("제목 0") < content.index("제목 1")
    assert "제목 2" not in content
    assert "제목 3" not in content
    assert evidence.footer(artifact["citations"]) == (
        "\n\n참고 논문\n"
        "1. 제목 0 (Jager 2017)\n대상: 대상\n방법: 방법\n효과: 효과\nhttps://doi.org/0\n"
        "2. 제목 1 (Schoenfeld 2013)\n대상: 대상\n방법: 방법\n효과: 효과\nhttps://doi.org/1"
    )


async def test_근거_저장소가_없으면_인용하지_말라고_안내한다():
    content, artifact = await evidence.run("운동 직후 단백질을 먹어야 하는지 궁금한 사람")

    assert content == evidence.NO_EVIDENCE
    assert artifact is None
