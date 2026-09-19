"""예외 베이스와 패키지 공통 예외 — service가 던지고 전역 핸들러가 {traceId, error}로 번역한다.

도메인별 예외는 각 패키지의 exceptions.py가 소유한다 (chat, routines, exercises, users).
여기는 베이스(DomainError)와 둘 이상의 패키지가 함께 쓰는 예외(인증, 레이트리밋, AI 장애)만 둔다.
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


class AiUnavailableError(DomainError):
    status_code = 503
    code = "AI_UNAVAILABLE"
    default_message = "AI 응답 생성에 실패했습니다. 잠시 후 다시 시도해주세요."


class RateLimitedError(DomainError):
    status_code = 429
    code = "RATE_LIMITED"
    default_message = "요청이 너무 잦습니다. 잠시 후 다시 시도해주세요."
