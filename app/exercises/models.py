from sqlalchemy import BINARY, JSON, ForeignKey, String, func
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.core.orm import BackendBase


def _key_slug(key, folder: str):
    """미디어 키에서 폴더와 확장자 제거한 slug 추출"""
    return func.replace(func.replace(key, f"{folder}/", ""), ".webp", "")


class Exercise(BackendBase):
    __tablename__ = "exercise"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    thumbnail_key: Mapped[str] = mapped_column(String(255), unique=True)
    video_key: Mapped[str] = mapped_column(String(255))
    equipment_id: Mapped[bytes] = mapped_column(ForeignKey("equipment.id"))
    difficulty: Mapped[str] = mapped_column(String(20))
    exercise_type: Mapped[str] = mapped_column(String(20))
    instructions: Mapped[list] = mapped_column(JSON)
    slug: Mapped[str] = column_property(_key_slug(thumbnail_key, "thumbnails"))


class Equipment(BackendBase):
    __tablename__ = "equipment"

    id: Mapped[bytes] = mapped_column(BINARY(16), primary_key=True)
    thumbnail_key: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
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
    role: Mapped[str] = mapped_column(String(10))
