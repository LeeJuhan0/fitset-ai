import logging

import jwt

from app.auth import repository
from app.auth.exceptions import UnauthorizedError

logger = logging.getLogger("fitset")


def verify_token(token: str) -> str:
    """액세스 토큰 RS256 검증 후 유저 아이디 반환"""
    try:
        payload = jwt.decode(
            token,
            repository.signing_key(),
            algorithms=["RS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        logger.info("token rejected: %s (%s)", type(exc).__name__, exc)
        raise UnauthorizedError("유효하지 않은 토큰입니다.") from exc
    return payload["sub"]
