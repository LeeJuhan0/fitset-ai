"""운동 마스터 엔티티 — 백엔드 MySQL 스키마 사본 (docs/백엔드 ERD.md와 1:1).

주인은 백엔드(Spring·JPA)다 — 읽기 전용 조회에만 쓰고, 조회에 필요한 컬럼만 선언한다.
썸네일·영상 URL은 DB에 없다(2026-08-12 ERD) — 출처는 exercise_catalog 캐시뿐.
"""
from sqlalchemy import BINARY, JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.orm import BackendBase


class Exercise(BackendBase):
    __tablename__ = "exercise"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    equipment_id: Mapped[bytes] = mapped_column(ForeignKey("equipment.id"))
    difficulty: Mapped[str] = mapped_column(String(20))     # ENUM 대문자 (BEGINNER …)
    exercise_type: Mapped[str] = mapped_column(String(20))  # ENUM 대문자 (WEIGHT_AND_REPS …)
    instructions: Mapped[list] = mapped_column(JSON)


class Equipment(BackendBase):
    __tablename__ = "equipment"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))


class Muscle(BackendBase):
    __tablename__ = "muscle"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))


class ExerciseMuscle(BackendBase):
    __tablename__ = "exercise_muscle"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    exercise_id: Mapped[bytes] = mapped_column(ForeignKey("exercise.id"))
    muscle_id: Mapped[bytes] = mapped_column(ForeignKey("muscle.id"))
    role: Mapped[str] = mapped_column(String(10))   # ENUM 대문자 (PRIMARY | SECONDARY)
