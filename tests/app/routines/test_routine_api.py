from types import SimpleNamespace

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.clients import mysql
from app.exercises.models import Exercise
from app.rag.prompts import QueryAnalysis

ROUTINE_URL = "/ai/v1/routines"
LEG_PAYLOAD = {"level": "beginner", "muscleGroups": ["quadriceps"], "minutes": 60, "includeWarmup": False}


@pytest.mark.usefixtures("user_profile", "leg_routine", "llm")
async def test_사용자는_조건에_맞는_루틴을_생성할_수_있다(
    auth_client: TestClient,
    barbell_squat: Exercise,
):
    payload = LEG_PAYLOAD

    response = auth_client.post(ROUTINE_URL, json=payload)

    assert response.status_code == status.HTTP_200_OK
    routine = response.json()["data"]["routine"]
    assert routine["slug"] == "leg-day"
    assert routine["estimatedMinutes"] == 60
    assert routine["exercises"][0]["exerciseId"] == mysql.uuid_str(barbell_squat.id)
    assert routine["exercises"][0]["thumbnailUrl"].endswith("/thumbnails/barbell-squat.webp")
    assert [s["weight"] for s in routine["exercises"][0]["sets"]] == [0, 0, 0]


@pytest.mark.parametrize("overrides, expected_status_code", [
    # 통과하는 경우
    ({"muscle_groups": ["quadriceps", "glutes"]}, status.HTTP_200_OK),
    ({"minutes": 72}, status.HTTP_200_OK),
    ({"minutes": None}, status.HTTP_200_OK),

    # 걸러지는 경우
    ({"muscle_groups": ["chest"]}, status.HTTP_409_CONFLICT),
    ({"level": 1}, status.HTTP_409_CONFLICT),
    ({"minutes": 80}, status.HTTP_409_CONFLICT),
])
@pytest.mark.usefixtures("user_profile", "barbell_squat", "llm")
async def test_부위_수준_시간_조건을_벗어난_루틴은_추천하지_않는다(
    auth_client: TestClient,
    add_routine,
    overrides: dict,
    expected_status_code: int,
):
    routine = {"slug": "leg-day", "muscle_groups": ["quadriceps"], "exercises": [("barbell-squat", "바벨 스쿼트")]}
    add_routine(**{**routine, **overrides})
    payload = LEG_PAYLOAD

    response = auth_client.post(ROUTINE_URL, json=payload)

    assert response.status_code == expected_status_code



@pytest.mark.parametrize("muscle_groups, expected_status_code", [
    # 기피 부위가 든 루틴은 제외
    (["glutes"], status.HTTP_409_CONFLICT),

    # 요청 부위와 겹치는 기피 부위는 요청이 우선
    (["quadriceps"], status.HTTP_200_OK),
])
@pytest.mark.usefixtures("avoided_quadriceps", "leg_routine", "llm")
async def test_프로필_기피_부위가_든_루틴은_요청_부위가_아니면_추천하지_않는다(
    auth_client: TestClient,
    muscle_groups: list[str],
    expected_status_code: int,
):
    payload = {**LEG_PAYLOAD, "muscleGroups": muscle_groups}

    response = auth_client.post(ROUTINE_URL, json=payload)

    assert response.status_code == expected_status_code

@pytest.mark.usefixtures("user_profile", "push_up_workouts", "llm")
async def test_맨몸_운동_위주의_홈트_사용자에게는_맨몸_루틴만_추천한다(
    auth_client: TestClient,
    add_routine,
):
    add_routine("chest-gym", ["chest"], [("barbell-bench-press", "바벨 벤치프레스")], similarity=0.95)
    add_routine("chest-home", ["chest"], [("push-up", "푸시업")], equipment=("bodyweight",), similarity=0.5)
    payload = {"level": "beginner", "muscleGroups": ["chest"], "minutes": 60, "includeWarmup": False}

    response = auth_client.post(ROUTINE_URL, json=payload)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["routine"]["slug"] == "chest-home"


@pytest.mark.usefixtures("user_profile", "squat_workout", "leg_routine", "llm")
async def test_기록이_있는_종목은_실측_기록_기준_무게를_추천한다(
    auth_client: TestClient,
):
    payload = LEG_PAYLOAD

    response = auth_client.post(ROUTINE_URL, json=payload)

    assert response.status_code == status.HTTP_200_OK
    sets = response.json()["data"]["routine"]["exercises"][0]["sets"]
    assert [s["weight"] for s in sets] == [100, 100, 100]



@pytest.mark.usefixtures("user_profile", "deleted_squat_workout", "leg_routine", "llm")
async def test_사용자가_삭제한_운동_기록은_무게_추천에_쓰지_않는다(
    auth_client: TestClient,
):
    payload = LEG_PAYLOAD

    response = auth_client.post(ROUTINE_URL, json=payload)

    assert response.status_code == status.HTTP_200_OK
    sets = response.json()["data"]["routine"]["exercises"][0]["sets"]
    assert [s["weight"] for s in sets] == [0, 0, 0]

@pytest.mark.usefixtures("user_profile", "squat_workout", "leg_routine", "llm")
async def test_워밍업을_포함하면_첫_세트를_가볍게_추천한다(
    auth_client: TestClient,
):
    payload = {**LEG_PAYLOAD, "includeWarmup": True}

    response = auth_client.post(ROUTINE_URL, json=payload)

    assert response.status_code == status.HTTP_200_OK
    sets = response.json()["data"]["routine"]["exercises"][0]["sets"]
    assert [s["weight"] for s in sets] == [50, 100, 100]


@pytest.mark.usefixtures("user_profile", "pg_engine", "llm")
async def test_조건에_맞는_루틴이_없으면_HTTP_409_응답을_한다(
    auth_client: TestClient,
):
    payload = LEG_PAYLOAD

    response = auth_client.post(ROUTINE_URL, json=payload)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error"]["code"] == "NO_ROUTINE_CANDIDATE"


@pytest.mark.usefixtures("user_profile", "leg_routine")
async def test_안전하지_않은_요청은_루틴을_추천하지_않고_HTTP_409_응답을_한다(
    auth_client: TestClient,
    llm: SimpleNamespace,
):
    llm.structured[QueryAnalysis] = QueryAnalysis(
        description="하체 루틴", avoid_muscles=[], unsafe_constraints=["수술 직후 고강도 운동"],
    )
    payload = {**LEG_PAYLOAD, "context": "어제 무릎 수술했는데 스쿼트 세게 하고 싶어요"}

    response = auth_client.post(ROUTINE_URL, json=payload)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error"]["code"] == "UNSAFE_CONSTRAINT"


@pytest.mark.usefixtures("user_profile")
async def test_LLM이_실패하면_가장_유사한_루틴으로_대신_추천한다(
    auth_client: TestClient,
    add_routine,
    llm: SimpleNamespace,
):
    add_routine("leg-far", ["quadriceps"], [("barbell-squat", "바벨 스쿼트")], similarity=0.3)
    add_routine("leg-near", ["quadriceps"], [("barbell-squat", "바벨 스쿼트")], similarity=0.95)
    llm.structured.clear()
    payload = LEG_PAYLOAD

    response = auth_client.post(ROUTINE_URL, json=payload)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["routine"]["slug"] == "leg-near"


@pytest.mark.usefixtures("user_profile", "leg_routine")
async def test_쿼리_임베딩까지_실패하면_HTTP_503_응답을_한다(
    auth_client: TestClient,
    llm: SimpleNamespace,
):
    llm.embedding = None
    payload = LEG_PAYLOAD

    response = auth_client.post(ROUTINE_URL, json=payload)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["error"]["code"] == "AI_UNAVAILABLE"
