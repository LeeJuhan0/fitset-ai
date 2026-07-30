"""에이전트 가드레일 — LLM 출력이 계약을 벗어나지 않게 막는다.

두 가지를 본다:
1. slug 실재성 — LLM이 지어낸 종목 slug가 payload로 나가지 않게 로컬 마스터(206종)로 해석·검증
2. §7 tagged union — responseScheme이 payload의 존재·구조를 결정한다.
   text인데 payload가 있거나 chart인데 없으면 서버 버그이므로, 나가기 전에 여기서 정리한다.

프롬프트는 뚫릴 수 있다는 전제로 설계했다 — 최종 방어선은 프롬프트가 아니라 이 모듈이다.
"""
import logging
import re
from functools import lru_cache

from app.routines.repository import get_exercise_meta

logger = logging.getLogger("fitset")

PAYLOAD_SCHEMES = frozenset({"chart", "exerciseGif", "routine"})
DEFAULT_SCHEME = "text"


def _normalize(text: str) -> str:
    """비교용 정규화 — 공백·하이픈·대소문자 차이를 지운다."""
    return re.sub(r"[\s\-_]+", "", text).lower()


@lru_cache
def _name_index() -> dict[str, str]:
    """정규화된 한글명·slug → slug 색인. 종목 마스터는 부팅 후 불변이라 캐시한다."""
    index: dict[str, str] = {}
    for slug, entry in get_exercise_meta().items():
        index[_normalize(slug)] = slug
        for key in ("name_ko", "name"):
            value = entry.get(key)
            if value:
                index.setdefault(_normalize(value), slug)
    return index


def resolve_exercise_slug(value: str) -> str | None:
    """slug 또는 한글 종목명 → 실존 slug. 마스터에 없으면 None(LLM 환각 차단)."""
    if not value or not value.strip():
        return None
    return _name_index().get(_normalize(value))


def suggest_exercises(value: str, limit: int = 5) -> list[str]:
    """부분 일치 종목명 후보 — 정확 매칭 실패 시 LLM이 되묻거나 재호출할 근거.

    작은 모델은 "스쿼트"처럼 부분 이름을 넘기기 쉬운데("바벨 스쿼트"가 정본),
    빈손으로 돌려주면 "종목 없음"으로 끝난다. 후보를 주면 한 왕복 안에 복구된다.
    """
    needle = _normalize(value)
    if not needle:
        return []
    slugs: list[str] = []
    for key, slug in _name_index().items():
        if needle in key and slug not in slugs:
            slugs.append(slug)
        if len(slugs) >= limit:
            break
    return [exercise_name(slug) or slug for slug in slugs]


def exercise_name(slug: str) -> str | None:
    """slug → 한글 종목명. 내부 API가 죽어도 이름은 로컬 마스터로 채울 수 있다."""
    entry = get_exercise_meta().get(slug)
    if entry is None:
        return None
    return entry.get("name_ko") or entry.get("name")


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
