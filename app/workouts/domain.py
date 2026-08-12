"""운동 기록 엔티티 — 백엔드 MySQL 스키마 사본 (docs/백엔드 ERD.md와 1:1).

주인은 백엔드(Spring·JPA)다 — 읽기 전용 조회에만 쓰고, 조회에 필요한 컬럼만 선언한다.
weight_kg·duration_seconds·rest_seconds는 NOT NULL default 0 — "0=미기록/맨몸/마지막 세트"
의 null 변환은 구 내부 API가 하던 책임이라 repository가 승계한다.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BINARY, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.core.orm import BackendBase


class Workout(BackendBase):
    __tablename__ = "workout"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    user_id: Mapped[bytes] = mapped_column(BINARY(16))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime] = mapped_column(DateTime)
    active_duration_seconds: Mapped[int] = mapped_column(Integer)


class WorkoutExercise(BackendBase):
    __tablename__ = "workout_exercise"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    workout_id: Mapped[bytes] = mapped_column(ForeignKey("workout.id"))
    exercise_id: Mapped[bytes] = mapped_column(ForeignKey("exercise.id"))
    order_index: Mapped[int] = mapped_column(Integer)


class WorkoutSet(BackendBase):
    __tablename__ = "workout_set"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    workout_exercise_id: Mapped[bytes] = mapped_column(ForeignKey("workout_exercise.id"))
    order_index: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    rest_seconds: Mapped[int] = mapped_column(Integer)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    reps: Mapped[int] = mapped_column(Integer)
