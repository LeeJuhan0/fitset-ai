import logging

from app.core.schemas import DEFAULT_SCHEME, PAYLOAD_SCHEMES

logger = logging.getLogger("fitset")


def enforce_scheme_contract(
    scheme: str | None, payload: dict | None
) -> tuple[str, dict | None]:
    """응답 스킴 계약 검증 위반 시 텍스트 강등"""
    if scheme not in PAYLOAD_SCHEMES:
        if payload is not None:
            logger.warning("scheme %r carried a payload, dropping it", scheme)
        return DEFAULT_SCHEME, None
    if not payload:
        logger.info("scheme %s had no payload, downgrading to text", scheme)
        return DEFAULT_SCHEME, None
    return scheme, payload
