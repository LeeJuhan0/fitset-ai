from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Identity, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.orm import SearchBase


class EvidenceChunk(SearchBase):
    """논문 근거 청크, 대상 임베딩과 프롬프트 본문"""

    __tablename__ = "evidence_chunks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    paper: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    population: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    effect: Mapped[str] = mapped_column(Text)
    propositions: Mapped[list[str]] = mapped_column(ARRAY(Text))
    source_text: Mapped[str] = mapped_column(Text)
    citation: Mapped[str] = mapped_column(Text)
    doi_url: Mapped[str] = mapped_column(Text)
    license: Mapped[str] = mapped_column(Text)
    population_embedding = mapped_column(Vector(1024), nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceSituation(SearchBase):
    """근거 청크가 답하는 사용자 상황 문장과 임베딩"""

    __tablename__ = "evidence_situations"

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(Text, ForeignKey("evidence_chunks.id", ondelete="CASCADE"), index=True)
    situation: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(1024), nullable=False)
