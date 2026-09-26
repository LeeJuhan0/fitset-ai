import pytest

from app.clients import mysql
from app.workouts import repository
from tests.conftest import USER_ID


@pytest.mark.usefixtures("full_body_workout")
def test_종목별_세트는_세션을_가로질러_수행_시각과_세트_순서대로_준다(squat_workout: list):
    session_row = squat_workout[0]

    sets = repository.get_exercise_sets(USER_ID, "barbell-squat", days=30)

    assert [(record["performedAt"], record["orderIndex"]) for record in sets] == [
        ("2026-09-23T03:00:00Z", 0),
        ("2026-09-23T03:00:00Z", 1),
        ("2026-09-23T03:00:00Z", 2),
        ("2026-09-24T03:00:00Z", 0),
        ("2026-09-24T03:00:00Z", 1),
    ]
    assert sets[0] == {
        "workoutId": mysql.uuid_str(session_row.id),
        "performedAt": "2026-09-23T03:00:00Z",
        "orderIndex": 0,
        "weight": 100.0,
        "reps": 5,
        "durationSec": None,
        "restSec": 90,
    }


@pytest.mark.usefixtures("full_body_workout")
def test_종목별_세트는_다른_종목_세트를_섞지_않는다():
    sets = repository.get_exercise_sets(USER_ID, "push-up", days=30)

    assert [(record["weight"], record["reps"]) for record in sets] == [(None, 15)]


@pytest.mark.usefixtures("full_body_workout", "old_squat_workout", "deleted_squat_workout", "other_user_squat_workout")
def test_종목별_세트는_기간_밖_삭제된_남의_기록을_빼고_준다():
    sets = repository.get_exercise_sets(USER_ID, "barbell-squat", days=30)

    assert [record["weight"] for record in sets] == [100.0, 102.5]


@pytest.mark.usefixtures("full_body_workout")
def test_종목별_세트는_기록이_없는_종목이면_비어_있다():
    sets = repository.get_exercise_sets(USER_ID, "bench-press", days=30)

    assert sets == []


@pytest.mark.usefixtures("squat_workout")
def test_세션_요약은_시작_시각_오름차순으로_활동_시간과_함께_준다(full_body_workout: list):
    session_row = full_body_workout[0]

    sessions = repository.get_workout_sessions(USER_ID, days=30)

    assert [(session["startedAt"], session["activeDurationSeconds"]) for session in sessions] == [
        ("2026-09-23T03:00:00Z", 3600),
        ("2026-09-24T03:00:00Z", 3000),
    ]
    assert sessions[1] == {
        "id": mysql.uuid_str(session_row.id),
        "startedAt": "2026-09-24T03:00:00Z",
        "endedAt": "2026-09-24T04:00:00Z",
        "activeDurationSeconds": 3000,
    }


@pytest.mark.usefixtures("full_body_workout", "old_squat_workout", "deleted_squat_workout", "other_user_squat_workout")
def test_세션_요약은_기간_밖_삭제된_남의_세션을_빼고_준다():
    sessions = repository.get_workout_sessions(USER_ID, days=30)

    assert [session["startedAt"] for session in sessions] == ["2026-09-24T03:00:00Z"]


@pytest.mark.usefixtures("full_body_workout", "old_squat_workout")
def test_세션_요약은_조회_기간을_넓히면_오래된_세션도_준다():
    sessions = repository.get_workout_sessions(USER_ID, days=60)

    assert [session["activeDurationSeconds"] for session in sessions] == [2400, 3000]
