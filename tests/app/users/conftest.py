import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from app.clients import mysql
from app.exercises.models import Muscle
from app.users.models import BodyWeightHistory, UserAvoidedMuscle
from tests.conftest import NOW, USER_ID

OTHER_USER_ID = "22222222-2222-2222-2222-222222222222"


def _weight(weight: str, days_ago: int, user_id: str = USER_ID) -> BodyWeightHistory:
    """지정 일수 전 체중 측정 행"""
    return BodyWeightHistory(
        id=uuid.uuid4().bytes,
        user_id=mysql.uuid_bytes(user_id),
        weight=Decimal(weight),
        measured_at=(NOW - timedelta(days=days_ago)).replace(tzinfo=None),
    )


@pytest.fixture
def body_weights(seed):
    """최근 30일 세 번과 40일 전 한 번 잰 체중, 순서 섞어 삽입"""
    return seed(_weight("71.35", 5), _weight("74.00", 40), _weight("70.80", 1), _weight("72.00", 20))


@pytest.fixture
def other_user_body_weight(seed):
    """다른 사용자의 어제 체중"""
    return seed(_weight("90.00", 1, user_id=OTHER_USER_ID))[0]


@pytest.fixture
def chest(seed):
    """가슴 근육"""
    muscle = Muscle(id=uuid.uuid4().bytes, thumbnail_key="muscles/chest.webp", name="가슴")
    return seed(muscle)[0]


@pytest.fixture
def other_user_avoided_chest(seed, chest):
    """다른 사용자 프로필의 가슴 기피 등록"""
    avoided = UserAvoidedMuscle(id=uuid.uuid4().bytes, user_id=mysql.uuid_bytes(OTHER_USER_ID), muscle_id=chest.id)
    return seed(avoided)[0]
