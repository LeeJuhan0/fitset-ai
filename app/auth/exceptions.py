from fastapi import status

from app.core.exceptions import DomainError


class UnauthorizedError(DomainError):
    """토큰 누락 또는 검증 실패, 401"""

    def __init__(self, message: str = "인증에 실패했습니다."):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message=message,
        )
