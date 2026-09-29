import pytest
from botocore.exceptions import ClientError

from app.agent.guardrails import (
    BLOCKED_REPLIES,
    CAPABILITY_REPLY,
    HARMFUL_REPLY,
    OFF_TOPIC_REPLY,
    enforce_scheme_contract,
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
