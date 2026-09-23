from fastapi import status

from app.core.exceptions import DomainError


class ThreadNotFoundError(DomainError):
    """대화 스레드 없음, 404"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            code="THREAD_NOT_FOUND",
            message="대화 스레드를 찾을 수 없습니다.",
        )


class ThreadForbiddenError(DomainError):
    """타 유저 스레드 접근, 403"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            code="THREAD_FORBIDDEN",
            message="접근할 수 없는 대화 스레드입니다.",
        )


class ThreadExpiredError(DomainError):
    """보관 기한 지난 스레드, 409"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            code="THREAD_EXPIRED",
            message="보관 기한이 지난 대화입니다. 스레드를 삭제한 후 새로 시작해주세요.",
        )


class ThreadQuotaExceededError(DomainError):
    """유저 스레드 개수 상한 도달, 409"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            code="THREAD_QUOTA_EXCEEDED",
            message="대화 스레드가 최대 개수에 도달했습니다. 기존 스레드를 삭제한 후 다시 시도해주세요.",
        )


class ThreadFullError(DomainError):
    """스레드 메시지 수 상한 도달, 409"""

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            code="THREAD_FULL",
            message="대화가 너무 길어 더 보낼 수 없습니다. 새 스레드에서 계속해주세요.",
        )
