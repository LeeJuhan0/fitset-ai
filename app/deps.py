"""공통 의존성 주입 — 게이트웨이 헤더·미들웨어 산출물을 라우터에 전달한다."""
from fastapi import Request


def get_trace_id(request: Request) -> str:
    """trace_id_middleware(main.py)가 request.state에 넣은 traceId를 꺼낸다.

    라우터에서 Depends(deps.get_trace_id)로 받아 성공 응답 ApiResponse(trace_id=..., data=...)에 넣는다.
    """
    return request.state.trace_id
