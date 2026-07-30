"""공유 응답 스키마 — 팀 API 설계 규약(01)의 traceId + data/error 형식. core 레이어.

필드는 snake_case, JSON 와이어는 camelCase(팀 규약) — alias로 양쪽 유지.
성공(단건): {traceId, data} / 성공(목록): {traceId, data: {items, page}} / 실패: {traceId, error}
실패 응답은 라우터가 만들지 않는다 — 전역 예외 핸들러(main.py)가 조립한다.

각 도메인 schemas.py의 모델은 CamelModel을 상속하고, 라우터는
response_model=ApiResponse[모델] (목록은 ApiResponse[ListData[모델]]) 로 검증·문서화·필터링을 받는다.
"""
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel   # snake_case → camelCase 자동 alias

T = TypeVar("T")   # data 또는 items에 담기는 타입


class CamelModel(BaseModel):
    # populate_by_name=True: 코드에서는 snake_case로도, 와이어 dict의 camelCase로도 채울 수 있다.
    # FastAPI가 응답을 직렬화할 때는 alias(camelCase)로 내보낸다(response_model_by_alias 기본값).
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ApiResponse(CamelModel, Generic[T]):
    # 단건 성공 응답: {traceId, data}. trace_id는 미들웨어가 채운 값을 deps.get_trace_id로 주입받는다.
    trace_id: str
    data: T


class Page(CamelModel):
    # 커서 기반 페이지 정보. 커서 미사용 엔드포인트는 기본값(hasNext=false) 그대로 내보낸다.
    has_next: bool = False
    next_cursor: str | None = None


class ListData(CamelModel, Generic[T]):
    # 목록 성공 응답의 data 부분: {items, page}. ApiResponse[ListData[모델]]로 조합한다.
    items: list[T]
    page: Page = Field(default_factory=Page)


class ItemsData(CamelModel, Generic[T]):
    # page 없는 목록 응답의 data 부분: {items}. 커서를 쓰지 않는 엔드포인트 전용.
    # 채팅 API(클라이언트 API 명세 §공통)는 스레드 최대 5개·메시지 전체 로드라 page 객체를
    # 아예 내보내지 않기로 확정 — ListData를 쓰면 기본값 page가 딸려 나가 계약과 어긋난다.
    items: list[T]


class ErrorDetail(CamelModel):
    # 필드 단위 검증 오류(규약 §3.4). value는 클라가 보낸 원본 값이라 어떤 JSON 타입이든 올 수 있다.
    field: str
    value: Any = None
    reason: str


class ErrorBody(CamelModel):
    # 실패 응답의 error 부분. code는 01 규약 §4 카탈로그의 시맨틱 코드(예: USER_NOT_FOUND).
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(CamelModel):
    # 실패 응답 전체 형태: {traceId, error}. OpenAPI 문서화(responses=...)용 — 실제 조립은 main.py 핸들러.
    trace_id: str
    error: ErrorBody
