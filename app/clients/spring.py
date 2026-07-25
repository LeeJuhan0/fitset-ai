"""스프링 백엔드 내부 API 클라이언트 — docs/백엔드 내부 API 명세.md.

외부 경계는 여기서만. 응답 envelope {traceId, data}에서 data만 벗겨 반환한다.
스프링 DB 직접 접근 금지(계층 규칙) — 사용자 데이터는 반드시 이 경로.
"""
from functools import lru_cache

import httpx

from app.core.config import get_settings
from app.core.errors import DomainError, UserNotFoundError


class SpringInternalClient:
    def __init__(self, base_url: str):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=5.0)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise DomainError("내부 API 호출에 실패했습니다.") from exc
        if response.status_code == 404:
            raise UserNotFoundError()
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
        """종목 마스터 단건 (§4.3) — 썸네일 비교·검증용."""
        return await self._get(f"/exercises/{slug}")


@lru_cache
def get_spring_client() -> SpringInternalClient:
    return SpringInternalClient(get_settings().spring_internal_base_url)
