import logging
import operator
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, TypedDict

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AnyMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from app.agent import guardrails, tools
from app.core import llm
from app.core.config import get_settings

logger = logging.getLogger("fitset")

AGENT_NODE = "agent"
TOOLS_NODE = "tools"


class GraphEvent(StrEnum):
    DELTA = "delta"
    RESULT = "result"


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    artifacts: Annotated[list[dict], operator.add]
    tool_turns: int


@dataclass(frozen=True)
class AgentResult:
    content: str
    response_scheme: str
    payload: dict | None


def _model_provider(model_id: str) -> str:
    """모델 아이디에서 추론 프로필 접두사 제외 프로바이더 추출"""
    parts = model_id.split(".")
    if parts and parts[0] in ("us", "eu", "apac", "global"):
        parts = parts[1:]
    return parts[0] if parts else "amazon"


@lru_cache
def _model() -> Runnable:
    """툴 바인딩된 Bedrock 채팅 모델 전역 싱글턴"""
    settings = get_settings()
    model = ChatBedrockConverse(
        model=settings.llm_model_id,
        provider=_model_provider(settings.llm_model_id),
        region_name=settings.aws_region,
        client=llm.chat_runtime(),
        max_tokens=settings.llm_max_tokens,
        temperature=0.3,
    )
    return model.bind_tools(tools.TOOL_SCHEMAS)


async def _agent_node(state: ChatState) -> dict:
    """모델 호출로 답변 또는 툴콜 수신"""
    response = await _model().ainvoke(state["messages"])
    return {"messages": [response]}


async def _tools_node(state: ChatState) -> dict:
    """툴콜 실행 후 툴 메시지와 아티팩트 적재"""
    calls = getattr(state["messages"][-1], "tool_calls", []) or []
    outputs: list[AnyMessage] = []
    artifacts: list[dict] = []
    for call in calls:
        text, artifact = await tools.dispatch(state["user_id"], call["name"], call.get("args") or {})
        outputs.append(ToolMessage(content=text, tool_call_id=call["id"]))
        if artifact is not None:
            artifacts.append(artifact)
    return {
        "messages": outputs,
        "artifacts": artifacts,
        "tool_turns": state.get("tool_turns", 0) + 1,
    }


def _route(state: ChatState) -> str:
    """툴콜 유무와 왕복 상한으로 다음 노드 결정"""
    if state.get("tool_turns", 0) >= get_settings().agent_max_tool_turns:
        logger.warning("tool turn limit reached, ending turn")
        return END
    if getattr(state["messages"][-1], "tool_calls", None):
        return TOOLS_NODE
    return END


@lru_cache
def _graph() -> CompiledStateGraph:
    """에이전트 툴 루프 그래프 컴파일 전역 재사용"""
    builder = StateGraph(ChatState)
    builder.add_node(AGENT_NODE, _agent_node)
    builder.add_node(TOOLS_NODE, _tools_node)
    builder.add_edge(START, AGENT_NODE)
    builder.add_conditional_edges(AGENT_NODE, _route, {TOOLS_NODE: TOOLS_NODE, END: END})
    builder.add_edge(TOOLS_NODE, AGENT_NODE)
    return builder.compile()


def _chunk_text(chunk: AnyMessage) -> str:
    """스트리밍 청크에서 공백 보존 텍스트 추출"""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type", "text") == "text"
    )


def _text_of(message: AnyMessage) -> str:
    """모델 응답 블록 리스트에서 본문 텍스트 추출"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type", "text") == "text"
    ]
    return "".join(parts).strip()


def _initial_state(user_id: str, system_prompt: str, history: list[AnyMessage]) -> dict:
    """시스템 프롬프트와 대화 이력으로 그래프 입력 상태 조립"""
    return {
        "messages": [SystemMessage(content=system_prompt), *history],
        "user_id": user_id,
        "artifacts": [],
        "tool_turns": 0,
    }


def _finalize(state: dict) -> AgentResult:
    """최종 상태에서 본문과 마지막 아티팩트로 결과 확정"""
    content = ""
    for message in reversed(state.get("messages", [])):
        if message.type == "ai" and not getattr(message, "tool_calls", None):
            content = _text_of(message)
            break

    artifacts = state.get("artifacts") or []
    artifact = artifacts[-1] if artifacts else {}
    scheme, payload = guardrails.enforce_scheme_contract(
        artifact.get("response_scheme"), artifact.get("payload")
    )
    if not content:
        logger.warning("agent produced no text, using fallback")
        content = "요청을 처리했어요. 결과를 확인해보세요."
    return AgentResult(content, scheme, payload)


async def run_turn(user_id: str, system_prompt: str, history: list[AnyMessage]) -> AgentResult:
    """대화 한 턴 동기 실행 후 결과 반환"""
    state = await _graph().ainvoke(_initial_state(user_id, system_prompt, history))
    return _finalize(state)


async def run_turn_stream(user_id: str, system_prompt: str, history: list[AnyMessage]):
    """대화 한 턴 스트리밍 실행 본문 조각과 최종 결과 방출"""
    inputs = _initial_state(user_id, system_prompt, history)
    final_state: dict = {}
    async for mode, data in _graph().astream(inputs, stream_mode=["messages", "values"]):
        if mode == "values":
            final_state = data
            continue
        chunk, metadata = data
        if metadata.get("langgraph_node") != AGENT_NODE:
            continue
        text = _chunk_text(chunk)
        if text:
            yield GraphEvent.DELTA, text
    yield GraphEvent.RESULT, _finalize(final_state)
