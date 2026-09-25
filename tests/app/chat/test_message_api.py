import json
from types import SimpleNamespace

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.chat import repository
from app.chat.models import MessageRecord, ThreadRecord
from app.core.config import Settings
from app.exercises.models import Exercise
from app.memory import repository as memory_repository
from tests.conftest import USER_ID


def _messages_url(thread: ThreadRecord) -> str:
    """대화 메시지 경로"""
    return f"/ai/v1/threads/{thread.thread_id}/messages"


def _events(body: str) -> list[tuple[str, dict]]:
    """SSE 본문을 이벤트 이름과 데이터 목록으로"""
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        events.append((fields["event"], json.loads(fields["data"])))
    return events


async def test_사용자는_메시지를_보내면_답변을_스트리밍으로_받고_첫_메시지로_제목이_붙는다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
    llm: SimpleNamespace,
):
    chat_model.append(AIMessage(content="하체 위주로 스쿼트를 추천해요"))
    llm.text = "하체 운동 상담"
    payload = {"content": "오늘 하체 뭐 할까?"}

    response = auth_client.post(_messages_url(thread), json=payload)

    assert response.status_code == status.HTTP_200_OK
    events = _events(response.text)
    assert "".join(data["text"] for kind, data in events if kind == "delta") == "하체 위주로 스쿼트를 추천해요"
    kind, done = events[-1]
    assert kind == "done"
    assert done["message"]["content"] == "하체 위주로 스쿼트를 추천해요"
    assert done["message"]["responseScheme"] == "text"
    assert done["threadTitle"] == "하체 운동 상담"


@pytest.mark.usefixtures("llm")
async def test_보낸_메시지와_답변은_대화_기록에_저장된다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
):
    chat_model.append(AIMessage(content="좋아요"))
    auth_client.post(_messages_url(thread), json={"content": "안녕"})

    response = auth_client.get(_messages_url(thread))

    assert response.status_code == status.HTTP_200_OK
    items = response.json()["data"]["items"]
    assert [(item["role"], item["content"]) for item in items] == [("user", "안녕"), ("assistant", "좋아요")]


@pytest.mark.usefixtures("llm")
async def test_종목_설명을_요청하면_가이드_영상_정보를_함께_응답한다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
    barbell_squat: Exercise,
    settings: Settings,
):
    tool_call = {"name": "GetExerciseGuide", "args": {"exercise": "바벨 스쿼트"}, "id": "call-1", "type": "tool_call"}
    chat_model.extend([
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="바벨 스쿼트는 발을 어깨너비로 벌리고 앉았다 일어나요"),
    ])
    payload = {"content": "스쿼트 자세 알려줘"}

    response = auth_client.post(_messages_url(thread), json=payload)

    assert response.status_code == status.HTTP_200_OK
    message = _events(response.text)[-1][1]["message"]
    assert message["responseScheme"] == "exerciseGif"
    assert message["payload"]["slug"] == "barbell-squat"
    assert message["payload"]["videoUrl"] == f"{settings.media_cdn_base_url}/videos/barbell-squat.mp4"


async def test_만료된_대화에는_메시지를_보낼_수_없다(
    auth_client: TestClient,
    expired_thread: ThreadRecord,
):
    payload = {"content": "다시 이어서 얘기하자"}

    response = auth_client.post(_messages_url(expired_thread), json=payload)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error"]["code"] == "THREAD_EXPIRED"


@pytest.mark.usefixtures("conversation")
async def test_대화_메시지가_상한에_이르면_더_보낼_수_없다(
    auth_client: TestClient,
    thread: ThreadRecord,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "max_messages_per_thread", 6)
    payload = {"content": "하나 더 물어볼게"}

    response = auth_client.post(_messages_url(thread), json=payload)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error"]["code"] == "THREAD_FULL"


@pytest.mark.usefixtures("llm")
async def test_메시지를_너무_자주_보내면_HTTP_429_응답을_한다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "chat_messages_per_minute", 1)
    chat_model.append(AIMessage(content="첫 답변"))
    auth_client.post(_messages_url(thread), json={"content": "첫 질문"})

    response = auth_client.post(_messages_url(thread), json={"content": "바로 또 질문"})

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert response.json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.usefixtures("llm", "chat_model")
async def test_답변_생성에_실패하면_HTTP_503_응답을_하고_메시지는_저장되지_않는다(
    auth_client: TestClient,
    thread: ThreadRecord,
):
    payload = {"content": "오늘 뭐 할까?"}

    response = auth_client.post(_messages_url(thread), json=payload)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["error"]["code"] == "AI_UNAVAILABLE"
    assert repository.count_messages(thread.thread_id, None) == 0


async def test_대화_기록은_최신_페이지부터_커서로_과거_방향으로_나눠_조회한다(
    auth_client: TestClient,
    thread: ThreadRecord,
    conversation: list[MessageRecord],
):
    first = auth_client.get(_messages_url(thread), params={"limit": 4}).json()["data"]
    second = auth_client.get(_messages_url(thread), params={"limit": 4, "cursor": first["nextCursor"]}).json()["data"]

    message_ids = [message.message_id for message in conversation]
    assert [item["messageId"] for item in first["items"]] == message_ids[2:]
    assert [item["messageId"] for item in second["items"]] == message_ids[:2]
    assert second["nextCursor"] is None


async def test_만료된_대화의_기록을_조회하면_빈_목록을_받고_메시지는_지워진다(
    auth_client: TestClient,
    expired_thread: ThreadRecord,
):
    MessageRecord(
        thread_id=expired_thread.thread_id, message_id="01OLD", user_id=USER_ID, role="user", content="옛 메시지",
    ).save()

    response = auth_client.get(_messages_url(expired_thread))

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["items"] == []
    assert repository.count_messages(expired_thread.thread_id, None) == 0


@pytest.mark.usefixtures("conversation")
async def test_대화가_길어지면_대화_요약과_사용자_장기_요약을_남긴다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
    llm: SimpleNamespace,
):
    chat_model.append(AIMessage(content="오늘은 쉬어도 좋아요"))
    llm.text = "하체 위주로 운동하며 휴식 여부를 상담함"
    payload = {"content": "오늘 쉬어도 될까?"}

    auth_client.post(_messages_url(thread), json=payload)

    saved = ThreadRecord.get(USER_ID, thread.thread_id)
    assert saved.summary_text == "하체 위주로 운동하며 휴식 여부를 상담함"
    assert saved.summary_upto is not None
    assert memory_repository.get_user_summary(USER_ID) == "하체 위주로 운동하며 휴식 여부를 상담함"
