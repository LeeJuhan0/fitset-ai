"""JWT RS256 검증 — 비즈니스 규칙 §9 인증 규약 + AI 서버 JWKS 검증 가이드(2026-08-04).

ELB는 TLS 종료만 하므로 이 서버가 직접 검증한다:
JWKS 공개키(kid 매칭) → ① RS256 서명 → ② exp → ③ sub = userId.
type 클레임 검사는 없다 — 백엔드 refresh 토큰은 JWT가 아닌 불투명 랜덤 문자열이라
(fitset-api RefreshTokenGenerator, 2026-08-05 확인) 서명 검증 자체를 통과할 수 없다.
개인키는 백엔드만 보유 — 여기는 공개키 검증 전용이라 SSM·KMS 권한이 필요 없다.

키 로테이션은 무설정 대응 — 캐시에 없는 kid가 오면 PyJWKClient가 JWKS를 재조회한다.
로컬 개발은 JWT_PUBLIC_KEY_PEM 주입으로 JWKS를 우회한다(로컬 백엔드 kid=ephemeral).
"""
import logging
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from app.core.config import get_settings
from app.core.errors import DomainError, UnauthorizedError

logger = logging.getLogger("fitset")

# 콜드 캐시 때만 도는 네트워크 구간 — 검증이 threadpool에서 돌므로 스레드 점유 상한을 건다
_JWKS_TIMEOUT_SECONDS = 5


@lru_cache
def _jwks_client() -> PyJWKClient:
    """JWKS 클라이언트 싱글턴 — 서명키를 캐싱하고 미지의 kid만 재조회한다."""
    return PyJWKClient(get_settings().jwks_url, cache_keys=True, timeout=_JWKS_TIMEOUT_SECONDS)


def _signing_key(token: str):
    """토큰 헤더의 kid에 대응하는 공개키. PEM 주입(로컬 개발)이 우선, 없으면 JWKS 조회."""
    pem = get_settings().jwt_public_key_pem
    if pem:
        return pem
    try:
        return _jwks_client().get_signing_key_from_jwt(token).key
    except PyJWKClientConnectionError as exc:
        # JWKS 엔드포인트 장애 — 토큰 문제가 아니라 서버 환경 문제라 500으로 구분
        raise DomainError("인증 키를 불러올 수 없습니다.") from exc
    except PyJWKClientError as exc:
        # JWKS에 없는 kid 등 — 우리 키로 서명되지 않은 토큰이다
        logger.info("token rejected: unknown kid (%s)", exc)
        raise UnauthorizedError("유효하지 않은 토큰입니다.") from exc


def verify_token(token: str) -> str:
    """access 토큰을 검증하고 userId(sub)를 반환한다. 실패는 전부 401 UNAUTHORIZED."""
    try:
        payload = jwt.decode(
            token,
            _signing_key(token),
            algorithms=["RS256"],
            # 토큰에 aud/iss 클레임이 없으므로(검증 가이드 §4) 서명·exp·sub만 본다
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        # 401 응답에는 이유를 숨기고(공격자에게 힌트) 로그에만 남긴다 —
        # "prod 전체 401" 같은 상황에서 만료·서명·클레임 문제를 로그로 구분하기 위함
        # (2026-08-04 type 클레임 사고 때 로그만으로 원인 특정이 불가했다)
        logger.info("token rejected: %s (%s)", type(exc).__name__, exc)
        raise UnauthorizedError("유효하지 않은 토큰입니다.") from exc
    return payload["sub"]
