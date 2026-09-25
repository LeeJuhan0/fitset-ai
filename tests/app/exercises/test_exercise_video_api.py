import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.clients import mysql
from app.core.config import Settings
from app.exercises.models import Exercise


async def test_사용자는_종목_slug로_가이드_영상_URL을_조회할_수_있다(
    auth_client: TestClient,
    barbell_squat: Exercise,
    settings: Settings,
):
    response = auth_client.get("/ai/v1/exercises/barbell-squat/video")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"] == {
        "exerciseId": mysql.uuid_str(barbell_squat.id),
        "slug": "barbell-squat",
        "exerciseName": "바벨 스쿼트",
        "videoUrl": f"{settings.media_cdn_base_url}/videos/barbell-squat.mp4",
        "expiresAt": None,
    }


@pytest.mark.usefixtures("barbell_squat")
async def test_존재하지_않는_종목의_영상을_조회하면_종목을_찾을_수_없다고_응답한다(
    auth_client: TestClient,
):
    response = auth_client.get("/ai/v1/exercises/unknown-exercise/video")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "EXERCISE_NOT_FOUND"


@pytest.mark.usefixtures("barbell_row_without_video")
async def test_영상이_등록되지_않은_종목을_조회하면_영상이_없다고_응답한다(
    auth_client: TestClient,
):
    response = auth_client.get("/ai/v1/exercises/barbell-row/video")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "VIDEO_NOT_FOUND"


@pytest.mark.usefixtures("barbell_squat", "mysql_down")
async def test_종목_목록을_불러올_수_없으면_서버_오류_대신_종목을_찾을_수_없다고_응답한다(
    auth_client: TestClient,
):
    response = auth_client.get("/ai/v1/exercises/barbell-squat/video")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "EXERCISE_NOT_FOUND"
