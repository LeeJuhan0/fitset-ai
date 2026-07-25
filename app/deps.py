"""공통 의존성 주입 — traceId·인증(userId)을 라우터에 전달한다."""
from fastapi import Request

from app.core import auth
from app.core.errors import UnauthorizedError


def get_trace_id(request: Request) -> str:
    """trace_id_middleware(main.py)가 request.state에 넣은 traceId를 꺼낸다.

    라우터에서 Depends(deps.get_trace_id)로 받아 성공 응답 ApiResponse(trace_id=..., data=...)에 넣는다.
    """
    return request.state.trace_id


def get_current_user_id(request: Request) -> str:
    """Authorization Bearer JWT를 검증하고 userId(sub)를 돌려준다 — 비즈니스 규칙 §9.

    검증 실패는 UnauthorizedError → main.py DomainError 핸들러가 401 {traceId, error}로 번역.
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise UnauthorizedError("Authorization 헤더가 없습니다.")
    return auth.verify_token(header.removeprefix("Bearer ").strip())
