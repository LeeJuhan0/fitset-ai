from fastapi import Request

from app.core import auth
from app.core.exceptions import UnauthorizedError


def get_trace_id(request: Request) -> str:
    """미들웨어가 요청 상태에 넣은 traceId 반환"""
    return request.state.trace_id


def get_current_user_id(request: Request) -> str:
    """Bearer 토큰 검증 후 유저 아이디 반환"""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise UnauthorizedError("Authorization 헤더가 없습니다.")
    return auth.verify_token(header.removeprefix("Bearer ").strip())
