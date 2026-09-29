from app.memory.schemas import AvoidedExercise, Environment, UserPreferences
from app.memory.utils import render_preferences


def test_빈_편향은_문맥에_넣지_않는다():
    assert render_preferences(UserPreferences()) is None


def test_편향은_항목별_한_줄로_이유는_괄호로_쓴다():
    preferences = UserPreferences(
        preferred_exercises=["바벨 데드리프트", "풀업"],
        avoided_exercises=[AvoidedExercise(name="버피", reason="너무 힘듦"), AvoidedExercise(name="박스 점프")],
        environment=Environment(place="gym"),
        training_style="펌핑 위주",
    )

    assert render_preferences(preferences) == (
        "선호 종목: 바벨 데드리프트, 풀업\n기피 종목: 버피(너무 힘듦), 박스 점프\n운동 환경: 헬스장\n선호 방식: 펌핑 위주"
    )


def test_정보가_없는_이유와_빈_값_글자는_비운다():
    preferences = UserPreferences(
        avoided_exercises=[AvoidedExercise(name="버피", reason="싫음")],
        environment=Environment(place="unknown"),
        training_style="null",
    )
    leveled = UserPreferences(training_style="중급자")

    assert preferences.avoided_exercises[0].reason is None
    assert preferences.environment.place is None
    assert preferences.training_style is None
    assert leveled.training_style is None
    assert render_preferences(preferences) == "기피 종목: 버피"
