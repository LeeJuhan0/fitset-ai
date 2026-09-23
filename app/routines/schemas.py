from pydantic import Field

from app.core.schemas import CamelModel
from app.routines.enums import Level, MuscleGroup


class RoutineGenerateRequest(CamelModel):
    level: Level
    muscle_groups: list[MuscleGroup] = Field(min_length=1, max_length=12)
    minutes: int = Field(ge=10, le=180)
    context: str | None = Field(default=None, max_length=200)
    include_warmup: bool


class RoutineSetOut(CamelModel):
    order_index: int
    weight: float
    reps: int
    duration_seconds: int


class RoutineExerciseOut(CamelModel):
    exercise_id: str | None
    slug: str
    exercise_name: str
    thumbnail_url: str
    order_index: int
    sets: list[RoutineSetOut]


class RoutineOut(CamelModel):
    slug: str | None
    name: str
    estimated_minutes: int
    exercises: list[RoutineExerciseOut]


class RoutineData(CamelModel):
    routine: RoutineOut
