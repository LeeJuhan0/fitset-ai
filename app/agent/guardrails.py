import asyncio
import logging
import re

from langchain_core.messages import AnyMessage

from app.agent import prompts
from app.clients import bedrock
from app.core.config import get_settings
from app.core.schemas import DEFAULT_SCHEME, PAYLOAD_SCHEMES

logger = logging.getLogger("fitset")

INTERVENED = "GUARDRAIL_INTERVENED"
OFF_TOPIC_REPLY = "저는 운동 코치라 그 이야기는 도와드리기 어려워요. 운동이나 식단 질문이면 바로 답해 드릴게요."
CAPABILITY_REPLY = "저는 FitSet 운동 코치예요. 루틴 추천, 자세 안내, 운동 기록 조회와 차트를 도와드려요."
BLOCKED_REPLIES = {
    "MedicalDiagnosis": "진단이나 약은 병원에서 상담해 주세요. 통증이 있을 때 운동 강도 조절은 도와드릴게요.",
    "Doping": "금지 약물이나 도핑은 안내하지 않아요. 안전한 훈련과 영양 방법은 도와드릴게요.",
    "PROMPT_ATTACK": "그 요청은 처리할 수 없어요. 운동 관련 질문을 보내 주세요.",
}
SCOPE_REPLIES = {"OUT": OFF_TOPIC_REPLY, "ABOUT": CAPABILITY_REPLY}
HARMFUL_REPLY = "그 요청에는 답하지 않을게요. 운동이나 건강 이야기라면 언제든 물어봐 주세요."
ROUTINE_TOOL = "RecommendRoutine"
PRESCRIPTION = re.compile(r"\d+\s*세트|\d+\s*[-~]\s*\d+\s*회|\d+\s*회\s*반복")
DIGIT = re.compile(r"\d")


def invents_routine(messages: list[AnyMessage]) -> bool:
    """도구 없이 세트 횟수를 처방한 답변 판정"""
    last = messages[-1]
    if last.type != "ai" or getattr(last, "tool_calls", None):
        return False
    for message in reversed(messages[:-1]):
        if message.type == "human":
            break
        if message.type == "tool":
            return False
    return bool(PRESCRIPTION.search(last.text))


class PrescriptionHold:
    """숫자부터 답변 판정 전까지 스트림 보류"""

    def __init__(self):
        """보류 중인 답변 조각 버퍼 초기화"""
        self._held = ""

    def feed(self, text: str) -> str:
        """조각 투입, 첫 숫자 이전 확정분 반환"""
        if self._held:
            self._held += text
            return ""
        match = DIGIT.search(text)
        if match is None:
            return text
        self._held = text[match.start():]
        return text[:match.start()]

    def release(self) -> str:
        """판정 통과한 보류분 전부 반환"""
        held, self._held = self._held, ""
        return held

    def drop(self) -> None:
        """지어낸 루틴으로 판정된 보류분 폐기"""
        self._held = ""


def enforce_scheme_contract(
    scheme: str | None, payload: dict | None
) -> tuple[str, dict | None]:
    """응답 스킴 계약 검증 위반 시 텍스트 강등"""
    if scheme not in PAYLOAD_SCHEMES:
        if payload is not None:
            logger.warning("scheme %r carried a payload, dropping it", scheme)
        return DEFAULT_SCHEME, None
    if not payload:
        logger.info("scheme %s had no payload, downgrading to text", scheme)
        return DEFAULT_SCHEME, None
    return scheme, payload


def blocked_reason(response: dict) -> str | None:
    """가드레일 응답 차단 주제 필터 이름 추출"""
    if response.get("action") != INTERVENED:
        return None
    for assessment in response.get("assessments", []):
        for topic in assessment.get("topicPolicy", {}).get("topics", []):
            if topic.get("action") == "BLOCKED":
                return topic["name"]
        for content_filter in assessment.get("contentPolicy", {}).get("filters", []):
            if content_filter.get("action") == "BLOCKED":
                return content_filter["type"]
    return ""


def screen_input(text: str) -> str | None:
    """유저 입력 가드레일 검사 후 차단 템플릿 반환"""
    if not get_settings().guardrail_id:
        return None
    try:
        response = bedrock.apply_guardrail(text)
    except bedrock.LLM_ERRORS:
        logger.warning("guardrail check failed, passing input through", exc_info=True)
        return None
    reason = blocked_reason(response)
    if reason is None:
        return None
    logger.info("guardrail blocked input: %s", reason or "unknown")
    return BLOCKED_REPLIES.get(reason, HARMFUL_REPLY)


def scope_label(text: str) -> str:
    """범위 판정 IN ABOUT OUT 라벨 반환"""
    model_id = get_settings().scope_model_id
    if not model_id:
        return "IN"
    try:
        label = bedrock.classify(model_id, prompts.SCOPE_SYSTEM, f"<message>{text}</message>").upper()
    except bedrock.LLM_ERRORS:
        logger.warning("scope check failed, passing input through", exc_info=True)
        return "IN"
    return next((name for name in SCOPE_REPLIES if label.startswith(name)), "IN")


async def screen(text: str) -> str | None:
    """가드레일 범위 판정 동시 실행 후 템플릿 반환"""
    reply, label = await asyncio.gather(asyncio.to_thread(screen_input, text), asyncio.to_thread(scope_label, text))
    if label == "ABOUT":
        return CAPABILITY_REPLY
    if reply is not None:
        return reply
    if label in SCOPE_REPLIES:
        logger.info("scope check blocked input: %s", label)
    return SCOPE_REPLIES.get(label)
