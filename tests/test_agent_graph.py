"""에이전트 그래프 배선 테스트 — 감사 F15 + 스트리밍 이벤트 계약(§4.6).

ChatBedrockConverse에 core 클라이언트가 주입됐는지 검증한다 — 미주입이면 langchain이
기본 설정(read 60s·재시도 다수)으로 자체 클라이언트를 만들어 타임아웃 규약이 빠진다.
스트리밍은 가짜 그래프의 이벤트 시나리오로 delta 필터링과 최종 확정을 검증한다.
"""
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.agent import graph
from app.core import llm

CHART = {"chartType": "line", "metric": "bodyWeight", "title": "체중 변화",
         "xLabel": "날짜", "yLabel": "체중(kg)", "x": ["7/1"],
         "series": [{"name": "체중", "values": [72.4]}]}


class FakeGraph:
    """astream 이벤트 시나리오를 재생하는 컴파일 그래프 대역."""

    def __init__(self, events):
        self.events = events

    async def astream(self, inputs, stream_mode=None):
        for event in self.events:
            yield event


def test_chat_model_uses_tuned_bedrock_client():
    graph._model.cache_clear()
    llm.chat_runtime.cache_clear()
    model = graph._model()
    # bind_tools 결과(RunnableBinding)의 원본 모델이 core 클라이언트를 그대로 쓴다
    assert model.bound.client is llm.chat_runtime()


def test_chat_client_has_tuned_timeouts():
    llm.chat_runtime.cache_clear()
    config = llm.chat_runtime().meta.config
    assert config.read_timeout == 15.0
    assert config.connect_timeout == 2.0
    # botocore가 max_attempts(재시도 1)를 total_max_attempts(총 시도 2)로 정규화한다
    assert config.retries == {"mode": "adaptive", "total_max_attempts": 2}


async def test_run_turn_stream_yields_agent_deltas_then_result(monkeypatch):
    # agent 노드 토큰만 delta로 — tools 노드 청크는 본문이 아니라 걸러야 한다
    final_state = {
        "messages": [AIMessage(content="체중이 줄고 있어요.")],
        "artifacts": [{"response_scheme": "chart", "payload": CHART}],
    }
    events = [
        ("messages", (AIMessageChunk(content="체중이 "), {"langgraph_node": "agent"})),
        ("messages", (AIMessageChunk(content="툴 결과 원문"), {"langgraph_node": "tools"})),
        ("messages", (AIMessageChunk(content="줄고 있어요."), {"langgraph_node": "agent"})),
        ("values", final_state),
    ]
    monkeypatch.setattr(graph, "_graph", lambda: FakeGraph(events))
    collected = [
        event async for event in graph.run_turn_stream("u-1", "sys", [HumanMessage(content="체중 어때")])
    ]
    assert [text for kind, text in collected if kind == "delta"] == ["체중이 ", "줄고 있어요."]
    kind, result = collected[-1]
    assert kind == "result"
    assert result.content == "체중이 줄고 있어요."
    assert result.response_scheme == "chart"
    assert result.payload == CHART


async def test_run_turn_stream_falls_back_when_no_text(monkeypatch):
    # 툴만 돌고 본문이 비면 — 빈 말풍선 대신 폴백 문구, scheme은 text로 강등
    events = [("values", {"messages": [], "artifacts": []})]
    monkeypatch.setattr(graph, "_graph", lambda: FakeGraph(events))
    collected = [
        event async for event in graph.run_turn_stream("u-1", "sys", [HumanMessage(content="안녕")])
    ]
    assert [kind for kind, _ in collected] == ["result"]
    result = collected[0][1]
    assert result.content
    assert result.response_scheme == "text"
    assert result.payload is None


async def test_run_turn_and_stream_agree_on_finalize(monkeypatch):
    # 동기 경로(run_turn)와 스트리밍 경로의 최종 확정이 같은 _finalize를 타는지 회귀 가드
    state = {"messages": [AIMessage(content="같은 답")], "artifacts": []}

    class FakeInvokeGraph(FakeGraph):
        async def ainvoke(self, inputs):
            return state

    monkeypatch.setattr(graph, "_graph", lambda: FakeInvokeGraph([("values", state)]))
    sync_result = await graph.run_turn("u-1", "sys", [HumanMessage(content="q")])
    stream_events = [
        event async for event in graph.run_turn_stream("u-1", "sys", [HumanMessage(content="q")])
    ]
    stream_result = stream_events[-1][1]
    assert (sync_result.content, sync_result.response_scheme) == (
        stream_result.content, stream_result.response_scheme
    ) == ("같은 답", "text")
