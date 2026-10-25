from datetime import datetime

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Boolean, DateTime, Identity, Index, Integer, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.orm import SearchBase


class Routine(SearchBase):
    """루틴 검색 행, 룰 필터와 임베딩과 본문"""

    __tablename__ = "routines"
    __table_args__ = (
        Index("routines_muscle_gin", "muscle_groups", postgresql_using="gin"),
        Index("routines_level_minutes", "level", "minutes"),
        Index("routines_bodyweight", "level", "minutes", postgresql_where="bodyweight_only"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    goal: Mapped[str | None] = mapped_column(Text)
    level: Mapped[int] = mapped_column(SmallInteger)
    minutes: Mapped[int | None] = mapped_column(SmallInteger)
    muscle_groups: Mapped[list[str]] = mapped_column(ARRAY(Text))
    equipment: Mapped[list[str]] = mapped_column(ARRAY(Text))
    bodyweight_only: Mapped[bool] = mapped_column(Boolean)
    exercise_count: Mapped[int] = mapped_column(SmallInteger)
    exercise_names: Mapped[list[str]] = mapped_column(ARRAY(Text))
    body: Mapped[dict] = mapped_column(JSONB)
    embedding = mapped_column(HALFVEC(1024), nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
