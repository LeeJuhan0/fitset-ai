"""종목 저장소 — 카탈로그 캐시(`exercise_catalog`)와 백엔드 MySQL 종목 마스터 직조회.

카탈로그가 담는 것은 AI 서버가 자체 생성할 수 없는 값뿐이다 — 백엔드 UUID(`exerciseId`),
종목 수행 방식(`exerciseType`), CDN 썸네일·영상 URL. 종목의 나머지 정보(한글명·부위·장비·수행 방법)는 repo 동봉
metadata(206종)가 정본이므로 중복 저장하지 않는다.

일 1회 배치(scripts/sync_exercise_catalog.py)가 갱신하고 서버는 첫 조회 때 1회 Scan한다
— 206건짜리 정적 카탈로그라 스냅샷으로 충분하지만, 배치가 갱신해도 재시작 전까지
반영되지 않는다(루틴 스토어와 같은 성격, 감사 F16).

카탈로그가 비어 있어도(배치 미실행·조회 실패) 서버는 정상 동작한다 — 그 경우 exerciseId·
영상 URL이 null로 강등될 뿐이다(2026-08-12 내부 API 파기로 카탈로그가 URL 유일 출처).
부팅을 막을 만큼 치명적인 데이터가 아니다.
"""
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select

from app.clients import mysql
from app.core.config import get_settings
from app.core.dynamo import get_exercise_catalog_table, to_plain
from app.core.errors import ExerciseNotFoundError
from app.exercises.domain import Equipment, Exercise, ExerciseMuscle, Muscle

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
    """종목 slug → CDN 영상 URL(무서명·무기한). 캐시 미스면 None — 종목 마스터 videoUrl 폴백."""
    return (get_exercise_catalog().get(slug) or {}).get("video_url")


def cdn_thumbnail_url(slug: str) -> str | None:
    """종목 slug → CDN 썸네일 URL. 캐시 미스면 None."""
    return (get_exercise_catalog().get(slug) or {}).get("thumbnail_url")


# ── 종목 마스터 (repo 동봉 metadata 206종) — 카탈로그와 별개인 로컬 정본 ──────────

@lru_cache
def get_exercise_meta() -> dict[str, dict]:
    """종목 마스터 206종 (repo 동봉 metadata) — slug → 항목."""
    path = Path(get_settings().exercise_metadata_path)
    entries = json.loads(path.read_text())
    return {entry["slug"]: entry for entry in entries}


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


def _instruction_steps(raw) -> list[str]:
    """instructions JSON → 문자열 배열 — 구 내부 API가 하던 정규화의 승계.

    실 DB는 [{"content": 문장, "stepOrder": n}] 형태다(2026-08-12 prod 실측 —
    미정규화 시 툴의 join이 TypeError로 죽어 영상 payload까지 유실된다).
    """
    if not raw:
        return []
    if all(isinstance(step, str) for step in raw):
        return list(raw)
    ordered = sorted(raw, key=lambda step: step.get("stepOrder", 0))
    return [step.get("content", "") for step in ordered if step.get("content")]


def get_exercise(slug: str) -> dict:
    """종목 마스터 상세 — 백엔드 MySQL 직조회 (구 내부 API §4.3 승계).

    반환 형태는 구 spring 클라이언트와 동일. 썸네일·영상 URL은 DB에 컬럼이 없어(2026-08-12 ERD)
    카탈로그 캐시에서 채운다. 동기 — 호출부가 asyncio.to_thread로 감싼다.
    """
    rows = mysql.fetch_all(
        select(Exercise.id, Exercise.name, Exercise.difficulty, Exercise.instructions, Equipment.slug.label("equipment"))
        .join(Equipment, Equipment.id == Exercise.equipment_id)
        .where(Exercise.slug == slug)
    )
    if not rows:
        raise ExerciseNotFoundError()
    row = rows[0]

    muscles = mysql.fetch_all(
        select(ExerciseMuscle.role, Muscle.slug)
        .select_from(ExerciseMuscle)
        .join(Muscle, Muscle.id == ExerciseMuscle.muscle_id)
        .where(ExerciseMuscle.exercise_id == row["id"])
        .order_by(Muscle.slug)
    )
    return {
        "slug": slug,
        "name": row["name"],
        "primaryMuscles": [m["slug"] for m in muscles if m["role"] == "PRIMARY"],
        "secondaryMuscles": [m["slug"] for m in muscles if m["role"] == "SECONDARY"],
        "equipment": mysql.camel_kebab(row["equipment"]),  # 키 파일명이 kebab-case라 표기 변환 (#96)
        "difficulty": mysql.camel_enum(row["difficulty"]),
        "instructions": _instruction_steps(row["instructions"]),
        "thumbnailUrl": cdn_thumbnail_url(slug),
        "videoUrl": cdn_video_url(slug),
    }
