from fastapi import status

from app.core.exceptions import DomainError


class UserNotFoundError(DomainError):
    """사용자 프로필 없음, 404"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code="USER_NOT_FOUND",
            message="사용자를 찾을 수 없습니다.",
        )
