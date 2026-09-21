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
    """SSM 파라미터에서 백엔드 공개키 PEM 1회 조회"""
    settings = get_settings()
    client = boto3.client("ssm", region_name=settings.aws_region)
    response = client.get_parameter(Name=settings.jwt_public_key_param)
    return response["Parameter"]["Value"]


def _signing_key() -> str:
    """검증용 공개키 선택 PEM 주입 우선 아니면 SSM"""
    pem = get_settings().jwt_public_key_pem
    if pem:
        return pem
    try:
        return _public_key()
    except (BotoCoreError, ClientError) as exc:
        logger.error("공개키를 SSM에서 읽지 못했다: %s", exc)
        raise DomainError("인증 키를 불러올 수 없습니다.") from exc


def verify_token(token: str) -> str:
    """액세스 토큰 RS256 검증 후 유저 아이디 반환"""
    try:
        payload = jwt.decode(
            token,
            _signing_key(),
            algorithms=["RS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        logger.info("token rejected: %s (%s)", type(exc).__name__, exc)
        raise UnauthorizedError("유효하지 않은 토큰입니다.") from exc
    return payload["sub"]
