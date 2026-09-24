"""루틴 도메인 순수 로직 테스트 — 룰 필터·무게 추천(실측 기록)·기록 통계."""
import pytest

from app.routines import utils

META = {
    "barbell-bench-press": {
        "slug": "barbell-bench-press",
        "primaryMuscles": ["Chest"],
        "equipment": ["Barbell"],
    },
    "push-up": {
        "slug": "push-up",
        "primaryMuscles": ["Chest"],
        "equipment": ["Bodyweight"],
    },
    "barbell-overhead-press": {
        "slug": "barbell-overhead-press",
        "primaryMuscles": ["Shoulders"],
        "equipment": ["Barbell"],
    },
    "dumbbell-bench-press": {
        "slug": "dumbbell-bench-press",
        "primaryMuscles": ["Chest"],
        "equipment": ["Dumbbell"],
    },
    "dumbbell-incline-bench-press": {
        "slug": "dumbbell-incline-bench-press",
        "primaryMuscles": ["Chest"],
        "equipment": ["Dumbbell"],
    },
    "machine-underhand-row": {
        "slug": "machine-underhand-row",
        "primaryMuscles": ["Biceps"],
        "equipment": ["Machine"],
    },
    "dumbbell-curl": {
        "slug": "dumbbell-curl",
        "primaryMuscles": ["Biceps"],
        "equipment": ["Dumbbell"],
    },
    "barbell-curl": {
        "slug": "barbell-curl",
        "primaryMuscles": ["Biceps"],
        "equipment": ["Barbell"],
    },
    "ez-bar-preacher-curl": {
        "slug": "ez-bar-preacher-curl",
        "primaryMuscles": ["Biceps"],
        "equipment": ["Barbell"],
    },
    "dumbbell-lateral-raise": {
        "slug": "dumbbell-lateral-raise",
        "primaryMuscles": ["Shoulders"],
        "equipment": ["Dumbbell"],
    },
    "machine-chest-press": {
        "slug": "machine-chest-press",
        "primaryMuscles": ["Chest"],
        "equipment": ["Machine"],
    },
    "kettlebell-floor-press": {
        "slug": "kettlebell-floor-press",
        "primaryMuscles": ["Chest"],
        "equipment": ["Kettlebell"],
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
    assert utils.effective_avoided(["chest", "back"], ["chest"]) == {"back"}
    assert utils.effective_avoided(None, ["chest"]) == set()



def test_bodyweight_ratio_home_user():
    workouts = [
        {"exercises": [{"slug": "push-up", "sets": []}, {"slug": "push-up", "sets": []},
                       {"slug": "barbell-bench-press", "sets": []}]},
    ]
    assert utils.bodyweight_ratio(workouts, META) == pytest.approx(2 / 3)
    assert utils.bodyweight_ratio([], META) is None


def _workouts_with(weight, reps, slug="barbell-bench-press"):
    return [{"exercises": [{"slug": slug, "sets": [{"weight": weight, "reps": reps}]}]}]


def test_recommend_weight_from_history():
    # 60kg × 10렙 → e1RM 80 → 5렙 목표 = 80/(1+5/30) ≈ 68.6 → 바벨 5 단위 = 70
    stats = utils.build_e1rm_stats(_workouts_with(60, 10))
    assert utils.recommend_weight("barbell-bench-press", 5, stats, META) == 70.0


def test_recommend_weight_without_history_is_none():
    # 다른 종목 기록은 빌려오지 않는다 — 기록 없는 종목은 0kg 로 내려간다
    stats = utils.build_e1rm_stats(_workouts_with(20, 10, "dumbbell-bench-press"))
    assert utils.recommend_weight("dumbbell-incline-bench-press", 10, stats, META) is None
    assert utils.recommend_weight("barbell-bench-press", 10, {}, META) is None


def test_recommend_weight_bodyweight_is_none():
    stats = utils.build_e1rm_stats(_workouts_with(10, 10, "push-up"))
    assert utils.recommend_weight("push-up", 15, stats, META) is None


def test_e1rm_stats_skip_zero_weight_and_high_reps():
    workouts = [{"exercises": [{"slug": "barbell-bench-press", "sets": [
        {"weight": 0, "reps": 10},     # 맨몸/미기록 인코딩 — 제외
        {"weight": 60, "reps": 20},    # 12렙 초과 — Epley 오차로 제외
    ]}]}]
    assert utils.build_e1rm_stats(workouts) == {}


def test_warmup_weight_half():
    assert utils.warmup_weight("barbell-bench-press", 60.0, ["Barbell"]) == 30.0
    assert utils.warmup_weight("barbell-bench-press", None, ["Bodyweight"]) is None


def test_warmup_weight_not_below_bar():
    # 본 세트가 빈 봉이면 워밍업도 빈 봉이다 — 절반은 만들 수 없는 무게다
    assert utils.warmup_weight("barbell-curl", 20.0, ["Barbell"]) == 20.0


def test_recommend_weight_not_below_bar():
    # 바벨 종목은 봉 무게 아래로 내려가지 않는다. EZ바는 봉이 가벼워 따로 본다
    workouts = _workouts_with(10, 10, "barbell-curl") + _workouts_with(10, 10, "ez-bar-preacher-curl")
    stats = utils.build_e1rm_stats(workouts)
    assert utils.recommend_weight("barbell-curl", 10, stats, META) == 20.0
    assert utils.recommend_weight("ez-bar-preacher-curl", 10, stats, META) == 10.0
