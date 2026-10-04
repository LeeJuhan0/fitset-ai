import json
from types import SimpleNamespace

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agent.tools import evidence
from app.chat import repository
from app.chat.models import MessageRecord, ThreadRecord
from app.core.config import Settings
from app.exercises.models import Exercise
from app.memory import service as memory_service
from app.chat.summary import CurrentRequest, ThreadSummary
from app.memory.schemas import UserPreferences
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


@pytest.mark.usefixtures("llm", "barbell_squat")
async def test_도구_없이_지어낸_루틴은_보내지_않고_도구를_거친_답변만_보낸다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
):
    tool_call = {"name": "GetExerciseGuide", "args": {"exercise": "바벨 스쿼트"}, "id": "call-1", "type": "tool_call"}
    chat_model.extend([
        AIMessage(content="추천해요. 바벨 스쿼트 4세트 8-12회 반복하세요"),
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="루틴을 준비했어요"),
    ])

    response = auth_client.post(_messages_url(thread), json={"content": "중급자 50분 하체"})

    events = _events(response.text)
    streamed = "".join(data["text"] for kind, data in events if kind == "delta")
    assert "세트" not in streamed
    assert streamed.endswith("루틴을 준비했어요")
    assert events[-1][1]["message"]["content"] == "루틴을 준비했어요"


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
    state = ThreadSummary(current_request=CurrentRequest(text="오늘 휴식 여부", status="done"), decisions=["오늘은 휴식"])
    llm.structured[ThreadSummary] = state
    llm.structured[UserPreferences] = UserPreferences(preferred_exercises=["바벨 스쿼트"])
    payload = {"content": "바벨 스쿼트 좋아하는데 오늘은 쉬어도 될까?"}

    auth_client.post(_messages_url(thread), json=payload)

    saved = ThreadRecord.get(USER_ID, thread.thread_id)
    assert ThreadSummary(**saved.summary) == state
    assert saved.summary_upto is not None
    assert await memory_service.latest_preferences(USER_ID) == UserPreferences(preferred_exercises=["바벨 스쿼트"])


@pytest.mark.usefixtures("llm")
async def test_운동과_무관한_요청은_에이전트_없이_짧은_안내로_답하고_기록에_남긴다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
    monkeypatch,
    settings: Settings,
):
    from app.agent.guardrails import OFF_TOPIC_REPLY
    from app.clients import bedrock

    monkeypatch.setattr(settings, "scope_model_id", "scope-test")
    monkeypatch.setattr(bedrock, "classify", lambda model_id, system, text: "OUT")

    response = auth_client.post(_messages_url(thread), json={"content": "지금 비트코인 사도 돼?"})

    events = _events(response.text)
    assert [data["text"] for kind, data in events if kind == "delta"] == [OFF_TOPIC_REPLY]
    kind, done = events[-1]
    assert kind == "done" and done["message"]["content"] == OFF_TOPIC_REPLY
    assert chat_model == []
    saved = repository.recent_messages(thread.thread_id, 10)
    assert [m.content for m in saved] == ["지금 비트코인 사도 돼?", OFF_TOPIC_REPLY]


@pytest.mark.usefixtures("llm")
async def test_메시지가_500자를_넘으면_안내_답변만_보내고_두_메시지_모두_저장하지_않는다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
):
    response = auth_client.post(_messages_url(thread), json={"content": "가" * 501})

    assert response.status_code == status.HTTP_200_OK
    events = _events(response.text)
    assert [data["text"] for kind, data in events if kind == "delta"] == ["요청은 500자 이내로 입력해주세요."]
    kind, done = events[-1]
    assert kind == "done" and done["message"]["content"] == "요청은 500자 이내로 입력해주세요."
    assert done["threadTitle"] is None
    assert chat_model == []
    assert repository.recent_messages(thread.thread_id, 10) == []
    assert "가" * 501 not in response.text


@pytest.mark.usefixtures("llm")
async def test_메시지가_500자면_평소처럼_답한다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
):
    chat_model.append(AIMessage(content="좋아요"))

    assert auth_client.post(_messages_url(thread), json={"content": "가" * 500}).status_code == status.HTTP_200_OK
    assert len(repository.recent_messages(thread.thread_id, 10)) == 2


@pytest.mark.usefixtures("llm")
async def test_답변이_500자를_넘으면_500자_안_마지막_문장까지만_보내고_저장한다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
):
    long_answer = "스쿼트는 하체 운동의 기본이에요. " * 40
    chat_model.append(AIMessage(content=long_answer))

    response = auth_client.post(_messages_url(thread), json={"content": "스쿼트 알려줘"})

    events = _events(response.text)
    streamed = "".join(data["text"] for kind, data in events if kind == "delta")
    kind, done = events[-1]
    assert len(streamed) <= 500 and streamed.endswith(".")
    assert done["message"]["content"] == streamed
    assert repository.recent_messages(thread.thread_id, 10)[-1].content == streamed


@pytest.mark.usefixtures("llm")
async def test_논문_근거로_답하면_상한_안_답변_뒤에_참고_논문_목록을_붙여_보내고_저장한다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
    monkeypatch,
):
    citation = {"title": "크레아틴 로딩 필요성", "author_year": "Helms 2014", "doi_url": "https://doi.org/10.1186/1550-2783-11-20"}

    async def fake_run(situation):
        return "논문 근거", {"citations": [citation]}

    monkeypatch.setattr(evidence, "run", fake_run)
    chat_model.append(AIMessage(content="", tool_calls=[
        {"name": "FindEvidence", "args": {"situation": "크레아틴 로딩이 필요한지 궁금한 사람"}, "id": "e1", "type": "tool_call"},
    ]))
    chat_model.append(AIMessage(content="로딩은 필수가 아니에요. " * 60))

    response = auth_client.post(_messages_url(thread), json={"content": "크레아틴 로딩 꼭 해야 해요?"})

    events = _events(response.text)
    streamed = "".join(data["text"] for kind, data in events if kind == "delta")
    footer = "\n\n참고 논문\n1. 크레아틴 로딩 필요성 (Helms 2014) https://doi.org/10.1186/1550-2783-11-20"
    kind, done = events[-1]
    assert streamed.endswith(footer) and len(streamed) - len(footer) <= 500
    assert done["message"]["content"] == streamed
    assert repository.recent_messages(thread.thread_id, 10)[-1].content == streamed


@pytest.mark.usefixtures("llm", "now")
async def test_저장한_메시지는_대화방과_같이_14일_뒤_만료_시각을_갖는다(
    auth_client: TestClient,
    thread: ThreadRecord,
    chat_model: list,
    settings: Settings,
):
    from app.chat.models import ThreadRecord as Thread
    from app.chat.utils import ttl_epoch
    from tests.conftest import NOW

    chat_model.append(AIMessage(content="좋아요"))
    auth_client.post(_messages_url(thread), json={"content": "안녕"})

    expected = ttl_epoch(NOW, settings.thread_ttl_days)
    saved = list(MessageRecord.query(thread.thread_id))
    assert [message.deleted_at for message in saved] == [expected, expected]
    assert Thread.get(thread.user_id, thread.thread_id).expires_at == expected
