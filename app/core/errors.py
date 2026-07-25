"""도메인 예외 — service가 던지고 main.py 전역 핸들러가 {traceId, error}로 번역한다.

code는 01 규약 §4 카탈로그의 시맨틱 코드. HTTPException을 service에서 직접 쓰지 않는다(계층 규칙).
"""


class DomainError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    default_message = "서버 내부 오류가 발생했습니다."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message


class UnauthorizedError(DomainError):
    status_code = 401
    code = "UNAUTHORIZED"
    default_message = "인증에 실패했습니다."


class UserNotFoundError(DomainError):
    status_code = 404
    code = "USER_NOT_FOUND"
    default_message = "사용자를 찾을 수 없습니다."


class NoRoutineCandidateError(DomainError):
    status_code = 409
    code = "NO_ROUTINE_CANDIDATE"
    default_message = "조건을 만족하는 루틴을 구성할 수 없습니다."


class AiUnavailableError(DomainError):
    status_code = 503
    code = "AI_UNAVAILABLE"
    default_message = "AI 응답 생성에 실패했습니다. 잠시 후 다시 시도해주세요."
