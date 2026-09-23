from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, SmallInteger, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.orm import SearchBase


class Routine(SearchBase):
    __tablename__ = "routines"

    slug: Mapped[str] = mapped_column(Text, primary_key=True)
    goal: Mapped[str | None] = mapped_column(Text)
    level: Mapped[int] = mapped_column(SmallInteger)
    minutes: Mapped[int | None] = mapped_column(SmallInteger)
    muscle_groups: Mapped[list[str]] = mapped_column(ARRAY(Text))
    bodyweight_only: Mapped[bool] = mapped_column(Boolean)
    exercise_names: Mapped[list[str]] = mapped_column(ARRAY(Text))
    body: Mapped[dict] = mapped_column(JSONB)
    embedding = mapped_column(Vector(1024))
