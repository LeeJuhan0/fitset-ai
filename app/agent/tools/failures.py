"""툴 실패 → LLM 안내 지시 — 서버 장애가 유저 탓으로 번역되지 않게 한다.

내부 API 장애 사유를 그대로 돌려주면 LLM이 "계정 연결 문제, 다시 로그인해보라"처럼
유저가 고칠 수 없는 안내를 창작한다(prod 실측). 서버측 실패에는 안내 방법까지 지시한다.
"""
from app.core.errors import DomainError, UserNotFoundError

SERVER_FAULT_NOTE = (
    "서버 일시 장애로 데이터를 불러오지 못했다. 유저의 계정이나 로그인 문제가 아니다. "
    "계정 연결·재로그인 확인을 요구하지 말고, 잠시 후 다시 시도해달라고만 짧게 안내한다."
)


def is_server_fault(exc: DomainError) -> bool:
    """유저가 대화로 해소할 수 없는 실패인가 — 5xx 내부 장애와 백엔드 유저 부재."""
    return exc.status_code >= 500 or isinstance(exc, UserNotFoundError)
