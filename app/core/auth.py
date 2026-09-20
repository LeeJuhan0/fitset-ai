"""JWT RS256 검증 — 비즈니스 규칙 §9 인증 규약.

ELB는 TLS 종료만 하므로 이 서버가 직접 검증한다:
SSM 공개키 → ① RS256 서명 → ② exp → ③ sub = userId.
type 클레임 검사는 없다 — 백엔드 refresh 토큰은 JWT가 아닌 불투명 랜덤 문자열이라
(fitset-api RefreshTokenGenerator, 2026-08-05 확인) 서명 검증 자체를 통과할 수 없다.

공개키는 SSM /fitset/{env}/jwt/public-key 에서 읽는다(2026-09-21, 구 JWKS 방식 폐기).
JWKS는 정적 파일이라 런타임 서명키와 어긋났다 — dev가 서명한 토큰을 dev가 게시한
JWKS로 검증할 수 없었다(fitset-api #176). 환경별 파라미터를 직접 읽으면 어긋날 자리가 없다.

개인키는 백엔드만 보유한다. 여기는 공개키만 읽으므로 토큰을 발급할 수 없다.
파라미터 타입도 String이라 KMS 권한이 필요 없다 — 필요한 것은 ssm:GetParameter 하나다.

키 로테이션 시에는 이 서버를 재기동해야 한다(공개키를 프로세스 수명 동안 캐시한다).
"""
import logging
from functools import lru_cache

import boto3
import jwt
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings
from app.core.exceptions import DomainError, UnauthorizedError

logger = logging.getLogger("fitset")


@lru_cache
def _public_key() -> str:
    """백엔드 공개키 PEM. 프로세스 수명 동안 한 번만 읽는다."""
    settings = get_settings()
    client = boto3.client("ssm", region_name=settings.aws_region)
    response = client.get_parameter(Name=settings.jwt_public_key_param)
    return response["Parameter"]["Value"]


def _signing_key() -> str:
    """검증에 쓸 공개키. PEM 주입(로컬 개발)이 우선, 없으면 SSM에서 읽는다."""
    pem = get_settings().jwt_public_key_pem
    if pem:
        return pem
    try:
        return _public_key()
    except (BotoCoreError, ClientError) as exc:
        # SSM 장애·권한 누락 — 토큰 문제가 아니라 서버 환경 문제라 500으로 구분
        logger.error("공개키를 SSM에서 읽지 못했다: %s", exc)
        raise DomainError("인증 키를 불러올 수 없습니다.") from exc


def verify_token(token: str) -> str:
    """access 토큰을 검증하고 userId(sub)를 반환한다. 실패는 전부 401 UNAUTHORIZED."""
    try:
        payload = jwt.decode(
            token,
            _signing_key(),
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
