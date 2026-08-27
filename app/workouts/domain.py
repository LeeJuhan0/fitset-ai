"""운동 기록 엔티티 — 백엔드 MySQL 스키마 사본 (docs/백엔드 ERD.md와 1:1).

주인은 백엔드(Spring·JPA)다 — 읽기 전용 조회에만 쓰고, 조회에 필요한 컬럼만 선언한다.
weight·duration_seconds·rest_seconds는 NOT NULL default 0 — "0=미기록/맨몸/마지막 세트"
의 null 변환은 구 내부 API가 하던 책임이라 repository가 승계한다.
2026-08-28 실제 RDS(hangang-rds) 스키마로 정정: 공유본(fitset_app_01_04)과 달리 실제 DB는
workout_exercise_history·workout_exercise_set_history 이름이고 set 컬럼은 duration·rest,
세션 컬럼은 active_duration(활동 시간 초, pause_seconds 아님)이다. 파이썬 속성명은 종전
(duration_seconds·rest_seconds·active_duration_seconds)을 유지하고 DB 컬럼명만 매핑한다.
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
    active_duration_seconds: Mapped[int] = mapped_column("active_duration", Integer)


class WorkoutHistoryExercise(BackendBase):
    __tablename__ = "workout_exercise_history"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    workout_history_id: Mapped[bytes] = mapped_column(ForeignKey("workout_history.id"))
    exercise_id: Mapped[bytes] = mapped_column(ForeignKey("exercise.id"))
    order_index: Mapped[int] = mapped_column(Integer)


class WorkoutHistorySet(BackendBase):
    __tablename__ = "workout_exercise_set_history"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    workout_exercise_history_id: Mapped[bytes] = mapped_column(ForeignKey("workout_exercise_history.id"))
    order_index: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[int] = mapped_column("duration", Integer)
    rest_seconds: Mapped[int] = mapped_column("rest", Integer)
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 2))  # kg
    reps: Mapped[int] = mapped_column(Integer)
