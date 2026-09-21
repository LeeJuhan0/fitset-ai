from app.core.exceptions import DomainError


class UserNotFoundError(DomainError):
    status_code = 404
    code = "USER_NOT_FOUND"
    default_message = "사용자를 찾을 수 없습니다."
