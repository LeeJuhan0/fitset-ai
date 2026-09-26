import pytest

from app.routines import utils


@pytest.mark.parametrize("equipment, expected", [
    (["Bodyweight", "Dumbbell", "Barbell"], "Dumbbell"),
    (["Bodyweight"], None),
    ([], None),
])
def test_무게_장비는_증량_단위가_있는_첫_장비다(equipment: list[str], expected):
    matched = utils.weighted_equipment(equipment)

    assert matched == expected


@pytest.mark.parametrize("avoided, requested, expected", [
    (["back", "chest"], ["chest"], {"back"}),
    (["back"], [], {"back"}),
    (None, ["chest"], set()),
])
def test_실효_기피_부위는_요청_부위와_겹치는_항목을_뺀다(avoided, requested: list[str], expected: set):
    result = utils.effective_avoided(avoided, requested)

    assert result == expected


def test_맨몸_비율은_카탈로그에_있는_종목_중_맨몸_종목_비율이다(weight_workouts: list[dict], weight_meta: dict):
    workouts = weight_workouts

    ratio = utils.bodyweight_ratio(workouts, weight_meta)

    assert ratio == 0.25


@pytest.mark.parametrize("workouts", [[], [{"exercises": [{"slug": "unknown-exercise"}]}]])
def test_맨몸_비율은_판정할_종목이_없으면_없다(workouts: list[dict], weight_meta: dict):
    ratio = utils.bodyweight_ratio(workouts, weight_meta)

    assert ratio is None


def test_추정_1RM과_목표_렙_무게는_서로_역산된다():
    e1rm = utils.epley_e1rm(100.0, 5)

    weight = utils.weight_for_reps(e1rm, 5)

    assert (e1rm, weight) == (pytest.approx(116.667, abs=1e-3), pytest.approx(100.0))


def test_종목별_추정_1RM은_12렙_이하_무게_있는_세트의_최댓값이다(weight_workouts: list[dict]):
    workouts = weight_workouts

    stats = utils.build_e1rm_stats(workouts)

    assert stats == {"barbell-squat": pytest.approx(121.0), "dumbbell-curl": pytest.approx(16.0), "unknown-exercise": pytest.approx(58.333, abs=1e-3)}


@pytest.mark.parametrize("weight, equipment, expected", [
    (62.4, ["Barbell"], 60.0),
    (63.0, ["Barbell"], 65.0),
    (9.1, ["Dumbbell"], 10.0),
    (1.0, ["Dumbbell"], 2.0),
    (50.0, ["Bodyweight"], None),
])
def test_무게는_장비_증량_단위로_반올림하고_한_단위_아래로_내리지_않는다(weight: float, equipment: list[str], expected):
    rounded = utils.round_weight(weight, equipment)

    assert rounded == expected


@pytest.mark.parametrize("slug, equipment, expected", [
    ("barbell-squat", ["Barbell"], 20.0),
    ("ez-bar-preacher-curl", ["Barbell"], 10.0),
    ("kettlebell-swing", ["Kettlebell"], 4.0),
    ("push-up", ["Bodyweight"], 0.0),
])
def test_최소_무게는_종목_예외_다음_장비_하한을_따른다(slug: str, equipment: list[str], expected: float):
    floor = utils.min_weight(slug, equipment)

    assert floor == expected


def test_추천_무게는_실측_1RM에서_목표_렙_무게를_역산해_반올림한다(weight_meta: dict):
    stats = {"barbell-squat": 116.67}

    weight = utils.recommend_weight("barbell-squat", 5, stats, weight_meta)

    assert weight == 100.0


def test_추천_무게는_장비_최소_무게_아래로_내리지_않는다(weight_meta: dict):
    stats = {"barbell-squat": 30.0}

    weight = utils.recommend_weight("barbell-squat", 12, stats, weight_meta)

    assert weight == 20.0


@pytest.mark.parametrize("slug, stats", [
    ("unknown-exercise", {"unknown-exercise": 100.0}),
    ("dumbbell-curl", {}),
    ("push-up", {"push-up": 50.0}),
])
def test_추천_무게는_카탈로그_실측_무게_장비가_없으면_없다(slug: str, stats: dict, weight_meta: dict):
    weight = utils.recommend_weight(slug, 10, stats, weight_meta)

    assert weight is None


@pytest.mark.parametrize("slug, level, fallback, expected", [
    ("hand-plank", "beginner", 30, 20),
    ("hand-plank", "advanced", 30, 40),
    ("unknown-exercise", "intermediate", 42, 40),
    ("wall-sit", "unknown-level", 0, 45),
    ("unknown-exercise", "beginner", 3, 5),
])
def test_시간_세트는_종목_기본값에_난이도_계수를_곱해_5초_단위로_맞춘다(
    slug: str,
    level: str,
    fallback: int,
    expected: int,
):
    seconds = utils.set_duration_seconds(slug, level, fallback)

    assert seconds == expected


@pytest.mark.parametrize("slug, weight, equipment, expected", [
    ("barbell-squat", 100.0, ["Barbell"], 50.0),
    ("barbell-squat", 30.0, ["Barbell"], 20.0),
    ("barbell-squat", None, ["Barbell"], None),
    ("push-up", 10.0, ["Bodyweight"], None),
])
def test_워밍업_무게는_본_세트_절반이고_장비_최소_무게를_보장한다(slug: str, weight, equipment: list[str], expected):
    warmup = utils.warmup_weight(slug, weight, equipment)

    assert warmup == expected
