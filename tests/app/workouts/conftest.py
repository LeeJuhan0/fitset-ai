import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from app.clients import mysql
from app.exercises.models import Exercise
from app.workouts.models import WorkoutHistory, WorkoutHistoryExercise, WorkoutHistorySet
from tests.conftest import NOW, USER_ID

OTHER_USER_ID = "22222222-2222-2222-2222-222222222222"


def _session(
    entries: list[tuple[Exercise, list[tuple[float, int, int, int]]]],
    days_ago: int,
    user_id: str = USER_ID,
    active_seconds: int = 3000,
) -> list:
    """여러 종목 세트 묶은 운동 세션 행 목록, 세트는 역순 삽입"""
    started_at = (NOW - timedelta(days=days_ago)).replace(tzinfo=None)
    history = WorkoutHistory(
        id=uuid.uuid4().bytes,
        user_id=mysql.uuid_bytes(user_id),
        started_at=started_at,
        ended_at=started_at + timedelta(hours=1),
        active_duration_seconds=active_seconds,
    )
    rows = [history]
    for order, (exercise, sets) in enumerate(entries):
        entry = WorkoutHistoryExercise(
            id=uuid.uuid4().bytes, workout_history_id=history.id, exercise_id=exercise.id, order_index=order,
        )
        rows.append(entry)
        for index, (weight, reps, duration, rest) in reversed(list(enumerate(sets))):
            rows.append(WorkoutHistorySet(
                id=uuid.uuid4().bytes,
                workout_exercise_history_id=entry.id,
                order_index=index,
                duration_seconds=duration,
                rest_seconds=rest,
                weight=Decimal(str(weight)),
                reps=reps,
            ))
    return rows


@pytest.fixture
def bench_press(seed, barbell):
    """바벨 벤치프레스"""
    exercise = Exercise(
        id=uuid.uuid4().bytes,
        name="벤치프레스",
        thumbnail_key="thumbnails/bench-press.webp",
        video_key="videos/bench-press.mp4",
        equipment_id=barbell.id,
        difficulty="BEGINNER",
        exercise_type="WEIGHT_AND_REPS",
        instructions=[],
    )
    return seed(exercise)[0]


@pytest.fixture
def full_body_workout(seed, barbell_squat, push_up, bench_press):
    """어제 스쿼트 두 세트, 무게 없는 푸시업, 세트 없는 벤치 세션"""
    rows = _session(
        [
            (barbell_squat, [(100, 5, 0, 90), (102.5, 3, 0, 120)]),
            (push_up, [(0, 15, 0, 0)]),
            (bench_press, []),
        ],
        days_ago=1,
    )
    return seed(*rows)


@pytest.fixture
def old_squat_workout(seed, barbell_squat):
    """40일 전 바벨 스쿼트 세션"""
    return seed(*_session([(barbell_squat, [(80, 5, 0, 90)])], days_ago=40, active_seconds=2400))


@pytest.fixture
def other_user_squat_workout(seed, barbell_squat):
    """다른 사용자의 어제 바벨 스쿼트 세션"""
    return seed(*_session([(barbell_squat, [(140, 5, 0, 90)])], days_ago=1, user_id=OTHER_USER_ID))
