"""툴 레지스트리 — 그래프가 바인딩할 스키마와 이름→실행 함수 매핑.

툴은 3종이며 클라이언트 API 명세 §7의 payload 스킴과 1:1로 대응한다.
  DrawChart          → chart
  RecommendRoutine   → routine
  GetExerciseGuide   → exerciseGif

툴 이름은 bind_tools에 넘긴 pydantic 클래스명 그대로다 — prompts.CHAT_SYSTEM의
도구 사용 규칙에 적힌 이름과 반드시 일치해야 한다.

각 run(user_id, args)은 (LLM에 돌려줄 텍스트, artifact | None)을 반환한다.
artifact가 곧 응답 스킴·payload 후보이며, 최종 확정은 guardrails가 한다.
user_id는 툴 스키마에 넣지 않는다 — LLM이 남의 것을 조회하도록 유도될 수 있어
JWT에서 뽑은 값을 그래프가 직접 주입한다.
"""
import logging

from pydantic import ValidationError

from app.agent.tools import chart, exercise, routine

logger = logging.getLogger("fitset")

# bind_tools에 넘기는 pydantic 스키마 — 클래스 docstring이 툴 설명이 된다
TOOL_SCHEMAS = [chart.DrawChart, routine.RecommendRoutine, exercise.GetExerciseGuide]

# LLM이 부르는 툴 이름(=스키마 클래스명) → 실행 함수
TOOL_RUNNERS = {
    chart.DrawChart.__name__: chart.run,
    routine.RecommendRoutine.__name__: routine.run,
    exercise.GetExerciseGuide.__name__: exercise.run,
}


async def dispatch(user_id: str, name: str, args: dict) -> tuple[str, dict | None]:
    """이름으로 툴을 실행한다. 툴 안의 어떤 실패도 대화 턴을 죽이지 않는다.

    - 모르는 이름·인자 검증 실패 → 오류 내용을 LLM에 돌려줘 고쳐서 재시도하게 한다
      (예외를 올리면 graph 전체가 실패해 유저는 503을 본다 — 로컬 실측 사례)
    - 그 외 예외 → 로그 남기고 실패 사실만 전달, LLM이 도구 없이 답하게 한다
    """
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
