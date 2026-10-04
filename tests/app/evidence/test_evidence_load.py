from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.clients import bedrock
from app.evidence import load
from app.evidence.models import EvidenceChunk, EvidenceSituation

CHUNK = {
    "id": "jager2017-01",
    "paper": "2017_Jager_ISSN-protein",
    "title": "일일 단백질 권장량",
    "summary": ["대상: 근육을 늘리려는 운동인", "방법: 하루 1.4–2.0 g/kg 섭취", "효과: 근육 증가와 유지에 충분함"],
    "propositions": ["Daily protein of 1.4–2.0 g/kg is sufficient."],
    "source_text": "an overall daily protein intake in the range of 1.4–2.0 g/kg is sufficient",
    "citation": "J Int Soc Sports Nutr. 2017",
    "doi_url": "https://doi.org/10.1186/s12970-017-0177-8",
    "license": "CC BY",
    "situations": ["단백질 하루에 얼마나 먹어야 하는지 궁금한 사람", "근육 키우려고 프로틴 양을 정하려는 사람"],
}


def test_적재하면_청크와_상황이_임베딩과_함께_교체된다(pg_engine, settings, monkeypatch):
    embedded = []
    monkeypatch.setattr(bedrock, "embed_query", lambda text: embedded.append(text) or [0.1] * settings.embed_dimension)

    for _ in range(2):
        with Session(pg_engine) as session, session.begin():
            load.replace_all(session, load.to_rows(CHUNK))

    with Session(pg_engine) as session:
        chunk = session.get(EvidenceChunk, "jager2017-01")
        assert chunk.population == "근육을 늘리려는 운동인"
        assert chunk.effect == "근육 증가와 유지에 충분함"
        assert session.scalar(select(func.count()).select_from(EvidenceChunk)) == 1
        assert session.scalar(select(func.count()).select_from(EvidenceSituation)) == 2
    assert embedded[:3] == ["근육을 늘리려는 운동인", *CHUNK["situations"]]
    with pg_engine.begin() as conn:
        conn.execute(text("TRUNCATE evidence_chunks CASCADE"))
