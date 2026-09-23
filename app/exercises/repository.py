import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select

from app.clients import mysql
from app.core.config import get_settings
from app.exercises.models import CatalogEntry, Equipment, Exercise, ExerciseMuscle, Muscle
from app.exercises.exceptions import ExerciseNotFoundError

logger = logging.getLogger("fitset")


@lru_cache
def get_exercise_catalog() -> dict[str, dict]:
    """종목 카탈로그 DynamoDB 스캔 후 인메모리 캐시"""
    catalog: dict[str, dict] = {}
    try:
        for entry in CatalogEntry.scan():
            catalog[entry.slug] = entry.to_entry()
    except Exception:
        logger.exception("exercise catalog load failed, degrading to metadata-only")
        return {}
    logger.info("exercise catalog loaded: %d entries", len(catalog))
    return catalog


def exercise_id(slug: str) -> str | None:
    """종목 slug 기준 백엔드 마스터 UUID 조회"""
    return (get_exercise_catalog().get(slug) or {}).get("exercise_id")


def exercise_type(slug: str) -> str | None:
    """종목 slug 기준 수행 방식 조회"""
    return (get_exercise_catalog().get(slug) or {}).get("exercise_type")


def cdn_video_url(slug: str) -> str | None:
    """종목 slug 기준 CDN 영상 URL 조회"""
    return (get_exercise_catalog().get(slug) or {}).get("video_url")


def cdn_thumbnail_url(slug: str) -> str | None:
    """종목 slug 기준 CDN 썸네일 URL 조회"""
    return (get_exercise_catalog().get(slug) or {}).get("thumbnail_url")


@lru_cache
def get_exercise_meta() -> dict[str, dict]:
    """동봉 종목 마스터 JSON 로드 후 slug 색인 캐시"""
    path = Path(get_settings().exercise_metadata_path)
    entries = json.loads(path.read_text())
    return {entry["slug"]: entry for entry in entries}


def _normalize(text: str) -> str:
    """공백 하이픈 대소문자 제거 비교용 정규화"""
    return re.sub(r"[\s\-_]+", "", text).lower()


@lru_cache
def _name_index() -> dict[str, str]:
    """정규화된 종목명과 slug 색인 캐시 생성"""
    index: dict[str, str] = {}
    for slug, entry in get_exercise_meta().items():
        index[_normalize(slug)] = slug
        for key in ("name_ko", "name"):
            value = entry.get(key)
            if value:
                index.setdefault(_normalize(value), slug)
    return index


def resolve_exercise_slug(value: str) -> str | None:
    """slug 또는 한글 종목명을 실존 slug 로 해석"""
    if not value or not value.strip():
        return None
    return _name_index().get(_normalize(value))


def suggest_exercises(value: str, limit: int = 5) -> list[str]:
    """부분 일치 종목명 후보 상위 목록 반환"""
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
    """종목 slug 기준 한글 종목명 조회"""
    entry = get_exercise_meta().get(slug)
    if entry is None:
        return None
    return entry.get("name_ko") or entry.get("name")


def _instruction_steps(raw) -> list[str]:
    """수행 방법 JSON 을 순서 정렬 문자열 배열로 정규화"""
    if not raw:
        return []
    if all(isinstance(step, str) for step in raw):
        return list(raw)
    ordered = sorted(raw, key=lambda step: step.get("stepOrder", 0))
    return [step.get("content", "") for step in ordered if step.get("content")]


def get_exercise(slug: str) -> dict:
    """종목 마스터 상세 MySQL 조회 후 URL 결합"""
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
        "equipment": mysql.camel_kebab(row["equipment"]),
        "difficulty": mysql.camel_enum(row["difficulty"]),
        "instructions": _instruction_steps(row["instructions"]),
        "thumbnailUrl": cdn_thumbnail_url(slug),
        "videoUrl": cdn_video_url(slug),
    }
