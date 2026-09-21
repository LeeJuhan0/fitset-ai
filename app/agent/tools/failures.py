from app.core.exceptions import DomainError
from app.users.exceptions import UserNotFoundError

SERVER_FAULT_NOTE = (
    "서버 일시 장애로 데이터를 불러오지 못했다. 유저의 계정이나 로그인 문제가 아니다. "
    "계정 연결·재로그인 확인을 요구하지 말고, 잠시 후 다시 시도해달라고만 짧게 안내한다."
)


def is_server_fault(exc: DomainError) -> bool:
    """대화로 해소 불가한 서버 장애 여부 판정"""
    return exc.status_code >= 500 or isinstance(exc, UserNotFoundError)
