"""LangGraph 챗봇 그래프 — Bedrock 모델 + 툴콜 루프.

    START → agent ⇄ tools → END

agent가 툴을 부르면 tools에서 실행하고 다시 agent로 돌아온다. 툴을 안 부르면 종료.
왕복은 settings.agent_max_tool_turns로 제한한다 — 모델이 같은 툴을 반복 호출해
요청이 무한정 늘어지는 것을 막는다(클라 타임아웃 30s).

prebuilt ToolNode를 쓰지 않는 이유: 툴 결과에서 **텍스트(LLM용)와 payload(앱 렌더링용)를
분리해 들고 나와야** 하는데 ToolMessage는 문자열 하나뿐이다. artifacts 채널을 따로 두고
직접 디스패치한다. user_id도 툴 인자가 아니라 state에서 주입한다(사칭 방지).
"""
import logging
import operator
from functools import lru_cache
from typing import Annotated, TypedDict

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AnyMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.agent import guardrails, tools
from app.core import llm
from app.core.config import get_settings

logger = logging.getLogger("fitset")


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    artifacts: Annotated[list[dict], operator.add]
    tool_turns: int


class AgentResult:
    """그래프 1회 실행 결과 — 응답 본문과 §7 스킴·payload."""

    def __init__(self, content: str, response_scheme: str, payload: dict | None):
        self.content = content
        self.response_scheme = response_scheme
        self.payload = payload


def _model_provider(model_id: str) -> str:
    """모델 ID → 프로바이더 명. 추론 프로필 접두사(apac.·global. 등)가 붙으면
    langchain의 자동 감지가 접두사를 프로바이더로 오인할 수 있어 명시적으로 뽑는다."""
    parts = model_id.split(".")
    if parts and parts[0] in ("us", "eu", "apac", "global"):
        parts = parts[1:]
    return parts[0] if parts else "amazon"


@lru_cache
def _model():
    """Bedrock 채팅 모델. 툴 바인딩까지 끝난 핸들을 프로세스 전역으로 재사용한다.

    client 주입 필수 — 미주입 시 langchain이 기본 설정(read 60s·재시도 다수)으로
    자체 클라이언트를 만들어 core 타임아웃·풀 규약이 빠진다(감사 F15).
    """
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
    """모델을 호출해 답변 또는 툴콜을 받는다."""
    response = await _model().ainvoke(state["messages"])
    return {"messages": [response]}


async def _tools_node(state: ChatState) -> dict:
    """툴콜을 실행해 ToolMessage와 artifact를 함께 싣는다."""
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
    """툴콜이 남아있고 왕복 상한 안이면 tools로, 아니면 종료."""
    if state.get("tool_turns", 0) >= get_settings().agent_max_tool_turns:
        logger.warning("tool turn limit reached, ending turn")
        return END
    if getattr(state["messages"][-1], "tool_calls", None):
        return "tools"
    return END


@lru_cache
def _graph():
    """컴파일된 그래프 — 상태가 없어 프로세스 전역으로 재사용한다."""
    builder = StateGraph(ChatState)
    builder.add_node("agent", _agent_node)
    builder.add_node("tools", _tools_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", _route, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile()


def _text_of(message: AnyMessage) -> str:
    """모델 응답 본문 추출 — Converse는 content를 블록 리스트로 줄 수 있다."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type", "text") == "text"
    ]
    return "".join(parts).strip()


async def run_turn(user_id: str, system_prompt: str, history: list[AnyMessage]) -> AgentResult:
    """대화 1턴 실행. history 마지막이 이번 유저 발화다."""
    state = await _graph().ainvoke({
        "messages": [SystemMessage(content=system_prompt), *history],
        "user_id": user_id,
        "artifacts": [],
        "tool_turns": 0,
    })

    content = ""
    for message in reversed(state["messages"]):
        if message.type == "ai" and not getattr(message, "tool_calls", None):
            content = _text_of(message)
            break

    # 여러 툴을 불렀으면 마지막 artifact를 쓴다 — 화면에 붙는 카드는 하나뿐이다
    artifacts = state.get("artifacts") or []
    artifact = artifacts[-1] if artifacts else {}
    scheme, payload = guardrails.enforce_scheme_contract(
        artifact.get("response_scheme"), artifact.get("payload")
    )
    if not content:
        # 툴만 돌고 본문이 비는 경우 — 빈 말풍선을 내보내지 않는다
        logger.warning("agent produced no text, using fallback")
        content = "요청을 처리했어요. 결과를 확인해보세요."
    return AgentResult(content, scheme, payload)
