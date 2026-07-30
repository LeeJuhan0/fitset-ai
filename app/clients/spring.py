"""스프링 백엔드 내부 API 클라이언트 — docs/백엔드 내부 API 명세.md.

외부 경계는 여기서만. 응답 envelope {traceId, data}에서 data만 벗겨 반환한다.
스프링 DB 직접 접근 금지(계층 규칙) — 사용자 데이터는 반드시 이 경로.
"""
from datetime import date, timedelta
from functools import lru_cache

import httpx

from app.core.config import get_settings
from app.core.errors import DomainError, ExerciseNotFoundError, UserNotFoundError

# 404 응답의 error.code → 도메인 예외 — 종목 404가 USER_NOT_FOUND로 새지 않게 구분한다
_NOT_FOUND_BY_CODE = {
    "USER_NOT_FOUND": UserNotFoundError,
    "EXERCISE_NOT_FOUND": ExerciseNotFoundError,
}


def _window(days: int) -> dict:
    """시계열 API 공통 쿼리 (§4-B) — 최근 N일을 from/to 날짜 구간으로 바꾼다."""
    today = date.today()
    return {"from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat()}


def _not_found_error(response: httpx.Response) -> DomainError:
    """404 본문의 error.code로 예외를 고른다. 본문이 못 읽히면 USER_NOT_FOUND로 폴백."""
    try:
        code = response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        code = None
    return _NOT_FOUND_BY_CODE.get(code, UserNotFoundError)()


class SpringInternalClient:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        # transport는 테스트의 MockTransport 주입용 — 운영은 기본 커넥션 풀
        self._client = httpx.AsyncClient(base_url=base_url, timeout=5.0, transport=transport)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise DomainError("내부 API 호출에 실패했습니다.") from exc
        if response.status_code == 404:
            raise _not_found_error(response)
        response.raise_for_status()
        return response.json()["data"]

    async def get_profile(self, user_id: str) -> dict:
        """유저 프로필 (§4.1) — heightCm·weightKg·gender·goal·level·avoidBodyParts."""
        return await self._get(f"/users/{user_id}/profile")

    async def get_recent_workouts(self, user_id: str, days: int) -> list[dict]:
        """최근 운동 기록 raw (§4.2) — 세션 목록(종목·세트 포함), 최신순."""
        data = await self._get(f"/users/{user_id}/workouts", params={"days": days})
        return data["items"]

    async def get_exercise(self, slug: str) -> dict:
        """종목 마스터 단건 (§4.3) — 썸네일 비교·검증, 챗봇 운동 가이드 근거(instructions)."""
        return await self._get(f"/exercises/{slug}")

    async def get_body_weights(self, user_id: str, days: int) -> list[dict]:
        """체중 측정 이력 (§4.4) — measuredAt 오름차순. 챗봇 체중·BMI 차트용."""
        data = await self._get(f"/users/{user_id}/body-weights", params=_window(days))
        return data["items"]

    async def get_exercise_sets(self, user_id: str, slug: str, days: int) -> list[dict]:
        """종목별 수행 세트 시계열 (§4.5) — 세션 중첩 없는 평탄 목록. 챗봇 PR 차트용."""
        data = await self._get(f"/users/{user_id}/exercises/{slug}/sets", params=_window(days))
        return data["items"]

    async def get_workout_sessions(self, user_id: str, days: int) -> list[dict]:
        """세션 요약 목록 (§4.6) — 종목·세트 없이 세션 메타만. 챗봇 시간·빈도 차트용."""
        data = await self._get(f"/users/{user_id}/workout-sessions", params=_window(days))
        return data["items"]


@lru_cache
def get_spring_client() -> SpringInternalClient:
    return SpringInternalClient(get_settings().spring_internal_base_url)
