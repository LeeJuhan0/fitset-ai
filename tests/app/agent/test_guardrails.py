import pytest
from botocore.exceptions import ClientError
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.guardrails import (
    BLOCKED_REPLIES,
    CAPABILITY_REPLY,
    HARMFUL_REPLY,
    OFF_TOPIC_REPLY,
    REFUSAL_WINDOW,
    AnswerHold,
    PrescriptionHold,
    enforce_scheme_contract,
    forced_tool,
    invents_routine,
    scope_label,
    screen,
    screen_input,
)
from app.clients import bedrock
from app.core.schemas import ResponseScheme


def test_페이로드_스킴은_페이로드가_있으면_그대로_통과한다(chart_payload: dict, payload_schemes: list):
    for scheme in payload_schemes:
        result = enforce_scheme_contract(scheme, chart_payload)

        assert result == (scheme, chart_payload)


@pytest.mark.parametrize("payload", [None, {}])
def test_페이로드_스킴에_페이로드가_없으면_텍스트로_강등한다(payload, payload_schemes: list):
    for scheme in payload_schemes:
        result = enforce_scheme_contract(scheme, payload)

        assert result == (ResponseScheme.TEXT, None)


@pytest.mark.parametrize("scheme", [ResponseScheme.TEXT, None, "video"])
def test_페이로드가_없는_스킴은_실린_페이로드를_버리고_텍스트로_답한다(scheme, chart_payload: dict):
    result = enforce_scheme_contract(scheme, chart_payload)

    assert result == (ResponseScheme.TEXT, None)


@pytest.mark.parametrize("scheme", [ResponseScheme.TEXT, None])
def test_텍스트_답변은_그대로_텍스트로_답한다(scheme):
    result = enforce_scheme_contract(scheme, None)

    assert result == (ResponseScheme.TEXT, None)


def _blocked(topics=(), filters=()) -> dict:
    """테스트용 가드레일 차단 응답 생성"""
    return {
        "action": "GUARDRAIL_INTERVENED",
        "assessments": [{
            "topicPolicy": {"topics": [{"name": name, "action": "BLOCKED"} for name in topics]},
            "contentPolicy": {"filters": [{"type": name, "action": "BLOCKED"} for name in filters]},
        }],
    }


@pytest.fixture
def guardrail(monkeypatch, settings):
    """가드레일 활성화 후 테스트 응답 대체"""
    monkeypatch.setattr(settings, "guardrail_id", "gr-test")
    state = {"response": {"action": "NONE"}, "calls": []}

    def apply(text):
        state["calls"].append(text)
        if isinstance(state["response"], Exception):
            raise state["response"]
        return state["response"]

    monkeypatch.setattr(bedrock, "apply_guardrail", apply)
    return state


@pytest.mark.parametrize(("response", "reply"), [
    (_blocked(topics=["MedicalDiagnosis"]), BLOCKED_REPLIES["MedicalDiagnosis"]),
    (_blocked(topics=["Doping"], filters=["MISCONDUCT"]), BLOCKED_REPLIES["Doping"]),
    (_blocked(filters=["PROMPT_ATTACK"]), BLOCKED_REPLIES["PROMPT_ATTACK"]),
    (_blocked(filters=["INSULTS"]), HARMFUL_REPLY),
    ({"action": "GUARDRAIL_INTERVENED", "assessments": []}, HARMFUL_REPLY),
])
def test_가드레일이_막은_입력은_차단_이유에_맞는_짧은_답변을_돌려준다(guardrail, response, reply):
    guardrail["response"] = response

    assert screen_input("질문") == reply


def test_가드레일을_통과한_입력은_답변_없이_에이전트로_넘긴다(guardrail):
    assert screen_input("오늘 하체 뭐 할까?") is None
    assert guardrail["calls"] == ["오늘 하체 뭐 할까?"]


def test_가드레일_호출이_실패하면_입력을_막지_않는다(guardrail):
    guardrail["response"] = ClientError({"Error": {"Code": "ThrottlingException"}}, "ApplyGuardrail")

    assert screen_input("오늘 하체 뭐 할까?") is None


def test_가드레일_아이디가_없으면_검사하지_않는다(guardrail, settings):
    settings.guardrail_id = ""

    assert screen_input("지금 비트코인 사도 돼?") is None
    assert guardrail["calls"] == []


@pytest.fixture
def scope(monkeypatch, settings):
    """범위 판정 활성화 후 테스트 라벨 대체"""
    monkeypatch.setattr(settings, "scope_model_id", "scope-test")
    state = {"label": "IN", "calls": []}

    def classify(model_id, system, text):
        state["calls"].append(text)
        if isinstance(state["label"], Exception):
            raise state["label"]
        return state["label"]

    monkeypatch.setattr(bedrock, "classify", classify)
    return state


@pytest.mark.parametrize(("label", "expected"), [("IN", "IN"), ("OUT", "OUT"), ("out", "OUT"), ("ABOUT", "ABOUT"), ("모름", "IN")])
def test_범위_판정은_라벨_앞머리로_IN_ABOUT_OUT을_가른다(scope, label, expected):
    scope["label"] = label

    assert scope_label("질문") == expected
    assert scope["calls"] == ["<message>질문</message>"]


def test_범위_판정이_실패하거나_꺼져_있으면_막지_않는다(scope, settings):
    scope["label"] = ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")
    assert scope_label("질문") == "IN"

    settings.scope_model_id = ""
    assert scope_label("질문") == "IN"


@pytest.mark.parametrize(("label", "reply"), [("OUT", OFF_TOPIC_REPLY), ("ABOUT", CAPABILITY_REPLY)])
async def test_범위_밖이나_챗봇_자체를_물으면_고정_답변으로_막는다(guardrail, scope, label, reply):
    scope["label"] = label

    assert await screen("질문") == reply
async def test_가드레일이_막으면_가드레일_이유의_답변이_우선한다(guardrail, scope):
    guardrail["response"] = _blocked(topics=["MedicalDiagnosis"])
    scope["label"] = "OUT"

    assert await screen("무릎 아픈데 무슨 병이야?") == BLOCKED_REPLIES["MedicalDiagnosis"]


async def test_둘_다_통과하면_에이전트로_넘긴다(guardrail, scope):
    assert await screen("오늘 하체 뭐 할까?") is None


@pytest.mark.parametrize("answer", ["벤치프레스 4세트 해요", "8-12회 반복", "12회 반복하세요"])
def test_도구_없이_세트_횟수를_처방하면_지어낸_루틴이다(answer):
    assert invents_routine([HumanMessage("가슴 루틴"), AIMessage(answer)])


@pytest.mark.parametrize("answer", ["주 3회 운동하면 좋아요", "50분이면 충분해요"])
def test_세트_횟수_처방이_없으면_지어낸_루틴이_아니다(answer):
    assert not invents_routine([HumanMessage("질문"), AIMessage(answer)])


def test_이번_턴에_도구를_거친_답변은_지어낸_루틴이_아니다():
    messages = [HumanMessage("가슴 루틴"), AIMessage("", tool_calls=[{"name": "RecommendRoutine", "args": {}, "id": "c"}]),
                ToolMessage("루틴", tool_call_id="c"), AIMessage("벤치프레스 4세트예요")]

    assert not invents_routine(messages)


def test_보류는_첫_숫자부터_판정까지_잡아_둔다():
    hold = PrescriptionHold()

    assert hold.feed("추천해요. 벤치 ") == "추천해요. 벤치 "
    assert hold.feed("4세트 ") == ""
    assert hold.feed("해요") == ""
    assert hold.release() == "4세트 해요"
    assert hold.feed("다음 3") == "다음 "
    hold.drop()
    assert hold.release() == ""


@pytest.mark.parametrize("answer", [
    "죄송합니다. 사용자님의 운동 기록을 확인할 수 없어 알 수 없습니다.",
    "죄송합니다. 사용자님의 머신 레그프레스 기록을 확인할 수 없습니다.",
    "죄송합니다. 유저님의 운동 기록을 확인할 수 있는 도구가 없어서 알 수 없습니다.",
])
def test_도구_없이_기록_조회를_거절하면_기록_도구를_강제한다(answer):
    assert forced_tool([HumanMessage("나 벤치 한적 있어?"), AIMessage(answer)]) == "QueryWorkoutHistory"


def test_기록_도구를_거친_뒤_기록이_없다는_답변은_강제하지_않는다():
    messages = [HumanMessage("레그프레스 기록"), AIMessage("", tool_calls=[{"name": "QueryWorkoutHistory", "args": {}, "id": "c"}]),
                ToolMessage("조회 결과 없음", tool_call_id="c"), AIMessage("최근 기록이 없어요")]

    assert forced_tool(messages) is None


def test_답변_보류는_첫_구간에_거절이_없으면_흘려보낸다():
    hold = AnswerHold()
    head = "가" * REFUSAL_WINDOW

    assert hold.feed(head[:50]) == ""
    assert hold.feed(head[50:] + "나") == head + "나"
    assert hold.feed("다") == "다"


def test_답변_보류는_첫_구간에_거절이_있으면_판정까지_잡아_둔다():
    hold = AnswerHold()
    refusal = "죄송합니다. 운동 기록을 확인할 수 없어요." + "가" * REFUSAL_WINDOW

    assert hold.feed(refusal) == ""
    hold.drop()
    assert hold.release() == ""
    assert hold.feed("짧은 답") == ""
    assert hold.release() == "짧은 답"
