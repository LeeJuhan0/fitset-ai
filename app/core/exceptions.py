from fastapi import HTTPException, status


class DomainError(HTTPException):
    """도메인 예외 공통 부모, detail 에 code message"""

    def __init__(
        self,
        message: str = "서버 내부 오류가 발생했습니다.",
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        code: str = "INTERNAL_ERROR",
    ):
        super().__init__(status_code=status_code, detail={"code": code, "message": message})

    @property
    def code(self) -> str:
        """실패 봉투 error.code"""
        return self.detail["code"]

    @property
    def message(self) -> str:
        """실패 봉투 error.message"""
        return self.detail["message"]


class AiUnavailableError(DomainError):
    """AI 응답 생성 실패, 503"""

    def __init__(self, message: str = "AI 응답 생성에 실패했습니다. 잠시 후 다시 시도해주세요."):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="AI_UNAVAILABLE",
            message=message,
        )


class RateLimitedError(DomainError):
    """유저별 요청 한도 초과, 429"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="RATE_LIMITED",
            message="요청이 너무 잦습니다. 잠시 후 다시 시도해주세요.",
        )
