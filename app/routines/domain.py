"""루틴 도메인 순수 로직 — I/O 없는 함수·상수만.

룰 필터는 CLAUDE.md 확정 규칙(2026-07-25), 무게 추천은 docs/무게 추천.md의
Epley e1RM 3계층 폴백(실측 → 주동근 유추×0.8 → 성별·체중×레벨×목표)을 구현한다.
"""

LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}
LEVEL_FACTOR = {"beginner": 1.0, "intermediate": 1.3, "advanced": 1.6}
GOAL_FACTOR = {"strength": 1.1, "hypertrophy": 1.0, "weightLoss": 0.9, "endurance": 0.9}

# 패턴 그룹 → (남, 여) 체중 비율 — 무게 추천 C 계층 초기 e1RM
PATTERN_RATIO = {
    "squat": (0.50, 0.40),
    "hinge": (0.60, 0.45),
    "push_horizontal": (0.35, 0.20),
    "push_vertical": (0.25, 0.15),
    "pull": (0.35, 0.25),
    "isolation": (0.10, 0.07),
}

# 장비별 무게 반올림 단위 (metadata equipment 표기 기준)
EQUIPMENT_STEP = {
    "Barbell": 2.5,
    "Dumbbell": 2.0,
    "Kettlebell": 2.0,
    "Machine": 5.0,
    "Cable Machine": 5.0,
}
E1RM_MAX_REPS = 12       # Epley 오차가 커지는 고렙 세트는 e1RM 계산에서 제외
CROSS_EXERCISE_FACTOR = 0.8   # B 계층 — 종목 간 전이 안전 계수
WARMUP_FACTOR = 0.5


def pattern_group(meta: dict) -> str:
    """종목 metadata의 movementPattern·주동근으로 무게 추천 패턴 그룹을 정한다."""
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
    return "isolation"


def effective_avoided(avoided: list[str] | None, requested: list[str]) -> set[str]:
    """실효 기피 부위 — 요청 muscleGroups와 겹치는 부위는 클라 요청 우선(기피에서 제외)."""
    return set(avoided or []) - set(requested)


def passes_filters(
    routine: dict,
    *,
    muscle_groups: list[str],
    avoided: set[str],
    level: str,
    minutes: int,
    tolerance: float,
    home_only: bool,
) -> bool:
    """룰 필터 확정 규칙 — 부위 교집합·실효 기피·수준 상한·시간 ±tolerance·홈트 장비."""
    routine_muscles = set(routine.get("muscle_groups", []))
    if not routine_muscles & set(muscle_groups):
        return False
    if avoided & routine_muscles:
        return False
    if LEVEL_ORDER.get(routine.get("level", "beginner"), 0) > LEVEL_ORDER[level]:
        return False
    routine_minutes = routine.get("minutes_per_routine")
    if routine_minutes and abs(routine_minutes - minutes) > minutes * tolerance:
        return False
    if home_only and not set(routine.get("equipment", [])) <= {"bodyweight"}:
        return False
    return True


def _exercise_slug(exercise: dict) -> str | None:
    # 내부 API 응답의 종목 참조 — 필드명 slug 확정이나 과도기 exerciseId도 수용
    return exercise.get("slug") or exercise.get("exerciseId")


def bodyweight_ratio(workouts: list[dict], meta_by_slug: dict) -> float | None:
    """최근 기록의 맨몸 종목 비율 — 홈트 유저 판정(≥ 임계치)에 쓴다. 기록 없으면 None."""
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
    return weight * (1 + reps / 30)


def weight_for_reps(e1rm: float, reps: int) -> float:
    return e1rm / (1 + reps / 30)


def build_e1rm_stats(workouts: list[dict], meta_by_slug: dict) -> tuple[dict, dict]:
    """기록에서 (종목별, 주동근별) 최대 e1RM을 뽑는다 — 무게 추천 A·B 계층 입력.

    weight=0(맨몸/미기록 인코딩)과 12렙 초과 세트는 제외한다.
    """
    by_slug: dict[str, float] = {}
    by_muscle: dict[str, float] = {}
    for session in workouts:
        for exercise in session.get("exercises", []):
            slug = _exercise_slug(exercise)
            for set_record in exercise.get("sets", []):
                weight = set_record.get("weight") or 0
                reps = set_record.get("reps") or 0
                if weight <= 0 or reps <= 0 or reps > E1RM_MAX_REPS:
                    continue
                e1rm = epley_e1rm(weight, reps)
                by_slug[slug] = max(by_slug.get(slug, 0), e1rm)
                meta = meta_by_slug.get(slug)
                for muscle in (meta or {}).get("primaryMuscles", []):
                    key = muscle.lower()
                    by_muscle[key] = max(by_muscle.get(key, 0), e1rm)
    return by_slug, by_muscle


def round_weight(weight: float, equipment: list[str]) -> float | None:
    """장비별 단위로 반올림. 무게 개념이 없는 장비(맨몸·밴드)는 None."""
    step = next((EQUIPMENT_STEP[e] for e in equipment if e in EQUIPMENT_STEP), None)
    if step is None:
        return None
    return max(step, round(weight / step) * step)


def recommend_weight(
    slug: str,
    target_reps: int,
    stats: tuple[dict, dict],
    profile: dict,
    meta_by_slug: dict,
) -> float | None:
    """세트 무게 추천 — 3계층 폴백 (docs/무게 추천.md)."""
    meta = meta_by_slug.get(slug)
    if meta is None or "Bodyweight" in meta.get("equipment", []):
        return None

    by_slug, by_muscle = stats
    if slug in by_slug:                                    # A. 해당 종목 실측
        e1rm = by_slug[slug]
    else:
        muscles = [m.lower() for m in meta.get("primaryMuscles", [])]
        known = [by_muscle[m] for m in muscles if m in by_muscle]
        if known:                                          # B. 같은 주동근 유추
            e1rm = max(known) * CROSS_EXERCISE_FACTOR
        else:                                              # C. 성별·체중·레벨·목표 초기값
            body_weight = profile.get("weightKg") or 70.0
            gender = (profile.get("gender") or "MALE").upper()
            male_ratio, female_ratio = PATTERN_RATIO[pattern_group(meta)]
            ratio = female_ratio if gender == "FEMALE" else male_ratio
            level = profile.get("level") or "beginner"
            goal = profile.get("goal") or "hypertrophy"
            e1rm = body_weight * ratio * LEVEL_FACTOR.get(level, 1.0) * GOAL_FACTOR.get(goal, 1.0)

    return round_weight(weight_for_reps(e1rm, target_reps), meta.get("equipment", []))


def warmup_weight(weight: float | None, equipment: list[str]) -> float | None:
    """워밍업 첫 세트 — 본 세트 무게의 절반을 장비 단위로 반올림."""
    if weight is None:
        return None
    return round_weight(weight * WARMUP_FACTOR, equipment)
