from app.memory.schemas import UserPreferences
from app.memory.utils import render_preferences
from app.routines.enums import Goal, Level, MuscleGroup

MUSCLES = frozenset(MuscleGroup)

LEVEL_ORDER = {Level.BEGINNER: 0, Level.INTERMEDIATE: 1, Level.ADVANCED: 2}

MUSCLE_LABELS_KO = {
    MuscleGroup.BACK: "등",
    MuscleGroup.BICEPS: "이두근",
    MuscleGroup.CALVES: "종아리",
    MuscleGroup.CHEST: "가슴",
    MuscleGroup.CORE: "코어",
    MuscleGroup.FOREARMS: "전완근",
    MuscleGroup.GLUTES: "둔근",
    MuscleGroup.HAMSTRINGS: "햄스트링",
    MuscleGroup.QUADRICEPS: "대퇴사두근",
    MuscleGroup.SHOULDERS: "어깨",
    MuscleGroup.TRAPEZIUS: "승모근",
    MuscleGroup.TRICEPS: "삼두근",
}
LEVEL_LABELS_KO = {Level.BEGINNER: "초급", Level.INTERMEDIATE: "중급", Level.ADVANCED: "고급"}
GOAL_LABELS_KO = {Goal.HYPERTROPHY: "근비대", Goal.WEIGHT_LOSS: "체중감량", Goal.STRENGTH: "근력향상", Goal.ENDURANCE: "체력유지"}

DEFAULT_GOAL = Goal.HYPERTROPHY
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


def weighted_equipment(equipment: list[str]) -> str | None:
    """무게 단위를 가진 장비 하나 선택"""
    return next((e for e in equipment if e in EQUIPMENT_STEP), None)


def effective_avoided(avoided: list[str] | None, requested: list[str]) -> set[str]:
    """요청 부위와 겹치는 항목 제외한 실효 기피 부위 계산"""
    return set(avoided or []) - set(requested)


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


def build_e1rm_stats(workouts: list[dict]) -> dict[str, float]:
    """기록에서 종목별 최대 e1RM 추출"""
    by_slug: dict[str, float] = {}
    for session in workouts:
        for exercise in session.get("exercises", []):
            slug = _exercise_slug(exercise)
            for set_record in exercise.get("sets", []):
                weight = set_record.get("weight") or 0
                reps = set_record.get("reps") or 0
                if weight <= 0 or reps <= 0 or reps > E1RM_MAX_REPS:
                    continue
                by_slug[slug] = max(by_slug.get(slug, 0), epley_e1rm(weight, reps))
    return by_slug


def round_weight(weight: float, equipment: list[str]) -> float | None:
    """장비별 증량 단위로 무게 반올림"""
    matched = weighted_equipment(equipment)
    if matched is None:
        return None
    step = EQUIPMENT_STEP[matched]
    return max(step, round(weight / step) * step)


def min_weight(slug: str, equipment: list[str]) -> float:
    """종목과 장비별 최소 설정 가능 무게 조회"""
    matched = weighted_equipment(equipment)
    if matched is None:
        return 0.0
    if slug in BAR_WEIGHT_OVERRIDE:
        return BAR_WEIGHT_OVERRIDE[slug]
    return EQUIPMENT_FLOOR.get(matched, 0.0)


def recommend_weight(slug: str, target_reps: int, stats: dict[str, float], meta_by_slug: dict) -> float | None:
    """해당 종목 실측 기록 기준 세트 무게 추천"""
    meta = meta_by_slug.get(slug)
    if meta is None or slug not in stats:
        return None
    equipment = meta.get("equipment", [])
    weight = round_weight(weight_for_reps(stats[slug], target_reps), equipment)
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


def excluded_exercises(preferences: UserPreferences | None) -> tuple[str, ...]:
    """운동 편향의 기피 거절 종목명"""
    if preferences is None:
        return ()
    items = [*preferences.avoided_exercises, *preferences.rejected_exercises]
    return tuple(dict.fromkeys(item.name for item in items))


def preference_context(context: str | None, preferences: UserPreferences | None) -> str | None:
    """요청 문맥 뒤에 운동 편향 문장 덧붙임"""
    rendered = render_preferences(preferences) if preferences is not None else None
    parts = [part for part in (context, rendered) if part]
    return "\n".join(parts) or None


def prefers_home(preferences: UserPreferences | None) -> bool:
    """운동 편향상 집에서 운동하는지 여부"""
    return preferences is not None and preferences.environment.place == "home"
