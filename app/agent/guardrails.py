"""에이전트 가드레일 — LLM 출력이 §7 tagged union 계약을 벗어나지 않게 막는다.

responseScheme이 payload의 존재·구조를 결정한다 — text인데 payload가 있거나
chart인데 없으면 서버 버그이므로, 나가기 전에 여기서 정리한다.
프롬프트는 뚫릴 수 있다는 전제로 설계했다 — 최종 방어선은 프롬프트가 아니라 이 모듈이다.

어휘(ResponseScheme)는 core/schemas가 정본이고, 종목 slug 해석·검증은
exercises/repository(종목 마스터의 주인)가 맡는다 — 여기는 계약 강제만 남는다.
"""
import logging

from app.core.schemas import DEFAULT_SCHEME, PAYLOAD_SCHEMES

logger = logging.getLogger("fitset")


def enforce_scheme_contract(
    scheme: str | None, payload: dict | None
) -> tuple[str, dict | None]:
    """§7 계약 강제 — 어긋나면 text로 강등한다. 클라가 두 갈래 파싱을 하지 않게."""
    if scheme not in PAYLOAD_SCHEMES:
        if payload is not None:
            logger.warning("scheme %r carried a payload, dropping it", scheme)
        return DEFAULT_SCHEME, None
    if not payload:
        # 툴이 데이터 부족으로 payload를 못 만든 경우 — 빈 차트·빈 루틴을 내보내지 않는다
        logger.info("scheme %s had no payload, downgrading to text", scheme)
        return DEFAULT_SCHEME, None
    return scheme, payload
