import json
import re

from app.routines.enums import Goal, Level, MuscleGroup

MUSCLES = frozenset(MuscleGroup)

LEVEL_ORDER = {Level.BEGINNER: 0, Level.INTERMEDIATE: 1, Level.ADVANCED: 2}

DEFAULT_GOAL = Goal.HYPERTROPHY
LEVEL_FACTOR = {Level.BEGINNER: 1.0, Level.INTERMEDIATE: 1.3, Level.ADVANCED: 1.6}
GOAL_FACTOR = {Goal.STRENGTH: 1.1, Goal.HYPERTROPHY: 1.0, Goal.WEIGHT_LOSS: 0.9, Goal.ENDURANCE: 0.9}

PATTERN_RATIO = {
    "squat": (0.70, 0.55),
    "hinge": (0.90, 0.68),
    "push_horizontal": (0.55, 0.32),
    "push_vertical": (0.38, 0.23),
    "pull": (0.50, 0.35),
    "isolation": (0.30, 0.20),
    "isolation_small": (0.20, 0.13),
}

SMALL_ISOLATION_MUSCLES = frozenset({"Shoulders", "Forearms", "Triceps"})

PER_HAND_EQUIPMENT = frozenset({"Dumbbell", "Kettlebell"})

EQUIPMENT_LOAD_FACTOR = {
    "Barbell": 1.00,
    "Machine": 1.15,
    "Cable Machine": 0.85,
    "Dumbbell": 0.85,
    "Kettlebell": 0.80,
}

EQUIPMENT_STEP = {
    "Barbell": 5.0,
    "Dumbbell": 2.0,
    "Kettlebell": 2.0,
    "Machine": 5.0,
    "Cable Machine": 5.0,
}

EQUIPMENT_FLOOR = {
    "Barbell": 20.0,
    "Dumbbell": 2.0,
    "Kettlebell": 4.0,
    "Machine": 5.0,
    "Cable Machine": 5.0,
}

BAR_WEIGHT_OVERRIDE = {
    "ez-bar-preacher-curl": 10.0,
    "ez-bar-reverse-preacher-curl": 10.0,
    "landmine-t-bar-rows": 10.0,
    "plate-forward-lunge": 5.0,
}
E1RM_MAX_REPS = 12
CROSS_EXERCISE_FACTOR = 0.8
E1RM_CAP_MULTIPLE = 3.0
DEFAULT_BODY_WEIGHT = 70.0
WARMUP_FACTOR = 0.5

DURATION_BASE_SECONDS = {
    "hand-plank": 30,
    "elbow-side-plank": 25,
    "wall-sit": 45,
    "kettlebell-farmers-carry": 40,
    "abdominals-stretch-variation-one": 20,
    "abdominals-stretch-variation-two": 20,
    "abdominals-stretch-variation-three": 20,
    "abdominals-stretch-variation-four": 20,
}
DURATION_LEVEL_FACTOR = {Level.BEGINNER: 0.7, Level.INTERMEDIATE: 1.0, Level.ADVANCED: 1.3}
DURATION_STEP_SECONDS = 5


def pattern_group(meta: dict) -> str:
    """동작 패턴과 주동근 기반 무게 추천 그룹 결정"""
    patterns = set(meta.get("movementPattern", []))
    primary = set(meta.get("primaryMuscles", []))
    if patterns & {"Squat", "Lunge"}:
        return "squat"
    if patterns & {"Hinge", "Carry"}:
        return "hinge"
    if "Push" in patterns:
        return "push_vertical" if "Shoulders" in primary else "push_horizontal"
    if "Pull" in patterns:
        return "pull"
    if primary & SMALL_ISOLATION_MUSCLES:
        return "isolation_small"
    return "isolation"


def weighted_equipment(equipment: list[str]) -> str | None:
    """무게 단위를 가진 장비 하나 선택"""
    return next((e for e in equipment if e in EQUIPMENT_STEP), None)


def transfer_keys(meta: dict) -> list[tuple[str, str, str]]:
    """주동근 패턴 그룹 장비 조합 e1RM 전이 키 생성"""
    equipment = weighted_equipment(meta.get("equipment", []))
    if equipment is None:
        return []
    group = pattern_group(meta)
    return [(muscle.lower(), group, equipment) for muscle in meta.get("primaryMuscles", [])]


def effective_avoided(avoided: list[str] | None, requested: list[str]) -> set[str]:
    """요청 부위와 겹치는 항목 제외한 실효 기피 부위 계산"""
    return set(avoided or []) - set(requested)


def parse_query_analysis(raw: str) -> tuple[str | None, set[str], list[str]]:
    """쿼리 분석 LLM 응답 JSON 을 묘사문 기피 제약으로 파싱"""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match is None:
        return None, set(), []
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None, set(), []
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        return None, set(), []
    avoid = {m for m in data.get("avoidMuscles") or [] if m in MUSCLES}
    unsafe = [c for c in data.get("unsafeConstraints") or [] if isinstance(c, str) and c.strip()]
    return description.strip(), avoid, unsafe


def _exercise_slug(exercise: dict) -> str | None:
    """기록 항목에서 종목 slug 또는 exerciseId 추출"""
    return exercise.get("slug") or exercise.get("exerciseId")


def bodyweight_ratio(workouts: list[dict], meta_by_slug: dict) -> float | None:
    """최근 기록 맨몸 종목 비율 계산 홈트 판정용"""
    total = 0
    bodyweight = 0
    for session in workouts:
        for exercise in session.get("exercises", []):
            meta = meta_by_slug.get(_exercise_slug(exercise))
            if meta is None:
                continue
            total += 1
            if "Bodyweight" in meta.get("equipment", []):
                bodyweight += 1
    if total == 0:
        return None
    return bodyweight / total


def epley_e1rm(weight: float, reps: int) -> float:
    """Epley 공식으로 무게와 렙에서 추정 1RM 계산"""
    return weight * (1 + reps / 30)


def weight_for_reps(e1rm: float, reps: int) -> float:
    """추정 1RM 에서 목표 렙 수행 무게 역산"""
    return e1rm / (1 + reps / 30)


def build_e1rm_stats(workouts: list[dict], meta_by_slug: dict) -> tuple[dict, dict]:
    """기록에서 종목별 전이키별 최대 e1RM 통계 추출"""
    by_slug: dict[str, float] = {}
    by_transfer: dict[tuple[str, str, str], float] = {}
    for session in workouts:
        for exercise in session.get("exercises", []):
            slug = _exercise_slug(exercise)
            meta = meta_by_slug.get(slug)
            keys = transfer_keys(meta) if meta else []
            for set_record in exercise.get("sets", []):
                weight = set_record.get("weight") or 0
                reps = set_record.get("reps") or 0
                if weight <= 0 or reps <= 0 or reps > E1RM_MAX_REPS:
                    continue
                e1rm = epley_e1rm(weight, reps)
                by_slug[slug] = max(by_slug.get(slug, 0), e1rm)
                for key in keys:
                    by_transfer[key] = max(by_transfer.get(key, 0), e1rm)
    return by_slug, by_transfer


def round_weight(weight: float, equipment: list[str]) -> float | None:
    """장비별 증량 단위로 무게 반올림"""
    matched = weighted_equipment(equipment)
    if matched is None:
        return None
    step = EQUIPMENT_STEP[matched]
    return max(step, round(weight / step) * step)


def baseline_e1rm(meta: dict, profile: dict) -> float:
    """성별 체중 수준 목표 기반 초기 e1RM 추정"""
    body_weight = profile.get("weightKg") or DEFAULT_BODY_WEIGHT
    gender = (profile.get("gender") or "MALE").upper()
    male_ratio, female_ratio = PATTERN_RATIO[pattern_group(meta)]
    ratio = female_ratio if gender == "FEMALE" else male_ratio
    level = profile.get("level") or "beginner"
    goal = profile.get("goal") or DEFAULT_GOAL
    equipment = weighted_equipment(meta.get("equipment", []))
    e1rm = (
        body_weight * ratio
        * LEVEL_FACTOR.get(level, 1.0)
        * GOAL_FACTOR.get(goal, 1.0)
        * EQUIPMENT_LOAD_FACTOR.get(equipment, 1.0)
    )
    if equipment in PER_HAND_EQUIPMENT:
        return e1rm / 2
    return e1rm


def min_weight(slug: str, equipment: list[str]) -> float:
    """종목과 장비별 최소 설정 가능 무게 조회"""
    matched = weighted_equipment(equipment)
    if matched is None:
        return 0.0
    if slug in BAR_WEIGHT_OVERRIDE:
        return BAR_WEIGHT_OVERRIDE[slug]
    return EQUIPMENT_FLOOR.get(matched, 0.0)


def recommend_weight(
    slug: str,
    target_reps: int,
    stats: tuple[dict, dict],
    profile: dict,
    meta_by_slug: dict,
) -> float | None:
    """실측 유추 기준선 3계층 폴백 세트 무게 추천"""
    meta = meta_by_slug.get(slug)
    if meta is None or "Bodyweight" in meta.get("equipment", []):
        return None

    by_slug, by_transfer = stats
    if slug in by_slug:
        e1rm = by_slug[slug]
    else:
        known = [by_transfer[key] for key in transfer_keys(meta) if key in by_transfer]
        if known:
            cap = baseline_e1rm(meta, profile) * E1RM_CAP_MULTIPLE
            e1rm = min(max(known) * CROSS_EXERCISE_FACTOR, cap)
        else:
            e1rm = baseline_e1rm(meta, profile)

    equipment = meta.get("equipment", [])
    weight = round_weight(weight_for_reps(e1rm, target_reps), equipment)
    if weight is None:
        return None
    return max(weight, min_weight(slug, equipment))


def set_duration_seconds(slug: str, level: str, fallback: int) -> int:
    """종목 기본값과 난이도 계수로 시간 세트 길이 계산"""
    base = DURATION_BASE_SECONDS.get(slug, fallback)
    scaled = base * DURATION_LEVEL_FACTOR.get(level, 1.0)
    stepped = round(scaled / DURATION_STEP_SECONDS) * DURATION_STEP_SECONDS
    return max(DURATION_STEP_SECONDS, int(stepped))


def warmup_weight(slug: str, weight: float | None, equipment: list[str]) -> float | None:
    """본 세트 절반 무게 워밍업 최소 무게 보장"""
    if weight is None:
        return None
    halved = round_weight(weight * WARMUP_FACTOR, equipment)
    if halved is None:
        return None
    return max(halved, min_weight(slug, equipment))
