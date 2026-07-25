"""JWT RS256 검증 — 비즈니스 규칙 §9 인증 규약.

ELB는 TLS 종료만 하므로 이 서버가 직접 검증한다:
SSM 공개키 로드 → ① RS256 서명 → ② exp → ③ type == "access" → ④ sub = userId.
개인키는 백엔드(Spring)만 보유 — 여기는 공개키 검증 전용.
"""
from functools import lru_cache

import boto3
import jwt

from app.core.config import get_settings
from app.core.errors import DomainError, UnauthorizedError


@lru_cache
def get_public_key() -> str:
    """검증용 공개키 PEM. 환경변수 주입(로컬 개발)이 우선, 없으면 SSM에서 로드해 캐시한다."""
    settings = get_settings()
    if settings.jwt_public_key_pem:
        return settings.jwt_public_key_pem
    ssm = boto3.client("ssm", region_name=settings.aws_region)
    response = ssm.get_parameter(Name=settings.ssm_jwt_public_key_name)
    return response["Parameter"]["Value"]


def verify_token(token: str) -> str:
    """access 토큰을 검증하고 userId(sub)를 반환한다. 실패는 전부 401 UNAUTHORIZED."""
    try:
        public_key = get_public_key()
    except Exception as exc:   # SSM 파라미터 부재·권한 등 — 서버 설정 문제라 500으로 구분
        raise DomainError("인증 키를 불러올 수 없습니다.") from exc
    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("유효하지 않은 토큰입니다.") from exc
    if payload.get("type") != "access":
        raise UnauthorizedError("access 토큰이 아닙니다.")
    return payload["sub"]
