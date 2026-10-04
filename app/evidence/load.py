import argparse
import json
import logging
from pathlib import Path

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.clients import bedrock, postgres
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.evidence.models import EvidenceChunk, EvidenceSituation

logger = logging.getLogger("fitset")


def to_rows(chunk: dict) -> list[EvidenceChunk | EvidenceSituation]:
    """청크 1건을 Titan 임베딩 포함 행으로"""
    population, method, effect = (line.split(":", 1)[1].strip() for line in chunk["summary"])
    row = EvidenceChunk(
        id=chunk["id"],
        paper=chunk["paper"],
        title=chunk["title"],
        population=population,
        method=method,
        effect=effect,
        propositions=chunk["propositions"],
        source_text=chunk["source_text"],
        citation=chunk["citation"],
        doi_url=chunk["doi_url"],
        license=chunk["license"],
        population_embedding=bedrock.embed_query(population),
        embedding_model=get_settings().embed_model_id,
    )
    situations = [
        EvidenceSituation(chunk_id=chunk["id"], situation=situation, embedding=bedrock.embed_query(situation))
        for situation in chunk["situations"]
    ]
    return [row, *situations]


def replace_all(session: Session, rows: list[EvidenceChunk | EvidenceSituation]) -> None:
    """근거 테이블 전체를 새 행으로 교체"""
    session.execute(delete(EvidenceChunk))
    session.add_all(rows)


def main() -> None:
    """chunks.json 임베딩 후 근거 테이블 적재"""
    parser = argparse.ArgumentParser()
    parser.add_argument("chunks", type=Path)
    args = parser.parse_args()
    configure_logging()
    chunks = json.loads(args.chunks.read_text())
    rows = [row for chunk in chunks for row in to_rows(chunk)]
    engine = create_engine(postgres.url())
    with Session(engine) as session, session.begin():
        replace_all(session, rows)
    engine.dispose()
    logger.info("evidence loaded chunks=%d rows=%d", len(chunks), len(rows))


if __name__ == "__main__":
    main()
