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


def test_recommend_weight_tier_b_same_muscle():
    # bench 기록만 있는 유저의 다른 가슴 종목 → e1RM × 0.8
    stats = domain.build_e1rm_stats(_workouts_with(60, 10), META)
    meta = {**META, "dumbbell-bench-press": {
        "slug": "dumbbell-bench-press", "primaryMuscles": ["Chest"],
        "equipment": ["Dumbbell"], "movementPattern": ["Push"]}}
    weight = domain.recommend_weight("dumbbell-bench-press", 10, stats, {}, meta)
    assert weight == pytest.approx(48.0)   # 80×0.8=64 → 10렙 48 → 덤벨 2단위 48


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
    by_slug, by_muscle = domain.build_e1rm_stats(workouts, META)
    assert by_slug == {}
    assert by_muscle == {}


def test_warmup_weight_half():
    assert domain.warmup_weight(60.0, ["Barbell"]) == 30.0
    assert domain.warmup_weight(None, ["Bodyweight"]) is None
