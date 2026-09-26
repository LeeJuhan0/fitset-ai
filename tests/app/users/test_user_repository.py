import pytest

from app.users import repository
from app.users.exceptions import UserNotFoundError
from app.users.models import UserProfile
from tests.conftest import USER_ID


@pytest.mark.usefixtures("body_weights", "avoided_quadriceps")
def test_프로필은_키_최신_체중_기피_부위와_camelCase_열거형을_준다():
    profile = repository.get_profile(USER_ID)

    assert profile == {
        "heightCm": 175.0,
        "weightKg": 70.8,
        "gender": "MALE",
        "birthDate": "1995-01-01",
        "goal": "hypertrophy",
        "level": "beginner",
        "avoidBodyParts": ["quadriceps"],
    }


@pytest.mark.usefixtures("user_profile")
def test_프로필은_체중_기록과_기피_부위가_없으면_비워서_준다():
    profile = repository.get_profile(USER_ID)

    assert (profile["weightKg"], profile["avoidBodyParts"]) == (None, [])


@pytest.mark.usefixtures("user_profile", "other_user_body_weight", "other_user_avoided_chest")
def test_프로필은_다른_사용자의_체중과_기피_부위를_섞지_않는다():
    profile = repository.get_profile(USER_ID)

    assert (profile["weightKg"], profile["avoidBodyParts"]) == (None, [])


@pytest.mark.parametrize("goal, level, expected", [
    ("WEIGHT_LOSS", "INTERMEDIATE", ("weightLoss", "intermediate")),
    ("STRENGTH", "ADVANCED", ("strength", "advanced")),
])
def test_프로필은_목표와_수준을_camelCase_로_바꾼다(seed, user_profile: UserProfile, goal: str, level: str, expected: tuple):
    user_profile.workout_goal = goal
    user_profile.level = level
    seed(user_profile)

    profile = repository.get_profile(USER_ID)

    assert (profile["goal"], profile["level"]) == expected


def test_프로필이_없으면_사용자_없음_예외를_던진다():
    with pytest.raises(UserNotFoundError):
        repository.get_profile(USER_ID)


@pytest.mark.usefixtures("body_weights", "other_user_body_weight")
def test_체중_이력은_기간_안_측정값을_측정_시각_오름차순으로_준다():
    weights = repository.get_body_weights(USER_ID, days=30)

    assert weights == [
        {"measuredAt": "2026-09-05T03:00:00Z", "weightKg": 72.0},
        {"measuredAt": "2026-09-20T03:00:00Z", "weightKg": 71.35},
        {"measuredAt": "2026-09-24T03:00:00Z", "weightKg": 70.8},
    ]


@pytest.mark.usefixtures("body_weights")
def test_체중_이력은_조회_기간을_넓히면_오래된_측정값도_준다():
    weights = repository.get_body_weights(USER_ID, days=60)

    assert [weight["weightKg"] for weight in weights] == [74.0, 72.0, 71.35, 70.8]


@pytest.mark.usefixtures("other_user_body_weight")
def test_체중_이력은_측정값이_없으면_비어_있다():
    weights = repository.get_body_weights(USER_ID, days=30)

    assert weights == []
