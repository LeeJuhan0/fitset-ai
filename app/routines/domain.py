"""루틴 도메인 — 검색 저장소 엔티티와 I/O 없는 순수 함수·상수.

룰 필터는 CLAUDE.md 확정 규칙(2026-07-25)을 repository.search_statement 가 WHERE 절로 조립하고, 무게 추천은 docs/무게 추천.md의
Epley e1RM 3계층 폴백(실측 → 같은 주동근·패턴·장비 전이×0.8 → 성별·체중×레벨×목표)을 구현한다.
"""
import json
import re

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, SmallInteger, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.orm import SearchBase

# 팀 공통 MUSCLE enum — LLM이 파싱한 기피 부위 화이트리스트 검증에 쓴다
MUSCLES = frozenset({
    "back", "biceps", "calves", "chest", "core", "forearms",
    "glutes", "hamstrings", "quadriceps", "shoulders", "trapezius", "triceps",
})

LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}


class Routine(SearchBase):
    """routines 테이블 — 검색 저장소(Postgres pgvector) 엔티티, 검색과 LLM 선택 프롬프트에 쓰는 컬럼만 선언한다 (scripts/sql/routines_pgvector.sql 과 1:1)."""

    __tablename__ = "routines"

    slug: Mapped[str] = mapped_column(Text, primary_key=True)
    goal: Mapped[str | None] = mapped_column(Text)
    level: Mapped[int] = mapped_column(SmallInteger)            # 0 beginner, 1 intermediate, 2 advanced
    minutes: Mapped[int | None] = mapped_column(SmallInteger)   # NULL 이면 시간 필터 통과
    muscle_groups: Mapped[list[str]] = mapped_column(ARRAY(Text))
    bodyweight_only: Mapped[bool] = mapped_column(Boolean)
    exercise_names: Mapped[list[str]] = mapped_column(ARRAY(Text))
    body: Mapped[dict] = mapped_column(JSONB)                   # 세트 상세까지 담긴 전체 루틴
    embedding = mapped_column(Vector(1024))

DEFAULT_GOAL = "hypertrophy"   # 프로필 goal 미입력(null) 시 기본값 — 기본값 처리는 AI 서버 책임(내부 명세 §4.1)
LEVEL_FACTOR = {"beginner": 1.0, "intermediate": 1.3, "advanced": 1.6}
GOAL_FACTOR = {"strength": 1.1, "hypertrophy": 1.0, "weightLoss": 0.9, "endurance": 0.9}

# 패턴 그룹 → (남, 여) 체중 비율 — 무게 추천 C 계층 초기 e1RM.
# 바벨·머신 같은 양손 기준 부하로 표기하고, 한 손 장비는 baseline_e1rm이 절반으로 환산한다.
PATTERN_RATIO = {
    "squat": (0.70, 0.55),
    "hinge": (0.90, 0.68),
    "push_horizontal": (0.55, 0.32),
    "push_vertical": (0.38, 0.23),
    "pull": (0.50, 0.35),
    "isolation": (0.30, 0.20),
    "isolation_small": (0.20, 0.13),
}

# 고립 종목 중 절대 부하가 확연히 낮은 주동근 — 레이즈·리스트 컬 계열이 컬과 같은 값을 받지 않게 가른다
SMALL_ISOLATION_MUSCLES = frozenset({"Shoulders", "Forearms", "Triceps"})

# 무게가 한 손 기준인 장비 — 초기 추정과 상한을 절반으로 본다
PER_HAND_EQUIPMENT = frozenset({"Dumbbell", "Kettlebell"})

# 장비별 부하 계수 — 패턴 비율이 바벨 기준이라 기구 특성만큼 보정한다.
# 프리웨이트는 좌우를 따로 잡느라 안정화에 힘을 쓰고, 머신은 궤도가 고정돼 더 들 수 있다.
# 한 손 표기 환산(÷2)과는 별개다 — 이쪽은 단위가 아니라 실제 들 수 있는 부하의 차이다.
EQUIPMENT_LOAD_FACTOR = {
    "Barbell": 1.00,
    "Machine": 1.15,   # 궤도가 고정돼 안정화 부담이 없다. 1.10은 5kg 반올림에 묻혀 바벨과 같은 값이 된다
    "Cable Machine": 0.85,
    "Dumbbell": 0.85,
    "Kettlebell": 0.80,   # 무게 중심이 손 바깥이라 같은 무게도 덤벨보다 어렵다
}

# 장비별 무게 반올림 단위 (metadata equipment 표기 기준)
EQUIPMENT_STEP = {
    "Barbell": 5.0,      # 원판 2.5kg 한 쌍이 최소 증분
    "Dumbbell": 2.0,
    "Kettlebell": 2.0,
    "Machine": 5.0,
    "Cable Machine": 5.0,
}

# 장비별 최소 무게 — 이보다 가벼운 조합은 실물로 만들 수 없다
EQUIPMENT_FLOOR = {
    "Barbell": 20.0,     # 올림픽 봉
    "Dumbbell": 2.0,
    "Kettlebell": 4.0,
    "Machine": 5.0,
    "Cable Machine": 5.0,
}

# 봉을 쓰지 않거나 봉이 가벼운 종목 — 장비 표기는 Barbell이지만 최소 무게가 다르다
BAR_WEIGHT_OVERRIDE = {
    "ez-bar-preacher-curl": 10.0,
    "ez-bar-reverse-preacher-curl": 10.0,
    "landmine-t-bar-rows": 10.0,   # 한쪽이 바닥에 고정돼 실효 부하가 봉 무게보다 작다
    "plate-forward-lunge": 5.0,    # 원판 한 장 — 봉이 없다
}
E1RM_MAX_REPS = 12       # Epley 오차가 커지는 고렙 세트는 e1RM 계산에서 제외
CROSS_EXERCISE_FACTOR = 0.8   # B 계층 — 종목 간 전이 안전 계수
E1RM_CAP_MULTIPLE = 3.0       # B 계층 상한 — C 계층 초기 추정의 배수
DEFAULT_BODY_WEIGHT = 70.0    # 체중 미기록 시 초기값 추정에 쓰는 기본 체중
WARMUP_FACTOR = 0.5

# 시간 종목(exerciseType=DURATION) 세트 길이 — 원본 캐글 루틴이 플랭크마저 "10회"처럼
# 렙으로 인코딩해 시간 값이 없다(2026-08-05 실측). 그 숫자는 초가 아니므로 버리고
# 종목 특성 × 난이도로 정한다 (협의 포인트 ⑪ — 기획 확정 시 값만 조정).
DURATION_BASE_SECONDS = {
    "hand-plank": 30,                # 표준 플랭크 — 30초가 통용 기준
    "elbow-side-plank": 25,          # 사이드는 좌우 각각이라 짧게
    "wall-sit": 45,                  # 등척성 하체 — 더 길게 버틴다
    "kettlebell-farmers-carry": 40,  # 캐리 — 거리·시간 종목
    "abdominals-stretch-variation-one": 20,     # 스트레칭 4종 — 정적 유지라 20초
    "abdominals-stretch-variation-two": 20,
    "abdominals-stretch-variation-three": 20,
    "abdominals-stretch-variation-four": 20,
}
DURATION_LEVEL_FACTOR = {"beginner": 0.7, "intermediate": 1.0, "advanced": 1.3}
DURATION_STEP_SECONDS = 5   # 표시 단위 — 21초 같은 어중간한 값을 내보내지 않는다


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
    if primary & SMALL_ISOLATION_MUSCLES:
        return "isolation_small"
    return "isolation"


def weighted_equipment(equipment: list[str]) -> str | None:
    """무게 단위를 가진 장비 하나를 고른다."""
    return next((e for e in equipment if e in EQUIPMENT_STEP), None)


def transfer_keys(meta: dict) -> list[tuple[str, str, str]]:
    """e1RM 전이 키 — 주동근·패턴 그룹·장비가 모두 같은 종목끼리만 무게를 옮긴다.

    근육만으로 묶으면 머신 로우의 e1RM이 덤벨 컬로, 파머스 캐리가 리스트 컬로 흘러들어
    팔 고립 종목에 들 수 없는 무게가 나간다(2026-08-13 실기기 검증 26건). 장비를 키에
    넣어 바벨 양손 무게가 덤벨 한 손 무게로 그대로 넘어가는 것도 함께 막는다.
    """
    equipment = weighted_equipment(meta.get("equipment", []))
    if equipment is None:
        return []
    group = pattern_group(meta)
    return [(muscle.lower(), group, equipment) for muscle in meta.get("primaryMuscles", [])]


def effective_avoided(avoided: list[str] | None, requested: list[str]) -> set[str]:
    """실효 기피 부위 — 요청 muscleGroups와 겹치는 부위는 클라 요청 우선(기피에서 제외)."""
    return set(avoided or []) - set(requested)


def parse_query_analysis(raw: str) -> tuple[str | None, set[str], list[str]]:
    """쿼리 변환 LLM 응답(JSON) → (묘사문, 기피 부위, 안전 제약). 파싱 실패 시 묘사문 None."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)   # 코드펜스·군더더기 섞여도 본문만 취한다
    if match is None:
        return None, set(), []
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None, set(), []
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        return None, set(), []
    avoid = {m for m in data.get("avoidMuscles") or [] if m in MUSCLES}   # enum 화이트리스트
    unsafe = [c for c in data.get("unsafeConstraints") or [] if isinstance(c, str) and c.strip()]
    return description.strip(), avoid, unsafe



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
    """기록에서 (종목별, 전이키별) 최대 e1RM을 뽑는다 — 무게 추천 A·B 계층 입력.

    weight=0(맨몸/미기록 인코딩)과 12렙 초과 세트는 제외한다.
    """
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
    """장비별 단위로 반올림. 무게 개념이 없는 장비(맨몸·밴드)는 None."""
    matched = weighted_equipment(equipment)
    if matched is None:
        return None
    step = EQUIPMENT_STEP[matched]
    return max(step, round(weight / step) * step)


def baseline_e1rm(meta: dict, profile: dict) -> float:
    """C 계층 초기 e1RM — 성별·체중·수준·목표 추정값. B 계층 상한의 기준선이기도 하다.

    level은 요청이 아니라 프로필을 쓴다. 요청 난이도는 이번 세션을 얼마나 세게 할지의
    선택이지 그 사람의 근력이 바뀐 것이 아니다.
    """
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
    """종목 최소 무게 — 빈 봉보다 가벼운 바벨 세트 같은 만들 수 없는 값을 막는다."""
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
    """세트 무게 추천 — 3계층 폴백 (docs/무게 추천.md)."""
    meta = meta_by_slug.get(slug)
    if meta is None or "Bodyweight" in meta.get("equipment", []):
        return None

    by_slug, by_transfer = stats
    if slug in by_slug:                        # A. 해당 종목 실측 — 유저가 실제로 든 무게라 상한을 걸지 않는다
        e1rm = by_slug[slug]
    else:
        known = [by_transfer[key] for key in transfer_keys(meta) if key in by_transfer]
        if known:                              # B. 같은 주동근·패턴·장비에서 전이, 초기 추정 배수로 상한
            cap = baseline_e1rm(meta, profile) * E1RM_CAP_MULTIPLE
            e1rm = min(max(known) * CROSS_EXERCISE_FACTOR, cap)
        else:                                  # C. 성별·체중·레벨·목표 초기값
            e1rm = baseline_e1rm(meta, profile)

    equipment = meta.get("equipment", [])
    weight = round_weight(weight_for_reps(e1rm, target_reps), equipment)
    if weight is None:
        return None
    return max(weight, min_weight(slug, equipment))


def set_duration_seconds(slug: str, level: str, fallback: int) -> int:
    """시간 종목 세트 길이 — 종목 기본값 × 난이도 계수, 표시 단위로 반올림.

    카탈로그에 없는 신규 시간 종목은 설정 기본값(fallback)을 쓴다.
    """
    base = DURATION_BASE_SECONDS.get(slug, fallback)
    scaled = base * DURATION_LEVEL_FACTOR.get(level, 1.0)
    stepped = round(scaled / DURATION_STEP_SECONDS) * DURATION_STEP_SECONDS
    return max(DURATION_STEP_SECONDS, int(stepped))


def warmup_weight(slug: str, weight: float | None, equipment: list[str]) -> float | None:
    """워밍업 첫 세트 — 본 세트 무게의 절반. 최소 무게 아래로는 못 내려간다."""
    if weight is None:
        return None
    halved = round_weight(weight * WARMUP_FACTOR, equipment)
    if halved is None:
        return None
    return max(halved, min_weight(slug, equipment))
