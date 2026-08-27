"""백엔드 DB 직조회 repository 테스트 — SQLite 인메모리로 실제 쿼리를 돈다.

검증 대상은 구 내부 API와의 형태 계약이다: camelCase 키, UUID 문자열, Z 접미 시각,
0 → null 변환(무게·수행·휴식), enum 표기 변환(UPPER_SNAKE → camelCase), 중첩 구조.
소비자(routines·charts·툴)는 출처 교체를 모르므로 이 형태가 무너지면 안 된다.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from app.clients import mysql
from app.core.errors import ExerciseNotFoundError, UserNotFoundError
from app.core.orm import BackendBase
from app.exercises import repository as exercises_repository
from app.exercises.domain import Equipment, Exercise, ExerciseMuscle, Muscle
from app.users import repository as users_repository
from app.users.domain import BodyWeightHistory, UserAvoidedMuscle, UserProfile
from app.workouts import repository as workouts_repository
from app.workouts.domain import WorkoutHistory, WorkoutHistoryExercise, WorkoutHistorySet

USER_ID = "11111111-1111-1111-1111-111111111111"
# DB 저장 형식과 동일한 naive UTC — repository의 _cutoff 비교 대상
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _uid(seed: str) -> bytes:
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).bytes


@pytest.fixture
def db(monkeypatch):
    """SQLite 인메모리에 스키마를 만들고 fetch_all이 그 엔진을 쓰게 한다."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    BackendBase.metadata.create_all(engine)
    monkeypatch.setattr(mysql, "_engine", lambda: engine)

    from sqlalchemy.orm import Session
    with Session(engine) as session:
        user = mysql.uuid_bytes(USER_ID)
        session.add_all([
            # slug는 미디어 키 파일명에서 유도된다 (#96) — 시드도 실 DB 키 형태로 넣는다
            Equipment(id=_uid("eq"), thumbnail_key="equipments/barbell.webp", name="바벨"),
            Muscle(id=_uid("chest"), thumbnail_key="muscles/chest.webp", name="가슴"),
            Muscle(id=_uid("triceps"), thumbnail_key="muscles/triceps.webp", name="삼두근"),
            Exercise(
                id=_uid("bench"), thumbnail_key="thumbnails/barbell-bench-press.webp",
                video_key="videos/barbell-bench-press.mp4", name="바벨 벤치프레스",
                equipment_id=_uid("eq"), difficulty="INTERMEDIATE",
                exercise_type="WEIGHT_AND_REPS",
                # 실 DB 형태 — {"content", "stepOrder"} dict 배열 (prod 실측 2026-08-12)
                instructions=[
                    {"content": "민다", "stepOrder": 2},
                    {"content": "눕는다", "stepOrder": 1},
                ],
            ),
            ExerciseMuscle(id=_uid("em1"), exercise_id=_uid("bench"), muscle_id=_uid("chest"), role="PRIMARY"),
            ExerciseMuscle(id=_uid("em2"), exercise_id=_uid("bench"), muscle_id=_uid("triceps"), role="SECONDARY"),
            UserProfile(
                id=_uid("profile"), user_id=user, gender="MALE",
                birth_date=datetime(1998, 4, 12).date(), height=175.0,
                workout_goal="WEIGHT_LOSS", level="INTERMEDIATE",
            ),
            UserAvoidedMuscle(id=_uid("avoid"), user_id=user, muscle_id=_uid("triceps")),
            BodyWeightHistory(id=_uid("bw1"), user_id=user, weight=74.5, measured_at=NOW - timedelta(days=20)),
            BodyWeightHistory(id=_uid("bw2"), user_id=user, weight=72.4, measured_at=NOW - timedelta(days=1)),
            WorkoutHistory(
                # 활동 시간은 active_duration 컬럼에 직접 저장된다 (경과 3600 중 3180초)
                id=_uid("w1"), user_id=user,
                started_at=NOW - timedelta(days=2), ended_at=NOW - timedelta(days=2) + timedelta(hours=1),
                active_duration_seconds=3180,
            ),
            WorkoutHistoryExercise(id=_uid("we1"), workout_history_id=_uid("w1"), exercise_id=_uid("bench"), order_index=0),
            WorkoutHistorySet(
                id=_uid("s1"), workout_exercise_history_id=_uid("we1"), order_index=0,
                duration_seconds=32, rest_seconds=90, weight=60, reps=10,
            ),
            WorkoutHistorySet(
                # 0 = 미기록·맨몸·마지막 세트 — 와이어에서 null이 돼야 한다
                id=_uid("s2"), workout_exercise_history_id=_uid("we1"), order_index=1,
                duration_seconds=0, rest_seconds=0, weight=0, reps=12,
            ),
        ])
        session.commit()
    return engine


def test_get_profile_matches_old_wire_shape(db):
    profile = users_repository.get_profile(USER_ID)
    assert profile["heightCm"] == 175.0
    assert profile["weightKg"] == 72.4               # body_weight_log 최신 측정값
    assert profile["gender"] == "MALE"
    assert profile["birthDate"] == "1998-04-12"
    assert profile["goal"] == "weightLoss"           # WEIGHT_LOSS → camelCase (구 API 책임 승계)
    assert profile["level"] == "intermediate"
    assert profile["avoidBodyParts"] == ["triceps"]  # muscle slug


def test_get_profile_unknown_user_raises_404(db):
    with pytest.raises(UserNotFoundError):
        users_repository.get_profile("99999999-9999-9999-9999-999999999999")


def test_get_body_weights_ascending_window(db):
    items = users_repository.get_body_weights(USER_ID, days=30)
    assert [item["weightKg"] for item in items] == [74.5, 72.4]   # 오름차순
    assert items[0]["measuredAt"].endswith("Z")
    assert users_repository.get_body_weights(USER_ID, days=7) == [
        {"measuredAt": items[1]["measuredAt"], "weightKg": 72.4}
    ]


def test_get_recent_workouts_nested_shape_and_zero_to_null(db):
    sessions = workouts_repository.get_recent_workouts(USER_ID, days=7)
    assert len(sessions) == 1
    session = sessions[0]
    assert set(session) == {"id", "startedAt", "endedAt", "exercises"}
    exercise = session["exercises"][0]
    assert exercise["slug"] == "barbell-bench-press"
    assert exercise["exerciseName"] == "바벨 벤치프레스"
    assert exercise["orderIndex"] == 0
    first, second = exercise["sets"]
    assert first == {"id": first["id"], "orderIndex": 0, "weight": 60.0, "reps": 10,
                     "durationSec": 32, "restSec": 90}
    # 0은 전부 null로 — 맨몸 무게·미기록 시간·마지막 세트 휴식
    assert second["weight"] is None
    assert second["durationSec"] is None
    assert second["restSec"] is None
    assert second["reps"] == 12


def test_get_exercise_sets_flat_ascending(db):
    rows = workouts_repository.get_exercise_sets(USER_ID, "barbell-bench-press", days=7)
    assert [row["orderIndex"] for row in rows] == [0, 1]
    assert rows[0]["performedAt"].endswith("Z")
    assert rows[0]["weight"] == 60.0
    assert rows[1]["weight"] is None
    assert uuid.UUID(rows[0]["workoutId"])   # UUID 문자열


def test_get_workout_sessions_meta_only(db):
    sessions = workouts_repository.get_workout_sessions(USER_ID, days=7)
    assert sessions == [{
        "id": sessions[0]["id"],
        "startedAt": sessions[0]["startedAt"],
        "endedAt": sessions[0]["endedAt"],
        "activeDurationSeconds": 3180,
    }]


def test_get_exercise_detail_with_muscle_roles(db, monkeypatch):
    monkeypatch.setattr(exercises_repository, "cdn_thumbnail_url", lambda slug: "https://cdn/thumb.jpg")
    monkeypatch.setattr(exercises_repository, "cdn_video_url", lambda slug: "https://cdn/guide.mp4")
    detail = exercises_repository.get_exercise("barbell-bench-press")
    assert detail["name"] == "바벨 벤치프레스"
    assert detail["primaryMuscles"] == ["chest"]
    assert detail["secondaryMuscles"] == ["triceps"]
    assert detail["equipment"] == "barbell"
    assert detail["difficulty"] == "intermediate"    # INTERMEDIATE → camelCase
    # dict 배열이 stepOrder 순 문자열 배열로 정규화된다 — 구 §4.3 Array<String> 계약
    assert detail["instructions"] == ["눕는다", "민다"]
    assert detail["thumbnailUrl"] == "https://cdn/thumb.jpg"


def test_get_exercise_unknown_slug_raises_404(db):
    with pytest.raises(ExerciseNotFoundError):
        exercises_repository.get_exercise("no-such-exercise")
