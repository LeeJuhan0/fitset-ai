"""S3 클라이언트 — 운동 가이드 영상 presigned GET 발급. 외부 경계는 여기서만.

영상은 비공개 버킷에 있고 재생 URL은 요청 시점에 서명한다(내부 API 명세 §4.3).
백엔드가 미리 서명해 주지 않는 이유: 서명은 1시간짜리인데 종목 마스터는 캐시·재사용되므로
전달되는 동안 이미 만료된다. 위치(키)는 백엔드, 서명은 사용 시점에 이 서버.

generate_presigned_url은 네트워크 호출이 아니라 로컬 서명이라 동기 그대로 써도 블로킹이 없다.
"""
from functools import lru_cache

import boto3
from botocore.config import Config

from app.core.config import get_settings


@lru_cache
def _client():
    settings = get_settings()
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        # SigV4 고정 + virtual 주소 강제 — 기본값이면 글로벌 엔드포인트 호스트에
        # 리전 서명이 붙어 S3가 400(AuthorizationQueryParametersError)을 낸다 (실측)
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def video_key_for(slug: str) -> str:
    """내부 API가 videoKey를 주지 않을 때 쓰는 키 규약 폴백 (협의 포인트 ⑫)."""
    return get_settings().exercise_video_key_template.format(slug=slug)


def presign_video(key: str) -> tuple[str, int]:
    """가이드 영상 presigned GET URL과 유효 초를 돌려준다."""
    settings = get_settings()
    expires = settings.presign_expires_seconds
    url = _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.exercise_video_bucket, "Key": key},
        ExpiresIn=expires,
    )
    return url, expires
