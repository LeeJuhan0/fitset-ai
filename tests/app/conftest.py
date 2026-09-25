import uuid

import pytest

from app.exercises.models import Equipment, Exercise


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
