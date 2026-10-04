import logging
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Callable, NotRequired, TypedDict

from botocore.exceptions import BotoCoreError, ClientError
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AIMessage, AnyMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from app.agent import guardrails, prompts, tools
from app.agent.markdown import InternalBlockFilter, MarkdownStreamFilter, strip_internal_blocks, strip_markdown
from app.clients import bedrock
from app.core.config import get_settings

logger = logging.getLogger("fitset")

AGENT_NODE = "agent"
CACHE_POINT = {"cachePoint": {"type": "default"}}
GUARD_NODE = "guard"
TOOLS_NODE = "tools"


class GraphEvent(StrEnum):
    DELTA = "delta"
    RESULT = "result"


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    force_tool: NotRequired[str | None]


@dataclass(frozen=True)
class AgentResult:
    content: str
    response_scheme: str
    payload: dict | None


@lru_cache
def _chat_model(model_id: str) -> ChatBedrockConverse:
    """모델 아이디별 Bedrock 채팅 모델 싱글턴"""
    settings = get_settings()
    return ChatBedrockConverse(
        model=model_id,
        provider=bedrock.model_provider(model_id),
        region_name=settings.aws_region,
        client=bedrock.chat_runtime(),
        max_tokens=settings.chat_max_tokens,
        temperature=0.3,
    )


def _without_cache_points(messages: list[AnyMessage]) -> list[AnyMessage]:
    """캐시 미지원 폴백용 시스템 메시지 캐시 지점 제거"""
    return [
        message.model_copy(update={"content": [block for block in message.content if block != CACHE_POINT]})
        if isinstance(message, SystemMessage) and isinstance(message.content, list)
        else message
        for message in messages
    ]


def _with_fallbacks(bind: Callable[[ChatBedrockConverse], Runnable]) -> Runnable:
    """주 모델 호출 실패 시 폴백 모델 순서대로 재시도"""
    settings = get_settings()
    fallbacks = [
        RunnableLambda(_without_cache_points) | bind(_chat_model(model_id))
        for model_id in settings.chat_fallback_model_ids
    ]
    return bind(_chat_model(settings.chat_model_id)).with_fallbacks(
        fallbacks, exceptions_to_handle=(BotoCoreError, ClientError)
    )


@lru_cache
def _model() -> Runnable:
    """툴 바인딩된 채팅 모델과 폴백 전역 싱글턴"""
    return _with_fallbacks(lambda model: model.bind_tools(tools.TOOLS))


@lru_cache
def _forced_model(tool: str) -> Runnable:
    """지정 도구 호출을 강제한 채팅 모델과 폴백"""
    return _with_fallbacks(lambda model: model.bind_tools(tools.TOOLS, tool_choice=tool))


async def _agent_node(state: ChatState) -> dict:
    """모델 호출로 답변 또는 툴콜 수신"""
    tool = state.get("force_tool")
    model = _forced_model(tool) if tool else _model()
    response = _first_tool_call_only(await model.ainvoke(state["messages"]))
    return {"messages": [response], "force_tool": None}


def _first_tool_call_only(response: AIMessage) -> AIMessage:
    """병렬 툴콜을 첫 하나로 줄여 화면 결과와 답변 맞춤"""
    if len(response.tool_calls) <= 1:
        return response
    keep = response.tool_calls[0]["id"]
    content = response.content
    if isinstance(content, list):
        content = [
            block for block in content
            if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") != keep)
        ]
    return response.model_copy(update={"content": content, "tool_calls": response.tool_calls[:1]})


def _guard_node(state: ChatState) -> dict:
    """도구를 건너뛴 답변 제거 후 도구 강제"""
    tool = guardrails.forced_tool(state["messages"])
    logger.warning("agent answered without %s, retrying with it", tool)
    return {"messages": [RemoveMessage(id=state["messages"][-1].id)], "force_tool": tool}


def tool_node() -> ToolNode:
    """툴 실행 노드, 유저 아이디 주입과 실패 문구 변환"""
    return ToolNode(tools.TOOLS, handle_tool_errors=tools.tool_error_message)


def _tool_turns(messages: list[AnyMessage]) -> int:
    """이번 턴 툴콜 요청 횟수"""
    return sum(1 for message in messages if getattr(message, "tool_calls", None))


def _route(state: ChatState) -> str:
    """툴콜 유무, 왕복 상한, 도구 건너뛴 답변으로 다음 노드 결정"""
    if not getattr(state["messages"][-1], "tool_calls", None):
        return GUARD_NODE if guardrails.forced_tool(state["messages"]) else END
    if _tool_turns(state["messages"]) > get_settings().agent_max_tool_turns:
        logger.warning("tool turn limit reached, ending turn")
        return END
    return TOOLS_NODE


@lru_cache
def _graph() -> CompiledStateGraph:
    """에이전트 툴 루프 그래프 컴파일 전역 재사용"""
    builder = StateGraph(ChatState)
    builder.add_node(AGENT_NODE, _agent_node)
    builder.add_node(TOOLS_NODE, tool_node())
    builder.add_node(GUARD_NODE, _guard_node)
    builder.add_edge(START, AGENT_NODE)
    builder.add_conditional_edges(AGENT_NODE, _route, {TOOLS_NODE: TOOLS_NODE, GUARD_NODE: GUARD_NODE, END: END})
    builder.add_edge(GUARD_NODE, AGENT_NODE)
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


def _system_message(context: str | None) -> SystemMessage:
    """고정 프롬프트 뒤 캐시 지점, 그 뒤 유저별 요약"""
    blocks = [{"type": "text", "text": prompts.CHAT_SYSTEM.strip()}, CACHE_POINT]
    if context:
        blocks.append({"type": "text", "text": context})
    return SystemMessage(content=blocks)


def _initial_state(user_id: str, context: str | None, history: list[AnyMessage]) -> dict:
    """시스템 메시지와 대화 이력으로 그래프 입력 상태 조립"""
    return {
        "messages": [_system_message(context), *history],
        "user_id": user_id,
    }


def _log_usage(state: dict) -> None:
    """이번 턴 모델 호출 토큰, 캐시 적중량 로그"""
    for message in state.get("messages", []):
        usage = getattr(message, "usage_metadata", None)
        if message.type != "ai" or not usage:
            continue
        details = usage.get("input_token_details") or {}
        logger.info(
            "llm usage input=%s output=%s cache_read=%s cache_write=%s",
            usage.get("input_tokens"), usage.get("output_tokens"),
            details.get("cache_read"), details.get("cache_creation"),
        )


def _last_artifact(messages: list[AnyMessage]) -> dict:
    """마지막 툴 메시지 아티팩트, 없으면 빈 dict"""
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.artifact:
            return message.artifact
    return {}


def _finalize(state: dict) -> AgentResult:
    """최종 상태에서 본문과 마지막 아티팩트로 결과 확정"""
    content = ""
    for message in reversed(state.get("messages", [])):
        if message.type == "ai" and not getattr(message, "tool_calls", None):
            content = _text_of(message)
            break

    content = strip_markdown(strip_internal_blocks(content)).strip()
    artifact = _last_artifact(state.get("messages", []))
    scheme, payload = guardrails.enforce_scheme_contract(
        artifact.get("response_scheme"), artifact.get("payload")
    )
    if not content:
        logger.warning("agent produced no text, using fallback")
        content = "요청을 처리했어요. 결과를 확인해보세요."
    return AgentResult(content, scheme, payload)


async def run_turn(user_id: str, context: str | None, history: list[AnyMessage]) -> AgentResult:
    """대화 한 턴 동기 실행 후 결과 반환"""
    state = await _graph().ainvoke(_initial_state(user_id, context, history))
    _log_usage(state)
    return _finalize(state)


def _judge_held(hold: guardrails.AnswerHold, messages: list[AnyMessage]) -> str:
    """모델 응답 완료 시 보류분 방출 또는 폐기"""
    if messages[-1].type != "ai":
        return ""
    if guardrails.forced_tool(messages):
        hold.drop()
        return ""
    return hold.release()


async def run_turn_stream(user_id: str, context: str | None, history: list[AnyMessage]):
    """대화 한 턴 스트리밍 실행 본문 조각과 최종 결과 방출"""
    inputs = _initial_state(user_id, context, history)
    final_state: dict = {}
    markdown = MarkdownStreamFilter()
    internal = InternalBlockFilter()
    hold = guardrails.AnswerHold()
    async for mode, data in _graph().astream(inputs, stream_mode=["messages", "values"]):
        if mode == "values":
            final_state = data
            released = _judge_held(hold, data["messages"])
            if released:
                yield GraphEvent.DELTA, released
            continue
        chunk, metadata = data
        if metadata.get("langgraph_node") != AGENT_NODE:
            continue
        text = hold.feed(markdown.feed(internal.feed(_chunk_text(chunk))))
        if text:
            yield GraphEvent.DELTA, text
    tail = hold.feed(markdown.feed(internal.flush()) + markdown.flush()) + hold.release()
    if tail:
        yield GraphEvent.DELTA, tail
    _log_usage(final_state)
    yield GraphEvent.RESULT, _finalize(final_state)
