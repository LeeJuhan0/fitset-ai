import pytest

from app.clients import mysql
from app.workouts import repository
from tests.conftest import USER_ID


@pytest.mark.usefixtures("squat_workout")
def test_최근_운동_기록은_세션별로_종목과_세트를_중첩해_최신순으로_준다(full_body_workout: list):
    session_row, squat_entry, *_ = full_body_workout

    workouts = repository.get_recent_workouts(USER_ID, days=30)

    assert [workout["startedAt"] for workout in workouts] == ["2026-09-24T03:00:00Z", "2026-09-23T03:00:00Z"]
    latest = workouts[0]
    assert latest["id"] == mysql.uuid_str(session_row.id)
    assert latest["endedAt"] == "2026-09-24T04:00:00Z"
    assert latest["exercises"][0] == {
        "id": mysql.uuid_str(squat_entry.id),
        "slug": "barbell-squat",
        "exerciseName": "바벨 스쿼트",
        "orderIndex": 0,
        "sets": [
            {"id": latest["exercises"][0]["sets"][0]["id"], "orderIndex": 0, "weight": 100.0, "reps": 5, "durationSec": None, "restSec": 90},
            {"id": latest["exercises"][0]["sets"][1]["id"], "orderIndex": 1, "weight": 102.5, "reps": 3, "durationSec": None, "restSec": 120},
        ],
    }


@pytest.mark.usefixtures("full_body_workout")
def test_최근_운동_기록은_종목을_수행_순서대로_준다():
    workouts = repository.get_recent_workouts(USER_ID, days=30)

    exercises = workouts[0]["exercises"]
    assert [exercise["slug"] for exercise in exercises] == ["barbell-squat", "push-up", "bench-press"]
    assert [exercise["orderIndex"] for exercise in exercises] == [0, 1, 2]


@pytest.mark.usefixtures("full_body_workout")
def test_최근_운동_기록은_기본값_0인_무게_시간_휴식을_미기록으로_준다():
    workouts = repository.get_recent_workouts(USER_ID, days=30)

    push_up_set = workouts[0]["exercises"][1]["sets"][0]
    assert push_up_set["reps"] == 15
    assert (push_up_set["weight"], push_up_set["durationSec"], push_up_set["restSec"]) == (None, None, None)


@pytest.mark.usefixtures("full_body_workout")
def test_최근_운동_기록은_세트가_없는_종목도_빈_세트로_준다():
    workouts = repository.get_recent_workouts(USER_ID, days=30)

    bench = workouts[0]["exercises"][2]
    assert (bench["slug"], bench["sets"]) == ("bench-press", [])


@pytest.mark.usefixtures("full_body_workout", "old_squat_workout", "deleted_squat_workout", "other_user_squat_workout")
def test_최근_운동_기록은_기간_밖_삭제된_남의_기록을_빼고_준다():
    workouts = repository.get_recent_workouts(USER_ID, days=30)

    assert [workout["startedAt"] for workout in workouts] == ["2026-09-24T03:00:00Z"]


@pytest.mark.usefixtures("old_squat_workout", "deleted_squat_workout", "other_user_squat_workout")
def test_최근_운동_기록은_해당하는_세션이_없으면_비어_있다():
    workouts = repository.get_recent_workouts(USER_ID, days=30)

    assert workouts == []


@pytest.mark.usefixtures("full_body_workout", "old_squat_workout")
def test_최근_운동_기록은_조회_기간을_넓히면_오래된_세션도_준다():
    workouts = repository.get_recent_workouts(USER_ID, days=60)

    assert [workout["startedAt"] for workout in workouts] == ["2026-09-24T03:00:00Z", "2026-08-16T03:00:00Z"]
