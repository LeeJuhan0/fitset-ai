import logging

from pydantic import ValidationError

from app.agent.tools import chart, exercise, history, routine

logger = logging.getLogger("fitset")

_TOOLS = [
    (chart.DrawChart, chart.run),
    (routine.RecommendRoutine, routine.run),
    (exercise.GetExerciseGuide, exercise.run),
    (history.QueryWorkoutHistory, history.run),
]

TOOL_SCHEMAS = [schema for schema, _ in _TOOLS]
TOOL_RUNNERS = {schema.__name__: runner for schema, runner in _TOOLS}


async def dispatch(user_id: str, name: str, args: dict) -> tuple[str, dict | None]:
    """이름으로 툴 실행 실패는 안내 문구로 변환"""
    runner = TOOL_RUNNERS.get(name)
    if runner is None:
        return f"'{name}' 도구는 없습니다. 도구 없이 답한다.", None
    try:
        return await runner(user_id, args)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        logger.info("tool %s got invalid args: %s", name, problems)
        return f"도구 인자가 올바르지 않습니다 ({problems}). 인자를 고쳐 다시 호출한다.", None
    except Exception:
        logger.exception("tool %s failed", name)
        return "도구 실행 중 오류가 발생했습니다. 이 사실을 알리고 도구 없이 답한다.", None
