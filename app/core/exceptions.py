class DomainError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    default_message = "서버 내부 오류가 발생했습니다."

    def __init__(self, message: str | None = None):
        """예외 메시지 미지정 시 기본 메시지 적용"""
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
