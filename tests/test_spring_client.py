"""스프링 클라이언트 오류 매핑 테스트 — CS 감사 F7.

404 응답의 error.code로 도메인 예외를 고른다 — 종목 404가 USER_NOT_FOUND로 새면
차트 툴이 LLM에 "사용자를 찾을 수 없습니다"를 전달하는 오분류가 난다.
"""
import httpx
import pytest

from app.clients.spring import SpringInternalClient
from app.core.errors import ExerciseNotFoundError, UserNotFoundError


def _client(status_code: int, body: dict) -> SpringInternalClient:
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code, json=body))
    return SpringInternalClient("http://spring-test/internal", transport=transport)


async def test_404_with_exercise_code_maps_to_exercise_not_found():
    client = _client(404, {"traceId": "t", "error": {"code": "EXERCISE_NOT_FOUND", "message": "없음"}})
    with pytest.raises(ExerciseNotFoundError):
        await client.get_exercise("no-such-slug")


async def test_404_with_user_code_maps_to_user_not_found():
    client = _client(404, {"traceId": "t", "error": {"code": "USER_NOT_FOUND", "message": "없음"}})
    with pytest.raises(UserNotFoundError):
        await client.get_profile("u1")


async def test_404_without_parsable_body_defaults_to_user_not_found():
    # 게이트웨이가 비JSON 404를 돌려줘도 파싱 오류 없이 기존 동작으로 폴백한다
    transport = httpx.MockTransport(lambda request: httpx.Response(404, text="gateway error"))
    client = SpringInternalClient("http://spring-test/internal", transport=transport)
    with pytest.raises(UserNotFoundError):
        await client.get_profile("u1")


async def test_success_unwraps_data_envelope():
    client = _client(200, {"traceId": "t", "data": {"level": "beginner"}})
    assert await client.get_profile("u1") == {"level": "beginner"}
