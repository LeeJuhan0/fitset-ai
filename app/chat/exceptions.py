from app.core.exceptions import DomainError


class ThreadNotFoundError(DomainError):
    status_code = 404
    code = "THREAD_NOT_FOUND"
    default_message = "대화 스레드를 찾을 수 없습니다."


class ThreadForbiddenError(DomainError):
    status_code = 403
    code = "THREAD_FORBIDDEN"
    default_message = "접근할 수 없는 대화 스레드입니다."


class ThreadExpiredError(DomainError):
    status_code = 409
    code = "THREAD_EXPIRED"
    default_message = "보관 기한이 지난 대화입니다. 스레드를 삭제한 후 새로 시작해주세요."


class ThreadQuotaExceededError(DomainError):
    status_code = 409
    code = "THREAD_QUOTA_EXCEEDED"
    default_message = "대화 스레드가 최대 개수에 도달했습니다. 기존 스레드를 삭제한 후 다시 시도해주세요."


class ThreadFullError(DomainError):
    status_code = 409
    code = "THREAD_FULL"
    default_message = "대화가 너무 길어 더 보낼 수 없습니다. 새 스레드에서 계속해주세요."
