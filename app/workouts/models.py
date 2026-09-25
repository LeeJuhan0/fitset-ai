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
    active_duration_seconds: Mapped[int] = mapped_column("active_duration", Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkoutHistoryExercise(BackendBase):
    __tablename__ = "workout_exercise_history"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    workout_history_id: Mapped[bytes] = mapped_column(ForeignKey("workout_history.id"))
    exercise_id: Mapped[bytes] = mapped_column(ForeignKey("exercise.id"))
    order_index: Mapped[int] = mapped_column("sort_order", Integer)


class WorkoutHistorySet(BackendBase):
    __tablename__ = "workout_exercise_set_history"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    workout_exercise_history_id: Mapped[bytes] = mapped_column(ForeignKey("workout_exercise_history.id"))
    order_index: Mapped[int] = mapped_column("sort_order", Integer)
    duration_seconds: Mapped[int] = mapped_column("duration", Integer)
    rest_seconds: Mapped[int] = mapped_column("rest", Integer)
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    reps: Mapped[int] = mapped_column(Integer)
