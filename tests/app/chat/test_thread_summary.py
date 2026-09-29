from app.chat.summary import CurrentRequest, ThreadSummary, render_thread_summary


def test_빈_대화방_상태는_문맥에_넣지_않는다():
    assert render_thread_summary(ThreadSummary()) is None


def test_진행_중인_요청만_보여주고_끝나거나_취소된_요청은_뺀다():
    base = dict(decisions=["오늘은 가슴 운동"], open_questions=["운동 수준"])

    running = ThreadSummary(current_request=CurrentRequest(text="중급자 가슴 루틴 50분", status="in_progress"), **base)
    finished = ThreadSummary(current_request=CurrentRequest(text="40분 하체 루틴", status="cancelled"), **base)

    assert render_thread_summary(running) == "진행 중인 요청: 중급자 가슴 루틴 50분\n합의한 결정: 오늘은 가슴 운동\n답을 기다리는 질문: 운동 수준"
    assert render_thread_summary(finished) == "합의한 결정: 오늘은 가슴 운동\n답을 기다리는 질문: 운동 수준"


def test_채팅_문맥에는_대화방_상태만_넣는다():
    from app.agent.prompts import SUMMARY_DATA_NOTE, chat_context

    assert chat_context(None) is None
    assert chat_context("합의한 결정: 오늘은 가슴 운동") == f"<thread_summary>합의한 결정: 오늘은 가슴 운동</thread_summary>\n\n{SUMMARY_DATA_NOTE}"
