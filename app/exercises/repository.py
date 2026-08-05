"""종목 카탈로그 저장소 — 백엔드 마스터 캐시(`exercise_catalog`)를 읽는 유일한 곳.

담는 것은 AI 서버가 자체 생성할 수 없는 값뿐이다 — 백엔드 UUID(`exerciseId`),
종목 수행 방식(`exerciseType`), CDN 썸네일·영상 URL. 종목의 나머지 정보(한글명·부위·장비·수행 방법)는 repo 동봉
metadata(206종)가 정본이므로 중복 저장하지 않는다.

일 1회 배치(scripts/sync_exercise_catalog.py)가 갱신하고 서버는 첫 조회 때 1회 Scan한다
— 206건짜리 정적 카탈로그라 스냅샷으로 충분하지만, 배치가 갱신해도 재시작 전까지
반영되지 않는다(루틴 스토어와 같은 성격, 감사 F16).

카탈로그가 비어 있어도(배치 미실행·조회 실패) 서버는 정상 동작한다 — 그 경우 exerciseId는
null, 영상은 presigned URL 폴백으로 강등된다. 부팅을 막을 만큼 치명적인 데이터가 아니다.
"""
import logging
from functools import lru_cache

from app.core.dynamo import get_exercise_catalog_table, to_plain

logger = logging.getLogger("fitset")


@lru_cache
def get_exercise_catalog() -> dict[str, dict]:
    """slug → {exercise_id, thumbnail_url, video_url}. 실패 시 빈 dict(폴백 경로로 강등)."""
    catalog: dict[str, dict] = {}
    try:
        kwargs: dict = {}
        while True:
            page = get_exercise_catalog_table().scan(**kwargs)
            for item in page.get("Items", []):
                entry = to_plain(item)
                catalog[entry["slug"]] = entry
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    except Exception:
        logger.exception("exercise catalog load failed, degrading to metadata-only")
        return {}
    logger.info("exercise catalog loaded: %d entries", len(catalog))
    return catalog


def exercise_id(slug: str) -> str | None:
    """종목 slug → 백엔드 마스터 UUID. 캐시 미스면 None — 클라가 slug로 매핑 폴백."""
    return (get_exercise_catalog().get(slug) or {}).get("exercise_id")


def exercise_type(slug: str) -> str | None:
    """종목 slug → 수행 방식(WEIGHT_AND_REPS·REPS_ONLY·DURATION). 미스면 None(무게·렙 기본)."""
    return (get_exercise_catalog().get(slug) or {}).get("exercise_type")


def cdn_video_url(slug: str) -> str | None:
    """종목 slug → CDN 영상 URL(무서명·무기한). 캐시 미스면 None — presign 폴백."""
    return (get_exercise_catalog().get(slug) or {}).get("video_url")


def cdn_thumbnail_url(slug: str) -> str | None:
    """종목 slug → CDN 썸네일 URL. 캐시 미스면 None."""
    return (get_exercise_catalog().get(slug) or {}).get("thumbnail_url")
