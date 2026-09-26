import pytest

from app.agent.guardrails import enforce_scheme_contract
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
