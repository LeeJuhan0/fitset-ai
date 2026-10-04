import logging

from langgraph.prebuilt.tool_node import ToolInvocationError

from app.agent.tools import chart, evidence, exercise, history, routine

logger = logging.getLogger("fitset")

TOOLS = [
    chart.draw_chart,
    routine.recommend_routine,
    exercise.get_exercise_guide,
    history.query_workout_history,
    evidence.find_evidence,
]


def tool_error_message(exc: Exception) -> str:
    """툴 실패를 LLM 재시도 안내 문구로 변환"""
    if isinstance(exc, ToolInvocationError):
        logger.info("tool got invalid args: %s", exc.message)
        return f"도구 인자가 올바르지 않습니다 ({exc.message}). 인자를 고쳐 다시 호출한다."
    logger.error("tool failed", exc_info=exc)
    return "도구 실행 중 오류가 발생했습니다. 이 사실을 알리고 도구 없이 답한다."
