"""유저 프로필·신체 기록 엔티티 — 백엔드 MySQL 스키마 사본 (docs/백엔드 ERD.md와 1:1).

주인은 백엔드(Spring·JPA)다 — 읽기 전용 조회에만 쓰고, 조회에 필요한 컬럼만 선언한다.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BINARY, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.orm import BackendBase


class UserProfile(BackendBase):
    __tablename__ = "user_profile"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    user_id: Mapped[bytes] = mapped_column(BINARY(16), unique=True)
    gender: Mapped[str] = mapped_column(String(10))        # ENUM 대문자 (MALE | FEMALE)
    birth_date: Mapped[date] = mapped_column(Date)
    height_cm: Mapped[Decimal] = mapped_column(Numeric(5, 1))
    workout_goal: Mapped[str] = mapped_column(String(20))  # ENUM 대문자 (HYPERTROPHY …)
    level: Mapped[str] = mapped_column(String(20))         # ENUM 대문자 (BEGINNER …)


class UserAvoidedMuscle(BackendBase):
    __tablename__ = "user_avoided_muscle"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    user_id: Mapped[bytes] = mapped_column(BINARY(16))
    muscle_id: Mapped[bytes] = mapped_column(ForeignKey("muscle.id"))


class BodyWeightLog(BackendBase):
    __tablename__ = "body_weight_log"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    user_id: Mapped[bytes] = mapped_column(BINARY(16))
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    measured_at: Mapped[datetime] = mapped_column(DateTime)
