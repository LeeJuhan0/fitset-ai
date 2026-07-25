"""루틴 도메인 요청/응답 모델 — 클라이언트 API 명세 §1 (POST /v1/routines).

내부 snake_case ↔ 와이어 camelCase는 CamelModel alias가 처리한다. 로직·I/O 금지.
"""
from typing import Literal

from pydantic import Field

from app.core.schemas import CamelModel

Goal = Literal["hypertrophy", "strength", "weightLoss", "endurance"]
Level = Literal["beginner", "intermediate", "advanced"]
Muscle = Literal[
    "back", "biceps", "calves", "chest", "core", "forearms",
    "glutes", "hamstrings", "quadriceps", "shoulders", "trapezius", "triceps",
]


class RoutineGenerateRequest(CamelModel):
    goal: Goal
    level: Level
    muscle_groups: list[Muscle] = Field(min_length=1, max_length=12)
    minutes: int = Field(ge=10, le=180)
    context: str | None = Field(default=None, max_length=200)
    include_warmup: bool


class RoutineSetOut(CamelModel):
    order_index: int
    weight: float | None   # kg — 맨몸 운동은 null
    reps: int


class RoutineExerciseOut(CamelModel):
    slug: str              # 종목 식별자 — slug (uuid 미사용)
    exercise_name: str     # 한글명 (종목 마스터 name_ko)
    thumbnail_url: str
    order_index: int
    sets: list[RoutineSetOut]


class RoutineOut(CamelModel):
    slug: str | None       # 베이스 루틴 참조 (routines 테이블 slug)
    name: str
    estimated_minutes: int
    exercises: list[RoutineExerciseOut]


class RoutineData(CamelModel):
    routine: RoutineOut
