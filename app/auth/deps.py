from fastapi import Request

from app.auth import service
from app.auth.exceptions import UnauthorizedError


def get_current_user_id(request: Request) -> str:
    """Bearer 토큰 검증 후 유저 아이디 반환"""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise UnauthorizedError("Authorization 헤더가 없습니다.")
    return service.verify_token(header.removeprefix("Bearer ").strip())
