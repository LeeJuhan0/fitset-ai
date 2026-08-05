"""루틴 도메인 요청/응답 모델 — 클라이언트 API 명세 §1 (POST /ai/v1/routines).

내부 snake_case ↔ 와이어 camelCase는 CamelModel alias가 처리한다. 로직·I/O 금지.
"""
from typing import Literal

from pydantic import Field

from app.core.schemas import CamelModel

Level = Literal["beginner", "intermediate", "advanced"]
Muscle = Literal[
    "back", "biceps", "calves", "chest", "core", "forearms",
    "glutes", "hamstrings", "quadriceps", "shoulders", "trapezius", "triceps",
]


class RoutineGenerateRequest(CamelModel):
    # goal은 요청 필드가 아니다 — 내부 API 프로필 조회값을 쓴다 (2026-07-29 확정, 명세 §1)
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
    # 백엔드 마스터 UUID — 클라 저장(POST /api/v1/routines)·종목 상세 이동 키.
    # 카탈로그 캐시 미스면 null (클라가 자체 종목 마스터로 slug 매핑 폴백)
    exercise_id: str | None
    slug: str              # AI 서버 내부 식별자 — 가이드·영상 재발급 호출 키
    exercise_name: str     # 한글명 (종목 마스터 name_ko)
    thumbnail_url: str     # CDN URL (카탈로그) — 미스 시 루틴 적재값
    order_index: int
    sets: list[RoutineSetOut]


class RoutineOut(CamelModel):
    slug: str | None       # 베이스 루틴 참조 (routines 테이블 slug)
    name: str
    estimated_minutes: int
    exercises: list[RoutineExerciseOut]


class RoutineData(CamelModel):
    routine: RoutineOut
