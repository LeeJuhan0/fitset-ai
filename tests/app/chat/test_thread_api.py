import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.chat import repository
from app.chat.models import ThreadRecord
from app.core.clock import iso_utc
from tests.conftest import NOW

THREADS_URL = "/ai/v1/threads"


@pytest.mark.usefixtures("dynamo_tables")
async def test_사용자는_새_대화를_만들_수_있다(
    auth_client: TestClient,
):
    response = auth_client.post(THREADS_URL)

    assert response.status_code == status.HTTP_201_CREATED
    thread_id = response.json()["data"]["threadId"]
    assert response.json()["data"]["createdAt"] == iso_utc(NOW)
    assert [item["threadId"] for item in auth_client.get(THREADS_URL).json()["data"]["items"]] == [thread_id]


@pytest.mark.usefixtures("full_threads")
async def test_대화가_정원만큼_있으면_새_대화를_만들_수_없다(
    auth_client: TestClient,
):
    response = auth_client.post(THREADS_URL)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error"]["code"] == "THREAD_QUOTA_EXCEEDED"
    assert auth_client.get(THREADS_URL).json()["data"]["canCreateThread"] is False


async def test_대화_목록은_최근_활동순이며_만료된_대화는_삭제_필요로_표시한다(
    auth_client: TestClient,
    thread: ThreadRecord,
    expired_thread: ThreadRecord,
):
    response = auth_client.get(THREADS_URL)

    assert response.status_code == status.HTTP_200_OK
    items = response.json()["data"]["items"]
    assert [(item["threadId"], item["needsDeletion"]) for item in items] == [
        (thread.thread_id, False),
        (expired_thread.thread_id, True),
    ]
    assert response.json()["data"]["canCreateThread"] is True


@pytest.mark.usefixtures("other_user_thread")
async def test_다른_사용자의_대화는_목록에_보이지_않는다(
    auth_client: TestClient,
):
    response = auth_client.get(THREADS_URL)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["items"] == []


@pytest.mark.usefixtures("conversation")
async def test_사용자는_대화를_삭제하면_메시지까지_지워진다(
    auth_client: TestClient,
    thread: ThreadRecord,
):
    response = auth_client.delete(f"{THREADS_URL}/{thread.thread_id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert auth_client.get(THREADS_URL).json()["data"]["items"] == []
    assert repository.count_messages(thread.thread_id, None) == 0


async def test_다른_사용자의_대화는_삭제할_수_없다(
    auth_client: TestClient,
    other_user_thread: ThreadRecord,
):
    response = auth_client.delete(f"{THREADS_URL}/{other_user_thread.thread_id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "THREAD_NOT_FOUND"
    assert ThreadRecord.get(other_user_thread.user_id, other_user_thread.thread_id).title == "남의 대화"
