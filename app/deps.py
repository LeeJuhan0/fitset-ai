from fastapi import Request


def get_trace_id(request: Request) -> str:
    """미들웨어가 요청 상태에 넣은 traceId 반환"""
    return request.state.trace_id
