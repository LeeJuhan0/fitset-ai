from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class ResponseScheme(StrEnum):
    TEXT = "text"
    CHART = "chart"
    EXERCISE_GIF = "exerciseGif"
    ROUTINE = "routine"


PAYLOAD_SCHEMES = frozenset({ResponseScheme.CHART, ResponseScheme.EXERCISE_GIF, ResponseScheme.ROUTINE})
DEFAULT_SCHEME = ResponseScheme.TEXT


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ApiResponse(CamelModel, Generic[T]):
    trace_id: str
    data: T


class Page(CamelModel):
    has_next: bool = False
    next_cursor: str | None = None


class ListData(CamelModel, Generic[T]):
    items: list[T]
    page: Page = Field(default_factory=Page)


class ErrorDetail(CamelModel):
    field: str
    value: Any = None
    reason: str


class ErrorBody(CamelModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(CamelModel):
    trace_id: str
    error: ErrorBody
