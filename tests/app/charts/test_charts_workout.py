import pytest

from app.charts import utils
from app.charts.enums import ChartType

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def test_운동_시간_차트는_주별_평균_운동_시간을_분_단위로_그린다(workout_sessions: list[dict]):
    sessions = workout_sessions

    chart = utils.workout_duration_chart(sessions)

    assert chart == {
        "chartType": ChartType.LINE.value,
        "metric": "workoutDuration",
        "title": "주별 평균 운동 시간",
        "xLabel": "주차",
        "yLabel": "운동 시간(분)",
        "x": ["9/14", "9/21"],
        "series": [{"name": "운동 시간", "values": [45, 50]}],
    }


@pytest.mark.parametrize("broken", [
    {"startedAt": None, "activeDurationSeconds": 600},
    {"startedAt": "2026-09-15T09:00:00Z", "activeDurationSeconds": 0},
    {"startedAt": "2026-09-15T09:00:00Z", "activeDurationSeconds": None},
])
def test_운동_시간_차트는_시각이나_운동_시간이_없는_세션을_건너뛴다(workout_sessions: list[dict], broken: dict):
    sessions = workout_sessions + [broken]

    chart = utils.workout_duration_chart(sessions)

    assert chart["series"][0]["values"] == [45, 50]


@pytest.mark.parametrize("sessions", [
    [],
    [{"startedAt": "2026-09-14T09:00:00Z", "activeDurationSeconds": 3600}],
])
def test_운동_시간_차트는_기록된_주가_둘_미만이면_그리지_않는다(sessions: list[dict]):
    chart = utils.workout_duration_chart(sessions)

    assert chart is None


def test_운동_빈도_차트는_주별_세션_수를_막대로_그린다(workout_sessions: list[dict]):
    sessions = workout_sessions

    chart = utils.workout_frequency_chart(sessions)

    assert chart["chartType"] == ChartType.BAR.value
    assert chart["metric"] == "workoutFrequency"
    assert chart["x"] == ["9/14", "9/21"]
    assert chart["series"] == [{"name": "운동 횟수", "values": [2, 1]}]


def test_운동_빈도_차트는_일요일_세션을_그_주_월요일에_묶는다(workout_sessions: list[dict]):
    sessions = workout_sessions + [{"startedAt": "2026-09-20T23:59:59Z"}]

    chart = utils.workout_frequency_chart(sessions)

    assert chart["x"] == ["9/14", "9/21"]
    assert chart["series"][0]["values"] == [3, 1]


def test_운동_빈도_차트는_한_주만_있어도_그린다(workout_sessions: list[dict]):
    sessions = workout_sessions[:1]

    chart = utils.workout_frequency_chart(sessions)

    assert chart["x"] == ["9/14"]
    assert chart["series"][0]["values"] == [1]


@pytest.mark.parametrize("sessions", [[], [{"startedAt": None}]])
def test_운동_빈도_차트는_세션이_없으면_그리지_않는다(sessions: list[dict]):
    chart = utils.workout_frequency_chart(sessions)

    assert chart is None


def test_요일별_차트는_월요일부터_일요일까지_7칸을_센다(workout_sessions: list[dict]):
    sessions = workout_sessions + [{"startedAt": None}]

    chart = utils.weekday_frequency_chart(sessions)

    assert chart["metric"] == "weekdayFrequency"
    assert chart["x"] == WEEKDAYS
    assert chart["series"][0]["values"] == [2, 0, 1, 0, 0, 0, 0]


@pytest.mark.parametrize("sessions", [[], [{"startedAt": "invalid"}]])
def test_요일별_차트는_세션이_없으면_그리지_않는다(sessions: list[dict]):
    chart = utils.weekday_frequency_chart(sessions)

    assert chart is None


def test_부위_볼륨_차트는_주동근_주별_볼륨_합계를_그린다(recent_workouts: list[dict], exercise_meta: dict):
    workouts = recent_workouts

    chart = utils.muscle_volume_chart(workouts, exercise_meta, "chest")

    assert chart == {
        "chartType": ChartType.LINE.value,
        "metric": "muscleVolume",
        "title": "가슴 볼륨 추이",
        "xLabel": "주차",
        "yLabel": "볼륨(kg)",
        "x": ["9/14", "9/21"],
        "series": [{"name": "가슴", "values": [1000, 1400]}],
    }


def test_부위_볼륨_차트는_slug_대신_exerciseId로도_종목을_찾는다(recent_workouts: list[dict], exercise_meta: dict):
    for session in recent_workouts:
        for exercise in session["exercises"]:
            exercise["exerciseId"] = exercise.pop("slug")

    chart = utils.muscle_volume_chart(recent_workouts, exercise_meta, "chest")

    assert chart["series"][0]["values"] == [1000, 1400]


@pytest.mark.parametrize("muscle", ["quadriceps", "core", "calves"])
def test_부위_볼륨_차트는_볼륨_기록이_두_주_미만이면_그리지_않는다(
    recent_workouts: list[dict],
    exercise_meta: dict,
    muscle: str,
):
    chart = utils.muscle_volume_chart(recent_workouts, exercise_meta, muscle)

    assert chart is None


def test_부위_밸런스_차트는_부위별_볼륨을_내림차순으로_그린다(recent_workouts: list[dict], exercise_meta: dict):
    workouts = recent_workouts

    chart = utils.muscle_balance_chart(workouts, exercise_meta)

    assert chart["chartType"] == ChartType.BAR.value
    assert chart["metric"] == "muscleBalance"
    assert chart["x"] == ["가슴", "삼두근", "대퇴사두근", "둔근"]
    assert chart["series"][0]["values"] == [2400, 2400, 1500, 1500]


def test_부위_밸런스_차트는_상위_N개만_그린다(recent_workouts: list[dict], exercise_meta: dict):
    chart = utils.muscle_balance_chart(recent_workouts, exercise_meta, top_n=2)

    assert chart["x"] == ["가슴", "삼두근"]


def test_부위_밸런스_차트는_카탈로그에_없는_종목을_건너뛴다(recent_workouts: list[dict]):
    meta = {}

    chart = utils.muscle_balance_chart(recent_workouts, meta)

    assert chart is None


def test_상위_종목_차트는_세트_수_내림차순으로_그린다(recent_workouts: list[dict], exercise_meta: dict):
    workouts = recent_workouts

    chart = utils.top_exercises_chart(workouts, exercise_meta)

    assert chart["chartType"] == ChartType.BAR.value
    assert chart["metric"] == "topExercises"
    assert chart["x"] == ["벤치프레스", "바벨 스쿼트", "플랭크"]
    assert chart["series"][0]["values"] == [4.0, 3.0, 2.0]


@pytest.mark.parametrize("meta, expected_name", [
    ({"plank": {"name_ko": "플랭크"}}, "플랭크"),
    ({}, "plank"),
])
def test_상위_종목_차트는_종목명이_없으면_카탈로그명_다음_slug를_쓴다(
    recent_workouts: list[dict],
    meta: dict,
    expected_name: str,
):
    del recent_workouts[1]["exercises"][1]["exerciseName"]

    chart = utils.top_exercises_chart(recent_workouts, meta)

    assert chart["x"][-1] == expected_name


def test_상위_종목_차트는_상위_N개만_그린다(recent_workouts: list[dict], exercise_meta: dict):
    chart = utils.top_exercises_chart(recent_workouts, exercise_meta, top_n=1)

    assert chart["x"] == ["벤치프레스"]


@pytest.mark.parametrize("workouts", [[], [{"exercises": [{"slug": None, "sets": [{}]}]}]])
def test_상위_종목_차트는_종목이_없으면_그리지_않는다(workouts: list[dict], exercise_meta: dict):
    chart = utils.top_exercises_chart(workouts, exercise_meta)

    assert chart is None
