from typing import Literal

from pydantic import BaseModel, Field

MAX_ITEMS = 5


class CurrentRequest(BaseModel):
    """대화방에서 가장 최근 유저 요청과 처리 상태"""

    text: str = Field(description="유저가 이번 대화방에서 마지막으로 한 요청, 예: 중급자 가슴 루틴 50분")
    status: Literal["in_progress", "done", "cancelled"] = Field(
        description="in_progress=아직 답하는 중이거나 되묻는 중, done=답이나 루틴을 이미 줌, cancelled=유저가 다른 요청으로 바꿈",
    )


class ThreadSummary(BaseModel):
    """다음 턴 문맥용 대화방 상태 구조"""

    current_request: CurrentRequest | None = Field(default=None, description="가장 최근 요청 하나. 새 요청이 나오면 이전 요청을 대체한다")
    decisions: list[str] = Field(default_factory=list, max_length=MAX_ITEMS, description="유저와 합의한 결정, 예: 오늘은 가슴 운동")
    constraints: list[str] = Field(default_factory=list, max_length=MAX_ITEMS, description="이 대화방에서 말한 일시적 제약, 예: 오늘 어깨가 조금 아픔")
    open_questions: list[str] = Field(default_factory=list, max_length=MAX_ITEMS, description="코치가 물었지만 유저가 아직 답하지 않은 것")


def render_thread_summary(summary: ThreadSummary) -> str | None:
    """대화방 상태를 채팅 문맥용 한국어 줄로"""
    lines = []
    request = summary.current_request
    if request is not None and request.status == "in_progress":
        lines.append(f"진행 중인 요청: {request.text}")
    if summary.decisions:
        lines.append(f"합의한 결정: {', '.join(summary.decisions)}")
    if summary.constraints:
        lines.append(f"이번 대화의 제약: {', '.join(summary.constraints)}")
    if summary.open_questions:
        lines.append(f"답을 기다리는 질문: {', '.join(summary.open_questions)}")
    return "\n".join(lines) or None
