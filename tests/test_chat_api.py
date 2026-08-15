"""채팅 API 배선 테스트 — 라우터·서비스·응답 봉투를 인메모리 저장소로 태운다.

DynamoDB·Bedrock은 대체한다. 검증 대상은 계약이다:
{traceId, data} 봉투, camelCase 와이어, 목록의 page 부재, 메시지 목록 커서(§4.5),
메시지 전송 SSE 이벤트 3종(§4.6), 204 본문 없음, §7 payload 계약.
"""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.agent import graph
from app.chat import repository, service
from app.chat.domain import ThreadRecord
from app.core import llm, ratelimit
from app.main import app

USER_ID = "11111111-1111-1111-1111-111111111111"


class FakeStore:
    """스레드·메시지·요약을 메모리에 담는 repository 대역."""

    def __init__(self):
        self.threads: dict[tuple[str, str], ThreadRecord] = {}
        self.messages: dict[str, list[dict]] = {}
        self.user_summaries: dict[str, str] = {}
        self.list_messages_calls = 0   # F6 검증용 — 파티션 전체 읽기 횟수

    def list_threads(self, user_id):
        return [t.model_copy() for (uid, _), t in self.threads.items() if uid == user_id]

    def get_thread(self, user_id, thread_id):
        thread = self.threads.get((user_id, thread_id))
        return thread.model_copy() if thread else None

    def put_thread(self, record):
        self.threads[(record.user_id, record.thread_id)] = record.model_copy()

    def delete_thread(self, user_id, thread_id):
        self.threads.pop((user_id, thread_id), None)

    def touch_thread(self, user_id, thread_id, last_message_at, expires_at, title=None):
        thread = self.threads[(user_id, thread_id)]
        thread.last_message_at = last_message_at
        thread.expires_at = expires_at
        if title is not None:
            thread.title = title

    def save_summary(self, user_id, thread_id, summary_text, summary_upto):
        thread = self.threads[(user_id, thread_id)]
        thread.summary_text = summary_text
        thread.summary_upto = summary_upto

    def _sorted_messages(self, thread_id):
        # 내부 공용 — messages_page를 거치면 F6 카운터가 오염된다
        # (실제 repository에선 recent/count가 별도 Query라 전체 읽기가 아니다)
        return [dict(m) for m in sorted(
            self.messages.get(thread_id, []), key=lambda m: m["message_id"]
        )]

    def messages_page(self, thread_id, limit, cursor=None):
        self.list_messages_calls += 1
        messages = self._sorted_messages(thread_id)
        if cursor is not None:
            messages = [m for m in messages if m["message_id"] < cursor]
        page = messages[-limit:]
        next_cursor = page[0]["message_id"] if len(messages) > limit else None
        return page, next_cursor

    def count_messages(self, thread_id, after):
        messages = self.messages.get(thread_id, [])
        if after is None:
            return len(messages)
        return sum(1 for m in messages if m["message_id"] > after)

    def messages_after(self, thread_id, summary_upto):
        messages = self._sorted_messages(thread_id)
        if summary_upto is None:
            return messages
        return [m for m in messages if m["message_id"] > summary_upto]

    def recent_messages(self, thread_id, limit):
        return self._sorted_messages(thread_id)[-limit:]

    def put_message(self, item):
        self.messages.setdefault(item["thread_id"], []).append(dict(item))

    def delete_messages(self, thread_id):
        self.messages.pop(thread_id, None)

    def get_user_summary(self, user_id):
        return self.user_summaries.get(user_id)

    def save_user_summary(self, user_id, summary_text, updated_at):
        self.user_summaries[user_id] = summary_text


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    for name in (
        "list_threads", "get_thread", "put_thread", "delete_thread", "touch_thread",
        "save_summary", "messages_page", "recent_messages", "put_message",
        "delete_messages", "get_user_summary", "save_user_summary",
        "count_messages", "messages_after",
    ):
        monkeypatch.setattr(repository, name, getattr(fake, name))
    # 레이트리미터는 프로세스 전역 캐시 — 앞 테스트가 소비한 토큰이 새지 않게 초기화
    ratelimit.chat_limiter.cache_clear()
    ratelimit.routine_limiter.cache_clear()
    # 제목·요약 LLM은 부르지 않는다 — 폴백 경로로 떨어뜨려 Bedrock 의존을 끊는다
    monkeypatch.setattr(llm, "complete", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()))
    return fake


@pytest.fixture
def client(store):
    # TestClient를 컨텍스트 매니저로 쓰지 않는다 — lifespan이 돌면 부팅 훅이 실제 DynamoDB를
    # Scan한다(루틴 스토어 로드). 채팅 경로는 그 스토어를 쓰지 않으므로 lifespan을 건너뛴다.
    app.dependency_overrides[deps.get_current_user_id] = lambda: USER_ID
    yield TestClient(app)
    app.dependency_overrides.clear()


def _answer(content="좋아요", scheme="text", payload=None):
    """본문을 두 조각 delta로 흘리고 result로 끝나는 run_turn_stream 대역."""
    async def run_turn_stream(user_id, system_prompt, history):
        half = max(1, len(content) // 2)
        yield "delta", content[:half]
        if content[half:]:
            yield "delta", content[half:]
        yield "result", graph.AgentResult(content, scheme, payload)
    return run_turn_stream


def _events(response) -> list[tuple[str, dict]]:
    """SSE 본문 → (event, data) 목록. 하트비트 코멘트(:ping)는 계약상 무시 대상이라 버린다."""
    events = []
    for block in response.text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        lines = dict(line.split(": ", 1) for line in block.strip().split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_create_thread_returns_201_with_envelope(client):
    response = client.post("/ai/v1/threads")
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"traceId", "data"}
    assert body["data"]["threadId"]
    assert body["data"]["createdAt"].endswith("Z")


def test_thread_list_has_no_page_object(client):
    client.post("/ai/v1/threads")
    body = client.get("/ai/v1/threads").json()
    # 클라이언트 API 명세 §공통 — 커서를 안 쓰므로 page를 내보내지 않는다
    assert set(body["data"]) == {"items"}
    assert body["data"]["items"][0]["title"] is None
    assert body["data"]["items"][0]["lastMessageAt"] is None


def test_thread_quota_rejects_creation_with_409(client, store):
    created = [client.post("/ai/v1/threads").json()["data"]["threadId"] for _ in range(10)]
    response = client.post("/ai/v1/threads")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "THREAD_QUOTA_EXCEEDED"
    # 자동삭제가 없다 — 기존 10개는 전부 그대로 남는다
    remaining = {item["threadId"] for item in client.get("/ai/v1/threads").json()["data"]["items"]}
    assert remaining == set(created)


def test_send_message_persists_pair_and_returns_title(client, monkeypatch, store):
    monkeypatch.setattr(graph, "run_turn_stream", _answer("가슴 위주로 짜봤어요"))
    thread_id = client.post("/ai/v1/threads").json()["data"]["threadId"]

    response = client.post(
        f"/ai/v1/threads/{thread_id}/messages", json={"content": "가슴 루틴 추천해줘"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["X-Trace-Id"]

    events = _events(response)
    kinds = [kind for kind, _ in events]
    assert set(kinds[:-1]) == {"delta"} and kinds[-1] == "done"
    # delta 누적 == done 본문 (§4.6 규약 1 — done이 정본)
    assert "".join(data["text"] for kind, data in events if kind == "delta") == "가슴 위주로 짜봤어요"
    done = events[-1][1]
    assert done["message"]["role"] == "assistant"
    assert done["message"]["responseScheme"] == "text"
    assert done["message"]["payload"] is None
    assert done["message"]["content"] == "가슴 위주로 짜봤어요"
    # 첫 발화라 제목이 생성된다(LLM 실패 → 앞 30자 폴백)
    assert done["threadTitle"] == "가슴 루틴 추천해줘"
    assert len(store.messages[thread_id]) == 2


def test_message_list_marks_user_payload_null(client, monkeypatch):
    chart = {"chartType": "line", "metric": "bodyWeight", "title": "체중 변화",
             "xLabel": "날짜", "yLabel": "체중(kg)", "x": ["7/1"],
             "series": [{"name": "체중", "values": [72.4]}]}
    monkeypatch.setattr(graph, "run_turn_stream", _answer("줄고 있어요", "chart", chart))
    thread_id = client.post("/ai/v1/threads").json()["data"]["threadId"]
    client.post(f"/ai/v1/threads/{thread_id}/messages", json={"content": "체중 어때?"})

    data = client.get(f"/ai/v1/threads/{thread_id}/messages").json()["data"]
    items = data["items"]
    assert [item["role"] for item in items] == ["user", "assistant"]
    assert items[0]["responseScheme"] is None and items[0]["payload"] is None
    assert items[1]["responseScheme"] == "chart"
    assert items[1]["payload"]["metric"] == "bodyWeight"
    assert data["nextCursor"] is None   # 대화가 limit 이하라 단일 페이지


def test_message_list_pages_backwards_with_cursor(client, store):
    # §4.5 — 첫 호출은 최신 limit개 + nextCursor, 커서로 과거 페이지, 처음 도달 시 null
    thread_id = client.post("/ai/v1/threads").json()["data"]["threadId"]
    store.messages[thread_id] = [
        {"thread_id": thread_id, "message_id": f"M{index:04d}", "user_id": USER_ID,
         "role": "user", "content": f"m{index}", "response_scheme": None, "payload": None,
         "created_at": "2026-08-04T00:00:00Z"}
        for index in range(5)
    ]
    first = client.get(f"/ai/v1/threads/{thread_id}/messages?limit=2").json()["data"]
    assert [item["messageId"] for item in first["items"]] == ["M0003", "M0004"]
    assert first["nextCursor"] == "M0003"

    second = client.get(
        f"/ai/v1/threads/{thread_id}/messages?limit=2&cursor={first['nextCursor']}"
    ).json()["data"]
    assert [item["messageId"] for item in second["items"]] == ["M0001", "M0002"]

    last = client.get(
        f"/ai/v1/threads/{thread_id}/messages?limit=2&cursor={second['nextCursor']}"
    ).json()["data"]
    assert [item["messageId"] for item in last["items"]] == ["M0000"]
    assert last["nextCursor"] is None


def test_message_list_limit_out_of_range_is_400(client):
    thread_id = client.post("/ai/v1/threads").json()["data"]["threadId"]
    response = client.get(f"/ai/v1/threads/{thread_id}/messages?limit=101")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_delete_thread_removes_messages_and_returns_204(client, monkeypatch, store):
    monkeypatch.setattr(graph, "run_turn_stream", _answer())
    thread_id = client.post("/ai/v1/threads").json()["data"]["threadId"]
    client.post(f"/ai/v1/threads/{thread_id}/messages", json={"content": "안녕"})

    response = client.delete(f"/ai/v1/threads/{thread_id}")
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["X-Trace-Id"]
    assert store.messages.get(thread_id) is None
    # 재삭제는 404 — 현 계약은 비멱등 (§9 협의 포인트 8)
    assert client.delete(f"/ai/v1/threads/{thread_id}").status_code == 404


def test_unknown_thread_returns_thread_not_found(client):
    response = client.get("/ai/v1/threads/01JUNKNOWN0000000000000000/messages")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "THREAD_NOT_FOUND"


def test_message_length_is_validated(client):
    thread_id = client.post("/ai/v1/threads").json()["data"]["threadId"]
    response = client.post(f"/ai/v1/threads/{thread_id}/messages", json={"content": ""})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def _seed_thread(store, thread_id):
    """정원 경쟁의 사후 상태를 만든다 — create API를 거치지 않고 직접 심는다."""
    store.put_thread(ThreadRecord(
        user_id=USER_ID, thread_id=thread_id,
        expires_at=4102444800, created_at="2026-07-29T00:00:00Z",
    ))


def test_thread_list_caps_at_quota_even_after_race(client, store):
    # F2 read-repair — TOCTOU 경쟁으로 11개 이상 생겨도 목록은 계약(최대 10)을 지킨다
    for index in range(12):
        _seed_thread(store, f"01T{index}")
    items = client.get("/ai/v1/threads").json()["data"]["items"]
    assert len(items) == 10


def test_create_over_race_overflow_rejects_without_deleting(client, store):
    # 경쟁으로 초과된 상태에서 생성해도 지우지 않는다 — 409만 낸다
    for index in range(11):
        _seed_thread(store, f"01T{index}")
    response = client.post("/ai/v1/threads")
    assert response.status_code == 409
    assert len(store.threads) == 11


def _seed_expired_thread(store, thread_id):
    """보존 기한(14일)이 지난 스레드를 메시지 1건과 함께 심는다."""
    store.put_thread(ThreadRecord(
        user_id=USER_ID, thread_id=thread_id, title="옛 대화",
        last_message_at="2026-07-01T00:00:00Z", expires_at=1,
        created_at="2026-07-01T00:00:00Z",
    ))
    store.messages[thread_id] = [
        {"thread_id": thread_id, "message_id": "M0001", "user_id": USER_ID,
         "role": "user", "content": "옛날 얘기", "response_scheme": None, "payload": None,
         "created_at": "2026-07-01T00:00:00Z"}
    ]


def test_expired_thread_listed_with_needs_deletion_flag(client, store):
    # 만료 스레드는 숨기지 않는다 — 클라가 진입 시 삭제 안내를 띄울 수 있게 플래그로 노출
    _seed_expired_thread(store, "01EXP")
    fresh_id = client.post("/ai/v1/threads").json()["data"]["threadId"]
    flags = {
        item["threadId"]: item["needsDeletion"]
        for item in client.get("/ai/v1/threads").json()["data"]["items"]
    }
    assert flags == {"01EXP": True, fresh_id: False}


def test_expired_thread_messages_are_purged_lazily(client, store):
    # 만료 스레드 조회는 200 빈 목록 — 이 호출이 메시지 지연 삭제를 겸하고 항목은 남긴다
    _seed_expired_thread(store, "01EXP")
    body = client.get("/ai/v1/threads/01EXP/messages")
    assert body.status_code == 200
    assert body.json()["data"] == {"items": [], "nextCursor": None}
    assert "01EXP" not in store.messages
    assert (USER_ID, "01EXP") in store.threads


def test_expired_thread_rejects_send_with_409(client, store, monkeypatch):
    monkeypatch.setattr(graph, "run_turn_stream", _answer())
    _seed_expired_thread(store, "01EXP")
    response = client.post("/ai/v1/threads/01EXP/messages", json={"content": "안녕"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "THREAD_EXPIRED"


def test_expired_thread_delete_removes_thread_item(client, store):
    # 유저가 삭제를 확정하면 그때 스레드 항목까지 지운다 — 만료 스레드 항목의 유일한 삭제 경로
    _seed_expired_thread(store, "01EXP")
    assert client.delete("/ai/v1/threads/01EXP").status_code == 204
    assert store.threads == {}
    assert store.messages == {}


def test_refresh_below_threshold_skips_full_partition_read(client, monkeypatch, store):
    # F6 — 미요약이 임계 미달이면 COUNT만 하고 메시지 페이지 조회(messages_page)를 부르지 않는다
    monkeypatch.setattr(graph, "run_turn_stream", _answer())
    thread_id = client.post("/ai/v1/threads").json()["data"]["threadId"]
    store.list_messages_calls = 0
    client.post(f"/ai/v1/threads/{thread_id}/messages", json={"content": "안녕"})
    assert store.list_messages_calls == 0


def test_message_rate_limit_returns_429(client, monkeypatch):
    # F17 — 유저별 분당 상한(기본 10) 초과 시 RATE_LIMITED, LLM 경로 진입 전 차단
    monkeypatch.setattr(graph, "run_turn_stream", _answer())
    thread_id = client.post("/ai/v1/threads").json()["data"]["threadId"]
    responses = [
        client.post(f"/ai/v1/threads/{thread_id}/messages", json={"content": "안녕"})
        for _ in range(11)
    ]
    assert [r.status_code for r in responses[:10]] == [200] * 10
    assert responses[10].status_code == 429
    assert responses[10].json()["error"]["code"] == "RATE_LIMITED"


def test_thread_full_returns_409(client, monkeypatch, store):
    # F14 — 스레드당 메시지 상한(기본 1000, 2026-08-04 상향) 도달 시 THREAD_FULL, 새 스레드 유도
    from app.core.config import get_settings

    monkeypatch.setattr(graph, "run_turn_stream", _answer())
    thread_id = client.post("/ai/v1/threads").json()["data"]["threadId"]
    store.messages[thread_id] = [
        {"thread_id": thread_id, "message_id": f"M{index:04d}", "user_id": USER_ID,
         "role": "user", "content": "x", "response_scheme": None, "payload": None,
         "created_at": "2026-07-29T00:00:00Z"}
        for index in range(get_settings().max_messages_per_thread)
    ]
    response = client.post(f"/ai/v1/threads/{thread_id}/messages", json={"content": "안녕"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "THREAD_FULL"


async def test_refresh_summarizes_only_unsummarized_tail(store, monkeypatch):
    # F13 — 재요약 입력은 summary_upto 이후 꼬리만 (전체 대화 재전송 금지)
    captured = []

    def fake_complete(system, user, max_tokens=None):
        captured.append(user)
        return "병합 요약"

    monkeypatch.setattr(llm, "complete", fake_complete)
    _seed_thread(store, "01TX")
    store.threads[(USER_ID, "01TX")].summary_upto = "M0005"
    store.threads[(USER_ID, "01TX")].summary_text = "이전 요약"
    store.messages["01TX"] = [
        {"thread_id": "01TX", "message_id": f"M{index:04d}", "user_id": USER_ID,
         "role": "user", "content": f"msg-{index}", "response_scheme": None,
         "payload": None, "created_at": "2026-07-29T00:00:00Z"}
        for index in range(1, 16)   # M0001~M0015 — upto 이후 10건 = 임계 도달
    ]
    store.list_messages_calls = 0

    await service.refresh_summaries(USER_ID, "01TX")

    assert store.list_messages_calls == 0                # 파티션 전체 로드 없음
    assert store.threads[(USER_ID, "01TX")].summary_upto == "M0015"
    assert "이전 요약" in captured[0]                     # 기존 요약과의 증분 병합
    assert "msg-6" in captured[0]
    assert "msg-3" not in captured[0]                    # 이미 요약된 구간은 재전송 안 함
    assert store.user_summaries[USER_ID] == "병합 요약"


def test_agent_failure_before_first_delta_is_http_503(client, monkeypatch):
    # 첫 delta 전 실패 — 스트림을 시작하지 않고 기존 HTTP JSON 오류로 나간다 (§4.6 규약 3)
    async def boom(user_id, system_prompt, history):
        raise RuntimeError("bedrock down")
        yield  # 제너레이터로 만들기 위한 표식 — 도달하지 않는다

    monkeypatch.setattr(graph, "run_turn_stream", boom)
    thread_id = client.post("/ai/v1/threads").json()["data"]["threadId"]
    response = client.post(f"/ai/v1/threads/{thread_id}/messages", json={"content": "안녕"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_UNAVAILABLE"


def test_mid_stream_failure_emits_error_event(client, monkeypatch, store):
    # 첫 delta 이후 실패 — 이미 200이 나갔으므로 error 이벤트로 전달하고 스트림을 닫는다
    async def flaky(user_id, system_prompt, history):
        yield "delta", "생각 중"
        raise RuntimeError("bedrock cut")

    monkeypatch.setattr(graph, "run_turn_stream", flaky)
    thread_id = client.post("/ai/v1/threads").json()["data"]["threadId"]
    response = client.post(f"/ai/v1/threads/{thread_id}/messages", json={"content": "안녕"})
    assert response.status_code == 200
    events = _events(response)
    assert events[0] == ("delta", {"text": "생각 중"})
    kind, data = events[-1]
    assert kind == "error"
    assert data["error"]["code"] == "AI_UNAVAILABLE"
    assert data["traceId"]
    # 실패 턴은 assistant를 저장하지 않는다 — user 메시지만 남는다 (§4.6 규약 5)
    assert [m["role"] for m in store.messages[thread_id]] == ["user"]


def test_sse_조각은_생성되는_즉시_나간다_버퍼링_없음(client, monkeypatch, store):
    """스트리밍 증명 — delta가 모였다 한 번에 오지 않고 생성 간격 그대로 도착한다.

    TestClient는 앱을 다 돌린 뒤 본문을 돌려줘 시간 축이 뭉개진다(실측 — 조각 3개가
    한 덩어리로 도착). 그래서 진짜 uvicorn을 임시 포트에 띄워 실제 TCP 소켓으로 잰다.
    스텁 LLM이 0.25초 간격으로 delta 3개를 낸다 — 버퍼링이면 도착 간격이 0에 가깝고,
    조각마다 즉시 send되면 도착 간격이 생성 간격만큼 벌어진다. pytest -s 로 타임라인 확인.
    """
    import threading

    import httpx
    import uvicorn

    delay = 0.25

    async def slow_stream(user_id, system_prompt, history):
        yield "delta", "조각1"
        await asyncio.sleep(delay)
        yield "delta", "조각2"
        await asyncio.sleep(delay)
        yield "delta", "조각3"
        yield "result", graph.AgentResult("조각1조각2조각3", "text", None)

    monkeypatch.setattr(graph, "run_turn_stream", slow_stream)

    # lifespan="off" — 부팅 훅(루틴 스토어 Scan)이 실제 DynamoDB를 부르지 않게
    config = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]

        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as http:
            thread_id = http.post("/ai/v1/threads").json()["data"]["threadId"]
            arrivals = []
            started = time.perf_counter()
            with http.stream(
                "POST", f"/ai/v1/threads/{thread_id}/messages", json={"content": "스트리밍 되나요"}
            ) as response:
                for raw in response.iter_raw():
                    arrivals.append((time.perf_counter() - started, raw.decode()))
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    for at, chunk in arrivals:
        print(f"\n  +{at*1000:6.0f}ms  {chunk.replace(chr(10), chr(92)+chr(110))[:60]}")

    delta_times = [at for at, chunk in arrivals if "delta" in chunk]
    assert len(delta_times) >= 3, "delta들이 한 덩어리로 왔다 — 버퍼링"
    # 첫 조각은 마지막 delta 생성(2×delay)을 기다리지 않고 도착해야 한다
    assert delta_times[0] < delay, "첫 delta가 늦게 왔다 — 어딘가에서 모으고 있다"
    # 첫·끝 조각의 도착 간격이 생성 간격(2×delay)만큼 벌어져 있어야 한다
    spread = delta_times[-1] - delta_times[0]
    assert spread > delay * 1.5, f"delta들이 {spread:.3f}s 안에 몰려 도착 — 버퍼링 의심"
