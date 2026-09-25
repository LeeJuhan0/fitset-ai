import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.clients import mysql
from app.exercises.models import Equipment, Exercise, Muscle
from app.users.models import UserAvoidedMuscle, UserProfile
from app.workouts.models import WorkoutHistory, WorkoutHistoryExercise, WorkoutHistorySet
from tests.conftest import NOW, USER_ID


def _workout(exercise: Exercise, sets: list[tuple[float, int]], days_ago: int) -> list:
    """종목 하나짜리 운동 기록 세션 행 목록"""
    started_at = (NOW - timedelta(days=days_ago)).replace(tzinfo=None)
    history = WorkoutHistory(
        id=uuid.uuid4().bytes,
        user_id=mysql.uuid_bytes(USER_ID),
        started_at=started_at,
        ended_at=started_at + timedelta(hours=1),
        active_duration_seconds=3600,
    )
    entry = WorkoutHistoryExercise(
        id=uuid.uuid4().bytes, workout_history_id=history.id, exercise_id=exercise.id, order_index=0,
    )
    set_rows = [
        WorkoutHistorySet(
            id=uuid.uuid4().bytes,
            workout_exercise_history_id=entry.id,
            order_index=index,
            duration_seconds=0,
            rest_seconds=90,
            weight=Decimal(str(weight)),
            reps=reps,
        )
        for index, (weight, reps) in enumerate(sets)
    ]
    return [history, entry, *set_rows]


@pytest.fixture
def barbell(seed):
    """바벨 장비"""
    equipment = Equipment(id=uuid.uuid4().bytes, thumbnail_key="equipments/barbell.webp", name="바벨")
    return seed(equipment)[0]


@pytest.fixture
def barbell_squat(seed, barbell):
    """가이드 영상이 등록된 바벨 스쿼트"""
    exercise = Exercise(
        id=uuid.uuid4().bytes,
        name="바벨 스쿼트",
        thumbnail_key="thumbnails/barbell-squat.webp",
        video_key="videos/barbell-squat.mp4",
        equipment_id=barbell.id,
        difficulty="BEGINNER",
        exercise_type="WEIGHT_AND_REPS",
        instructions=[],
    )
    return seed(exercise)[0]


@pytest.fixture
def bodyweight(seed):
    """맨몸 장비"""
    equipment = Equipment(id=uuid.uuid4().bytes, thumbnail_key="equipments/bodyweight.webp", name="맨몸")
    return seed(equipment)[0]


@pytest.fixture
def push_up(seed, bodyweight):
    """맨몸 렙 종목 푸시업"""
    exercise = Exercise(
        id=uuid.uuid4().bytes,
        name="푸시업",
        thumbnail_key="thumbnails/push-up.webp",
        video_key="videos/push-up.mp4",
        equipment_id=bodyweight.id,
        difficulty="BEGINNER",
        exercise_type="REPS_ONLY",
        instructions=[],
    )
    return seed(exercise)[0]


@pytest.fixture
def user_profile(seed):
    """근비대 목표 초급 유저 프로필"""
    profile = UserProfile(
        id=uuid.uuid4().bytes,
        user_id=mysql.uuid_bytes(USER_ID),
        gender="MALE",
        birth_date=date(1995, 1, 1),
        height=Decimal("175.0"),
        workout_goal="HYPERTROPHY",
        level="BEGINNER",
    )
    return seed(profile)[0]


@pytest.fixture
def squat_workout(seed, barbell_squat):
    """이틀 전 바벨 스쿼트 100kg 5회 3세트 기록"""
    return seed(*_workout(barbell_squat, [(100, 5)] * 3, days_ago=2))


@pytest.fixture
def push_up_workouts(seed, push_up):
    """최근 3회 푸시업만 한 홈트 기록"""
    rows = [row for days_ago in (1, 3, 5) for row in _workout(push_up, [(0, 15)] * 3, days_ago)]
    return seed(*rows)


@pytest.fixture
def quadriceps(seed):
    """대퇴사두근 근육"""
    muscle = Muscle(id=uuid.uuid4().bytes, thumbnail_key="muscles/quadriceps.webp", name="대퇴사두근")
    return seed(muscle)[0]


@pytest.fixture
def avoided_quadriceps(seed, user_profile, quadriceps):
    """프로필에 대퇴사두근 기피 등록"""
    avoided = UserAvoidedMuscle(
        id=uuid.uuid4().bytes, user_id=mysql.uuid_bytes(USER_ID), muscle_id=quadriceps.id,
    )
    return seed(avoided)[0]


@pytest.fixture
def deleted_squat_workout(seed, barbell_squat):
    """사용자가 삭제한 바벨 스쿼트 100kg 5회 3세트 기록"""
    rows = _workout(barbell_squat, [(100, 5)] * 3, days_ago=2)
    rows[0].deleted_at = (NOW - timedelta(days=1)).replace(tzinfo=None)
    return seed(*rows)
