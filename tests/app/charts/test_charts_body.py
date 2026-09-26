import pytest

from app.charts import utils
from app.charts.enums import ChartType


def test_체중_차트는_날짜별_마지막_측정값을_꺾은선으로_그린다(body_weights: list[dict]):
    items = body_weights

    chart = utils.body_weight_chart(items)

    assert chart == {
        "chartType": ChartType.LINE.value,
        "metric": "bodyWeight",
        "title": "체중 변화",
        "xLabel": "날짜",
        "yLabel": "체중(kg)",
        "x": ["9/15", "9/20"],
        "series": [{"name": "체중", "values": [70.4, 69.8]}],
    }


def test_체중_차트는_날짜순으로_정렬한다(body_weights: list[dict]):
    items = list(reversed(body_weights))

    chart = utils.body_weight_chart(items)

    assert chart["x"] == ["9/15", "9/20"]


@pytest.mark.parametrize("broken", [
    {"measuredAt": None, "weightKg": 80.0},
    {"measuredAt": "invalid", "weightKg": 80.0},
    {"measuredAt": "2026-09-22T08:00:00Z", "weightKg": None},
])
def test_체중_차트는_시각이나_체중이_없는_측정값을_건너뛴다(body_weights: list[dict], broken: dict):
    items = body_weights + [broken]

    chart = utils.body_weight_chart(items)

    assert chart["series"][0]["values"] == [70.4, 69.8]


@pytest.mark.parametrize("items", [
    [],
    [{"measuredAt": "2026-09-15T08:00:00Z", "weightKg": 70.0}],
    [
        {"measuredAt": "2026-09-15T08:00:00Z", "weightKg": 70.0},
        {"measuredAt": "2026-09-15T20:00:00Z", "weightKg": 70.4},
    ],
])
def test_체중_차트는_측정일이_이틀_미만이면_그리지_않는다(items: list[dict]):
    chart = utils.body_weight_chart(items)

    assert chart is None


def test_BMI_차트는_체중을_키로_나눈_추이를_그린다(body_weights: list[dict]):
    items = body_weights

    chart = utils.bmi_chart(items, height_cm=170)

    assert chart["metric"] == "bmi"
    assert chart["x"] == ["9/15", "9/20"]
    assert chart["series"] == [{"name": "BMI", "values": [24.4, 24.2]}]


@pytest.mark.parametrize("height_cm", [None, 0])
def test_BMI_차트는_키가_없으면_그리지_않는다(body_weights: list[dict], height_cm):
    chart = utils.bmi_chart(body_weights, height_cm)

    assert chart is None


def test_BMI_차트는_체중_차트가_없으면_그리지_않는다(body_weights: list[dict]):
    items = body_weights[:1]

    chart = utils.bmi_chart(items, height_cm=170)

    assert chart is None


def test_PR_차트는_세션별_최대_추정_1RM_추이를_그린다(bench_sets: list[dict]):
    sets = bench_sets

    chart = utils.exercise_pr_chart(sets, "벤치프레스")

    assert chart == {
        "chartType": ChartType.LINE.value,
        "metric": "exercisePr",
        "title": "벤치프레스 추정 1RM 추이",
        "xLabel": "날짜",
        "yLabel": "추정 1RM(kg)",
        "x": ["9/15", "9/22"],
        "series": [{"name": "벤치프레스", "values": [93.3, 99.2]}],
    }


@pytest.mark.parametrize("record", [
    {"weight": 200.0, "reps": 13},
    {"weight": 0, "reps": 5},
    {"weight": None, "reps": 5},
    {"weight": 200.0, "reps": 0},
    {"weight": 200.0, "reps": None},
])
def test_PR_차트는_12렙_초과나_무게_렙이_없는_세트를_제외한다(bench_sets: list[dict], record: dict):
    sets = bench_sets + [{"workoutId": "w2", "performedAt": "2026-09-22T09:00:00Z", **record}]

    chart = utils.exercise_pr_chart(sets, "벤치프레스")

    assert chart["series"][0]["values"] == [93.3, 99.2]


def test_PR_차트는_세션_id가_없으면_수행_시각으로_세션을_구분한다(bench_sets: list[dict]):
    sets = [{**record, "workoutId": None} for record in bench_sets]

    chart = utils.exercise_pr_chart(sets, "벤치프레스")

    assert chart["x"] == ["9/15", "9/22"]
    assert chart["series"][0]["values"] == [93.3, 99.2]


def test_PR_차트는_유효한_세션이_둘_미만이면_그리지_않는다(bench_sets: list[dict]):
    sets = [record for record in bench_sets if record["reps"] <= 12 and record["workoutId"] == "w1"]

    chart = utils.exercise_pr_chart(sets, "벤치프레스")

    assert chart is None
