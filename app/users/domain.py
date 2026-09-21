from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BINARY, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.orm import BackendBase


class UserProfile(BackendBase):
    __tablename__ = "user_profile"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    user_id: Mapped[bytes] = mapped_column(BINARY(16), unique=True)
    gender: Mapped[str] = mapped_column(String(10))
    birth_date: Mapped[date] = mapped_column(Date)
    height: Mapped[Decimal] = mapped_column(Numeric(5, 1))
    workout_goal: Mapped[str] = mapped_column(String(20))
    level: Mapped[str] = mapped_column(String(20))


class UserAvoidedMuscle(BackendBase):
    __tablename__ = "user_avoided_muscle"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    user_id: Mapped[bytes] = mapped_column(BINARY(16))
    muscle_id: Mapped[bytes] = mapped_column(ForeignKey("muscle.id"))


class BodyWeightHistory(BackendBase):
    __tablename__ = "body_weight_history"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    user_id: Mapped[bytes] = mapped_column(BINARY(16))
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    measured_at: Mapped[datetime] = mapped_column(DateTime)
