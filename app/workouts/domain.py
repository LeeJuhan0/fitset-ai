"""운동 기록 엔티티 — 백엔드 MySQL 스키마 사본 (docs/백엔드 ERD.md와 1:1).

주인은 백엔드(Spring·JPA)다 — 읽기 전용 조회에만 쓰고, 조회에 필요한 컬럼만 선언한다.
weight·duration_seconds·rest_seconds는 NOT NULL default 0 — "0=미기록/맨몸/마지막 세트"
의 null 변환은 구 내부 API가 하던 책임이라 repository가 승계한다.
2026-08-25 백엔드 리팩토링 반영: workout*→workout_history* 개명(#92·#95),
weight_kg→weight(#93), active_duration_seconds→pause_seconds(#99 — 순수 운동 시간은
(ended_at - started_at) - pause_seconds로 계산한다).
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BINARY, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.core.orm import BackendBase


class WorkoutHistory(BackendBase):
    __tablename__ = "workout_history"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    user_id: Mapped[bytes] = mapped_column(BINARY(16))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime] = mapped_column(DateTime)
    pause_seconds: Mapped[int] = mapped_column(Integer)


class WorkoutHistoryExercise(BackendBase):
    __tablename__ = "workout_history_exercise"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    workout_history_id: Mapped[bytes] = mapped_column(ForeignKey("workout_history.id"))
    exercise_id: Mapped[bytes] = mapped_column(ForeignKey("exercise.id"))
    order_index: Mapped[int] = mapped_column(Integer)


class WorkoutHistorySet(BackendBase):
    __tablename__ = "workout_history_set"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    workout_history_exercise_id: Mapped[bytes] = mapped_column(ForeignKey("workout_history_exercise.id"))
    order_index: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    rest_seconds: Mapped[int] = mapped_column(Integer)
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 2))  # kg
    reps: Mapped[int] = mapped_column(Integer)
