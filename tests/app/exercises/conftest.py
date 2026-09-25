import uuid

import pytest

from app.core.orm import BackendBase
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


@pytest.fixture
def barbell_row_without_video(seed, barbell):
    """가이드 영상이 비어 있는 바벨 로우"""
    exercise = Exercise(
        id=uuid.uuid4().bytes,
        name="바벨 로우",
        thumbnail_key="thumbnails/barbell-row.webp",
        video_key="",
        equipment_id=barbell.id,
        difficulty="INTERMEDIATE",
        exercise_type="WEIGHT_AND_REPS",
        instructions=[],
    )
    return seed(exercise)[0]


@pytest.fixture
def mysql_down(mysql_engine):
    """백엔드 테이블 제거로 종목 조회 장애 재현"""
    BackendBase.metadata.drop_all(mysql_engine)
