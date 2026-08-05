"""운동 도메인 서비스 — 가이드 영상 재생 URL 발급 (클라이언트 API 명세 §6-B).

챗봇 payload에 담기는 URL과 재발급 API가 같은 함수를 쓴다 — 응답 구조가 동일해야
클라가 payload 자리에 그대로 갈아끼울 수 있기 때문이다.

URL 출처는 2단계다 (2026-08-05 개정):
  1순위 CDN — 카탈로그 캐시의 CloudFront URL. 무서명·무기한이라 expiresAt이 null이고
              저장된 대화를 다시 열어도 그대로 재생된다(재발급 왕복 자체가 사라진다).
  2순위 presign — 카탈로그 미스이거나 클라가 재생 실패로 재발급을 요청할 때(fallback=true).
              비공개 버킷 직접 서명이라 1시간 만료가 붙는다.

CDN이 정상인 한 presign 경로는 돌지 않지만, CDN 장애·오브젝트 누락 시 클라가
fallback으로 되물어 복구할 수 있도록 남겨둔다.
"""
import asyncio
import logging
from datetime import timedelta

from app.agent import guardrails
from app.clients import s3
from app.clients.spring import get_spring_client
from app.core import clock
from app.core.errors import DomainError, ExerciseNotFoundError, VideoNotFoundError
from app.exercises import repository as exercise_catalog

logger = logging.getLogger("fitset")


async def get_video(slug: str, fallback: bool = False) -> dict:
    """가이드 영상 재생 URL. 기본은 CDN, fallback=True거나 CDN 미보유면 presigned URL."""
    name = guardrails.exercise_name(slug)
    if name is None:
        raise ExerciseNotFoundError()

    cdn_url = None if fallback else exercise_catalog.cdn_video_url(slug)
    if cdn_url:
        video_url, expires_at = cdn_url, None    # CloudFront 공개 배포 — 만료 없음
    else:
        video_url, expires_at = await _presigned(slug)

    return {
        "exerciseId": exercise_catalog.exercise_id(slug),
        "slug": slug,
        "exerciseName": name,
        "videoUrl": video_url,
        "expiresAt": expires_at,
    }


async def _presigned(slug: str) -> tuple[str, str]:
    """폴백 경로 — 비공개 버킷 presigned GET URL과 만료 시각(ISO Z)."""
    key = await _resolve_key(slug)
    if key is None:
        raise VideoNotFoundError()
    url, expires_in = await asyncio.to_thread(s3.presign_video, key)
    return url, clock.iso_utc(clock.now_utc() + timedelta(seconds=expires_in))


async def _resolve_key(slug: str) -> str | None:
    """영상 S3 키 — 내부 API의 videoKey가 정본, 실패·미등록 시 키 규약으로 폴백한다."""
    try:
        detail = await get_spring_client().get_exercise(slug)
    except DomainError:
        # 마스터 조회가 죽어도 키 규약이 맞으면 재생은 된다 — 서명 실패는 아래에서 드러난다
        logger.warning("exercise lookup failed, falling back to key template: %s", slug)
        return s3.video_key_for(slug)
    return detail.get("videoKey") or s3.video_key_for(slug)
