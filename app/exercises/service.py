"""운동 도메인 서비스 — 가이드 영상 presigned URL 발급·재발급 (클라이언트 API 명세 §6-B).

챗봇 payload에 담기는 URL과 재발급 API가 같은 함수를 쓴다 — 응답 구조가 동일해야
클라가 payload 자리에 그대로 갈아끼울 수 있기 때문이다.
"""
import asyncio
import logging
from datetime import timedelta

from app.agent import guardrails
from app.clients import s3
from app.core import clock
from app.clients.spring import get_spring_client
from app.core.errors import DomainError, ExerciseNotFoundError, VideoNotFoundError

logger = logging.getLogger("fitset")


async def get_video(slug: str) -> dict:
    """종목 가이드 영상의 presigned URL과 만료 시각을 돌려준다."""
    name = guardrails.exercise_name(slug)
    if name is None:
        raise ExerciseNotFoundError()

    key = await _resolve_key(slug)
    if key is None:
        raise VideoNotFoundError()

    url, expires_in = await asyncio.to_thread(s3.presign_video, key)
    expires_at = clock.now_utc() + timedelta(seconds=expires_in)
    return {
        "slug": slug,
        "exerciseName": name,
        "videoUrl": url,
        "expiresAt": clock.iso_utc(expires_at),
    }


async def _resolve_key(slug: str) -> str | None:
    """영상 S3 키 — 내부 API의 videoKey가 정본, 실패·미등록 시 키 규약으로 폴백한다."""
    try:
        detail = await get_spring_client().get_exercise(slug)
    except DomainError:
        # 마스터 조회가 죽어도 키 규약이 맞으면 재생은 된다 — 서명 실패는 아래에서 드러난다
        logger.warning("exercise lookup failed, falling back to key template: %s", slug)
        return s3.video_key_for(slug)
    return detail.get("videoKey") or s3.video_key_for(slug)
