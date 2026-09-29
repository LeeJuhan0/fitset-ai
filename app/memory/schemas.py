from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_ITEMS = 5
EMPTY_WORDS = {"", "null", "none", "unknown", "없음", "모름", "싫음", "싫어", "싫어함", "싫다", "싶음"}
LEVEL_WORDS = {"초보", "초보자", "초급", "초급자", "중급", "중급자", "고급", "고급자", "상급", "상급자"}


class AvoidedExercise(BaseModel):
    """유저가 싫다고 한 종목과 이유"""

    name: str = Field(description="한국어 종목명")
    reason: str | None = Field(default=None, description="유저가 말한 이유. 이유 없이 싫다고만 했으면 null")

    @field_validator("reason", mode="before")
    @classmethod
    def _blank_reason(cls, value):
        """null 없음 같은 글자는 빈 값"""
        return _blank(value)


class RejectedExercise(BaseModel):
    """추천에서 빼달라고 한 종목과 이유"""

    name: str = Field(description="한국어 종목명")
    reason: str = Field(description="유저가 말한 거절 이유")


class Environment(BaseModel):
    """운동 장소와 쓸 수 있는 장비"""

    place: Literal["gym", "home", "outdoor", "mixed", "unknown"] | None = Field(default=None, description="유저가 직접 말한 주 운동 장소. 종목이나 기구 이름으로 추측하지 않고, 말하지 않았으면 unknown")
    equipment: list[str] = Field(default_factory=list, max_length=MAX_ITEMS, description="유저가 말한 쓸 수 있는 장비와 한계, 예: 덤벨 20kg까지")

    @field_validator("place", mode="before")
    @classmethod
    def _blank_place(cls, value):
        """null 없음 같은 글자는 빈 값"""
        return _blank(value)


class UserPreferences(BaseModel):
    """대화에서 드러난 유저 운동 편향 구조"""

    preferred_exercises: list[str] = Field(default_factory=list, max_length=MAX_ITEMS, description="좋아하거나 자주 찾는 종목")
    avoided_exercises: list[AvoidedExercise] = Field(default_factory=list, max_length=MAX_ITEMS, description="유저가 평소 싫다고 말한 종목. 추천받은 루틴과 상관없는 호불호")
    rejected_exercises: list[RejectedExercise] = Field(default_factory=list, max_length=MAX_ITEMS, description="추천받은 루틴을 보고 빼달라거나 바꿔달라고 한 종목")
    environment: Environment = Field(default_factory=Environment, description="운동 장소와 장비")
    training_style: str | None = Field(default=None, description="선호하는 방식, 예: 고중량 저반복, 펌핑 위주, 서킷")
    specific_goals: list[str] = Field(default_factory=list, max_length=MAX_ITEMS, description="구체적인 목표, 예: 벤치프레스 100kg")

    @field_validator("training_style", mode="before")
    @classmethod
    def _blank_style(cls, value):
        """빈 값 글자와 수준 단어는 빈 값"""
        if isinstance(value, str) and value.strip() in LEVEL_WORDS:
            return None
        return _blank(value)


def _blank(value):
    """LLM 이 글자로 쓴 빈 값을 None 으로"""
    if isinstance(value, str) and value.strip().lower() in EMPTY_WORDS:
        return None
    return value
