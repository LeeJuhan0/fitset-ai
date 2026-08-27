"""직조회 repository 엣지 유즈케이스 — 빈 데이터·부분 데이터·기간 경계·정렬.

행복 경로 형태 계약은 test_backend_repositories.py가 맡고, 여기서는 소비자 로직이
None·빈 목록을 만나는 경계 상황이 구 내부 API와 같은 모양으로 떨어지는지 본다.
"""
from datetime import datetime, timedelta, timezone

from app.clients import mysql
from app.exercises.domain import Equipment, Exercise
from app.users import repository as users_repository
from app.users.domain import UserProfile
from app.workouts import repository as workouts_repository
from app.workouts.domain import WorkoutHistory, WorkoutHistoryExercise, WorkoutHistorySet
from tests.conftest import seed, uid

USER_ID = "11111111-1111-1111-1111-111111111111"
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _profile() -> UserProfile:
    return UserProfile(
        id=uid("p"), user_id=mysql.uuid_bytes(USER_ID), gender="FEMALE",
        birth_date=datetime(2000, 1, 1).date(), height=162.0,
        workout_goal="ENDURANCE", level="BEGINNER",
    )


def _bench() -> list:
    return [
        Equipment(id=uid("eq"), thumbnail_key="equipments/barbell.webp", name="바벨"),
        Exercise(id=uid("bench"), thumbnail_key="thumbnails/barbell-bench-press.webp",
                 video_key="videos/barbell-bench-press.mp4", name="바벨 벤치프레스",
                 equipment_id=uid("eq"), difficulty="INTERMEDIATE",
                 exercise_type="WEIGHT_AND_REPS", instructions=[]),
    ]


def test_profile_without_weight_log_returns_null_weight(backend_engine):
    # 체중 기록이 한 건도 없는 신규 유저 — weightKg는 null (무게 추천의 성별·체중 폴백 경로)
    seed(backend_engine, [_profile()])
    profile = users_repository.get_profile(USER_ID)
    assert profile["weightKg"] is None
    assert profile["avoidBodyParts"] == []
    assert profile["goal"] == "endurance"


def test_body_weights_outside_window_are_excluded(backend_engine):
    from app.users.domain import BodyWeightHistory

    seed(backend_engine, [_profile(), BodyWeightHistory(
        id=uid("bw"), user_id=mysql.uuid_bytes(USER_ID),
        weight=70.0, measured_at=NOW - timedelta(days=40),
    )])
    assert users_repository.get_body_weights(USER_ID, days=30) == []
    # 프로필의 최신 체중은 기간 제한이 없다 — 40일 전 기록도 최신값으로 쓴다
    assert users_repository.get_profile(USER_ID)["weightKg"] == 70.0


def test_recent_workouts_empty_when_no_sessions(backend_engine):
    assert workouts_repository.get_recent_workouts(USER_ID, days=28) == []
    assert workouts_repository.get_workout_sessions(USER_ID, days=28) == []
    assert workouts_repository.get_exercise_sets(USER_ID, "barbell-bench-press", days=28) == []


def test_recent_workouts_orders_sessions_latest_first(backend_engine):
    user = mysql.uuid_bytes(USER_ID)
    seed(backend_engine, [
        WorkoutHistory(id=uid("w-old"), user_id=user, started_at=NOW - timedelta(days=5),
                ended_at=NOW - timedelta(days=5) + timedelta(hours=1), active_duration_seconds=0),
        WorkoutHistory(id=uid("w-new"), user_id=user, started_at=NOW - timedelta(days=1),
                ended_at=NOW - timedelta(days=1) + timedelta(hours=1), active_duration_seconds=0),
    ])
    sessions = workouts_repository.get_recent_workouts(USER_ID, days=7)
    assert [s["id"] for s in sessions] == [mysql.uuid_str(uid("w-new")), mysql.uuid_str(uid("w-old"))]
    # 세션 요약은 반대로 시간 오름차순 — 차트 x축 기준 (구 §4.6)
    summaries = workouts_repository.get_workout_sessions(USER_ID, days=7)
    assert [s["id"] for s in summaries] == [mysql.uuid_str(uid("w-old")), mysql.uuid_str(uid("w-new"))]


def test_recent_workouts_window_excludes_old_sessions(backend_engine):
    user = mysql.uuid_bytes(USER_ID)
    seed(backend_engine, [
        WorkoutHistory(id=uid("w-in"), user_id=user, started_at=NOW - timedelta(days=2),
                ended_at=NOW - timedelta(days=2), active_duration_seconds=0),
        WorkoutHistory(id=uid("w-out"), user_id=user, started_at=NOW - timedelta(days=40),
                ended_at=NOW - timedelta(days=40), active_duration_seconds=0),
    ])
    sessions = workouts_repository.get_recent_workouts(USER_ID, days=28)
    assert [s["id"] for s in sessions] == [mysql.uuid_str(uid("w-in"))]


def test_exercise_without_sets_keeps_empty_list(backend_engine):
    # 세트를 기록하지 않고 종목만 담은 세션 — exercises 목록엔 나오되 sets는 빈 배열 (outer join)
    user = mysql.uuid_bytes(USER_ID)
    seed(backend_engine, [
        *_bench(),
        WorkoutHistory(id=uid("w"), user_id=user, started_at=NOW - timedelta(days=1),
                ended_at=NOW - timedelta(days=1), active_duration_seconds=0),
        WorkoutHistoryExercise(id=uid("we"), workout_history_id=uid("w"), exercise_id=uid("bench"), order_index=0),
    ])
    sessions = workouts_repository.get_recent_workouts(USER_ID, days=7)
    assert sessions[0]["exercises"][0]["slug"] == "barbell-bench-press"
    assert sessions[0]["exercises"][0]["sets"] == []


def test_exercise_sets_filters_other_users_and_slugs(backend_engine):
    user = mysql.uuid_bytes(USER_ID)
    other = mysql.uuid_bytes("99999999-9999-9999-9999-999999999999")
    seed(backend_engine, [
        *_bench(),
        WorkoutHistory(id=uid("mine"), user_id=user, started_at=NOW - timedelta(days=1),
                ended_at=NOW, active_duration_seconds=0),
        WorkoutHistory(id=uid("theirs"), user_id=other, started_at=NOW - timedelta(days=1),
                ended_at=NOW, active_duration_seconds=0),
        WorkoutHistoryExercise(id=uid("we-mine"), workout_history_id=uid("mine"), exercise_id=uid("bench"), order_index=0),
        WorkoutHistoryExercise(id=uid("we-theirs"), workout_history_id=uid("theirs"), exercise_id=uid("bench"), order_index=0),
        WorkoutHistorySet(id=uid("s-mine"), workout_exercise_history_id=uid("we-mine"), order_index=0,
                   duration_seconds=30, rest_seconds=60, weight=40, reps=10),
        WorkoutHistorySet(id=uid("s-theirs"), workout_exercise_history_id=uid("we-theirs"), order_index=0,
                   duration_seconds=30, rest_seconds=60, weight=100, reps=10),
    ])
    rows = workouts_repository.get_exercise_sets(USER_ID, "barbell-bench-press", days=7)
    # 남의 세션 세트(100kg)는 절대 섞이면 안 된다
    assert [row["weight"] for row in rows] == [40.0]


def test_exercise_detail_without_muscles_returns_empty_lists(backend_engine):
    from app.exercises import repository as exercises_repository

    seed(backend_engine, _bench())
    detail = exercises_repository.get_exercise("barbell-bench-press")
    assert detail["primaryMuscles"] == []
    assert detail["secondaryMuscles"] == []
    assert detail["instructions"] == []
