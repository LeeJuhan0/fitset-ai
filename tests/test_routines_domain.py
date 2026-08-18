"""루틴 도메인 순수 로직 테스트 — 룰 필터·무게 추천(3계층)·기록 통계."""
import pytest

from app.routines import domain

META = {
    "barbell-bench-press": {
        "slug": "barbell-bench-press",
        "primaryMuscles": ["Chest"],
        "equipment": ["Barbell"],
        "movementPattern": ["Push"],
    },
    "push-up": {
        "slug": "push-up",
        "primaryMuscles": ["Chest"],
        "equipment": ["Bodyweight"],
        "movementPattern": ["Push"],
    },
    "barbell-overhead-press": {
        "slug": "barbell-overhead-press",
        "primaryMuscles": ["Shoulders"],
        "equipment": ["Barbell"],
        "movementPattern": ["Push"],
    },
    "dumbbell-bench-press": {
        "slug": "dumbbell-bench-press",
        "primaryMuscles": ["Chest"],
        "equipment": ["Dumbbell"],
        "movementPattern": ["Push"],
    },
    "dumbbell-incline-bench-press": {
        "slug": "dumbbell-incline-bench-press",
        "primaryMuscles": ["Chest"],
        "equipment": ["Dumbbell"],
        "movementPattern": ["Push"],
    },
    "machine-underhand-row": {
        "slug": "machine-underhand-row",
        "primaryMuscles": ["Biceps"],
        "equipment": ["Machine"],
        "movementPattern": ["Pull"],
    },
    "dumbbell-curl": {
        "slug": "dumbbell-curl",
        "primaryMuscles": ["Biceps"],
        "equipment": ["Dumbbell"],
        "movementPattern": ["Isolation"],
    },
}

ROUTINE = {
    "slug": "test-w1-d1",
    "level": "intermediate",
    "muscle_groups": ["chest", "triceps"],
    "equipment": ["barbell"],
    "minutes_per_routine": 50,
}


def test_effective_avoided_client_request_wins():
    # 기피 부위와 요청 부위가 겹치면 클라 요청 우선 — 실효 기피에서 빠진다
    assert domain.effective_avoided(["chest", "back"], ["chest"]) == {"back"}
    assert domain.effective_avoided(None, ["chest"]) == set()


def test_passes_filters_muscle_intersection_and_level():
    base = dict(avoided=set(), level="intermediate", minutes=50, tolerance=0.2, home_only=False)
    assert domain.passes_filters(ROUTINE, muscle_groups=["chest"], **base)
    assert not domain.passes_filters(ROUTINE, muscle_groups=["back"], **base)          # 교집합 없음
    assert not domain.passes_filters(ROUTINE, muscle_groups=["chest"], **{**base, "level": "beginner"})   # 수준 상한
    assert not domain.passes_filters(ROUTINE, muscle_groups=["chest"], **{**base, "minutes": 30})         # ±20% 밖
    assert not domain.passes_filters(ROUTINE, muscle_groups=["chest"], **{**base, "avoided": {"triceps"}})
    assert not domain.passes_filters(ROUTINE, muscle_groups=["chest"], **{**base, "home_only": True})     # 장비 필요 루틴


def test_bodyweight_ratio_home_user():
    workouts = [
        {"exercises": [{"slug": "push-up", "sets": []}, {"slug": "push-up", "sets": []},
                       {"slug": "barbell-bench-press", "sets": []}]},
    ]
    assert domain.bodyweight_ratio(workouts, META) == pytest.approx(2 / 3)
    assert domain.bodyweight_ratio([], META) is None


def _workouts_with(weight, reps, slug="barbell-bench-press"):
    return [{"exercises": [{"slug": slug, "sets": [{"weight": weight, "reps": reps}]}]}]


def test_recommend_weight_tier_a_from_history():
    # 60kg × 10렙 → e1RM 80 → 5렙 목표 = 80/(1+5/30) ≈ 68.6 → 바벨 2.5 단위 = 67.5
    stats = domain.build_e1rm_stats(_workouts_with(60, 10), META)
    weight = domain.recommend_weight("barbell-bench-press", 5, stats, {}, META)
    assert weight == 67.5


def test_recommend_weight_tier_b_same_muscle_pattern_equipment():
    # 덤벨 벤치 기록 → 같은 주동근·패턴·장비인 덤벨 인클라인 벤치로 전이. e1RM × 0.8
    stats = domain.build_e1rm_stats(_workouts_with(20, 10, "dumbbell-bench-press"), META)
    weight = domain.recommend_weight("dumbbell-incline-bench-press", 10, stats, {}, META)
    expected = domain.weight_for_reps(domain.epley_e1rm(20, 10) * 0.8, 10)
    assert weight == pytest.approx(round(expected / 2) * 2)


def test_recommend_weight_no_transfer_across_equipment():
    # 바벨 벤치 기록은 덤벨 벤치로 넘어가지 않는다 — 양손 무게가 한 손 무게로 둔갑하던 경로
    stats = domain.build_e1rm_stats(_workouts_with(60, 10), META)
    profile = {"gender": "MALE", "weightKg": 70, "level": "beginner", "goal": "hypertrophy"}
    weight = domain.recommend_weight("dumbbell-bench-press", 10, stats, profile, META)
    baseline = domain.baseline_e1rm(META["dumbbell-bench-press"], profile)
    assert weight == pytest.approx(round(domain.weight_for_reps(baseline, 10) / 2) * 2)


def test_recommend_weight_no_transfer_compound_to_isolation():
    # 머신 언더핸드 로우(이두 주동)의 무게가 덤벨 컬로 흘러들지 않는다 — 실기기 검증 26건의 원인
    stats = domain.build_e1rm_stats(_workouts_with(90, 8, "machine-underhand-row"), META)
    profile = {"gender": "MALE", "weightKg": 70, "level": "beginner", "goal": "hypertrophy"}
    weight = domain.recommend_weight("dumbbell-curl", 10, stats, profile, META)
    assert weight is not None and weight <= 10


def test_recommend_weight_tier_b_capped_by_baseline_multiple():
    # 같은 키라도 전이값이 과하면 초기 추정의 3배에서 잘린다
    stats = domain.build_e1rm_stats(_workouts_with(200, 5, "dumbbell-bench-press"), META)
    profile = {"gender": "MALE", "weightKg": 70, "level": "beginner", "goal": "hypertrophy"}
    weight = domain.recommend_weight("dumbbell-incline-bench-press", 10, stats, profile, META)
    cap = domain.baseline_e1rm(META["dumbbell-incline-bench-press"], profile) * domain.E1RM_CAP_MULTIPLE
    assert weight == pytest.approx(round(domain.weight_for_reps(cap, 10) / 2) * 2)


def test_recommend_weight_tier_a_not_capped():
    # 실측은 상한을 받지 않는다 — 유저가 실제로 든 무게라 초기 추정보다 커도 그대로 쓴다
    stats = domain.build_e1rm_stats(_workouts_with(120, 5), META)
    profile = {"gender": "MALE", "weightKg": 70, "level": "beginner", "goal": "hypertrophy"}
    weight = domain.recommend_weight("barbell-bench-press", 5, stats, profile, META)
    assert weight == pytest.approx(120.0)


def test_recommend_weight_tier_c_profile_based():
    # 기록 전무 남성 70kg intermediate strength — 수직 푸시 0.25 × 1.3 × 1.1
    stats = ({}, {})
    profile = {"gender": "MALE", "weightKg": 70, "level": "intermediate", "goal": "strength"}
    weight = domain.recommend_weight("barbell-overhead-press", 8, stats, profile, META)
    expected_e1rm = 70 * 0.25 * 1.3 * 1.1
    expected = round(domain.weight_for_reps(expected_e1rm, 8) / 2.5) * 2.5
    assert weight == expected


def test_recommend_weight_bodyweight_is_none():
    assert domain.recommend_weight("push-up", 15, ({}, {}), {}, META) is None


def test_e1rm_stats_skip_zero_weight_and_high_reps():
    workouts = [{"exercises": [{"slug": "barbell-bench-press", "sets": [
        {"weight": 0, "reps": 10},     # 맨몸/미기록 인코딩 — 제외
        {"weight": 60, "reps": 20},    # 12렙 초과 — Epley 오차로 제외
    ]}]}]
    by_slug, by_transfer = domain.build_e1rm_stats(workouts, META)
    assert by_slug == {}
    assert by_transfer == {}


def test_warmup_weight_half():
    assert domain.warmup_weight(60.0, ["Barbell"]) == 30.0
    assert domain.warmup_weight(None, ["Bodyweight"]) is None
