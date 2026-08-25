"""운동 마스터 엔티티 — 백엔드 MySQL 스키마 사본 (docs/백엔드 ERD.md와 1:1).

주인은 백엔드(Spring·JPA)다 — 읽기 전용 조회에만 쓰고, 조회에 필요한 컬럼만 선언한다.
썸네일·영상 URL은 DB에 키(thumbnail_key·video_key)로만 있다 — CDN 조립은 카탈로그 캐시 몫.
2026-08-25 백엔드 리팩토링 반영: slug 컬럼 삭제·미디어 키로 대체(#96). 키 파일명이
기존 slug와 동일함을 라이브 API로 확인해(thumbnails/{slug}.webp), slug는 키에서
유도하는 계산 컬럼으로 유지한다 — 206행 테이블이라 인덱스 미사용 필터도 무방하다.
"""
from sqlalchemy import BINARY, JSON, ForeignKey, String, func
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.core.orm import BackendBase


def _key_slug(key, folder: str):
    """미디어 키(예: thumbnails/barbell-squat.webp) → 폴더·확장자를 뗀 slug.

    replace만 쓴다 — substring_index는 MySQL 전용이라 SQLite 테스트에서 못 돈다.
    """
    return func.replace(func.replace(key, f"{folder}/", ""), ".webp", "")


class Exercise(BackendBase):
    __tablename__ = "exercise"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    thumbnail_key: Mapped[str] = mapped_column(String(255), unique=True)
    video_key: Mapped[str] = mapped_column(String(255))
    equipment_id: Mapped[bytes] = mapped_column(ForeignKey("equipment.id"))
    difficulty: Mapped[str] = mapped_column(String(20))     # ENUM 대문자 (BEGINNER …)
    exercise_type: Mapped[str] = mapped_column(String(20))  # ENUM 대문자 (WEIGHT_AND_REPS …)
    instructions: Mapped[list] = mapped_column(JSON)
    slug: Mapped[str] = column_property(_key_slug(thumbnail_key, "thumbnails"))


class Equipment(BackendBase):
    __tablename__ = "equipment"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    thumbnail_key: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    # 키 파일명은 kebab-case(cable-machine) — EQUIP enum key(cableMachine) 변환은 repository 몫
    slug: Mapped[str] = column_property(_key_slug(thumbnail_key, "equipments"))


class Muscle(BackendBase):
    __tablename__ = "muscle"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    thumbnail_key: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = column_property(_key_slug(thumbnail_key, "muscles"))


class ExerciseMuscle(BackendBase):
    __tablename__ = "exercise_muscle"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    exercise_id: Mapped[bytes] = mapped_column(ForeignKey("exercise.id"))
    muscle_id: Mapped[bytes] = mapped_column(ForeignKey("muscle.id"))
    role: Mapped[str] = mapped_column(String(10))   # ENUM 대문자 (PRIMARY | SECONDARY)
