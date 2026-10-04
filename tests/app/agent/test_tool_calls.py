from langchain_core.messages import AIMessage

from app.agent import graph


def _call(call_id: str) -> dict:
    """차트 툴콜 1건"""
    return {"name": "DrawChart", "args": {"metric": "volume"}, "id": call_id, "type": "tool_call"}


def test_병렬_툴콜은_첫_하나만_남긴다():
    response = AIMessage(
        content=[
            {"type": "text", "text": "차트를 그릴게요"},
            {"type": "tool_use", "id": "a", "name": "DrawChart", "input": {}},
            {"type": "tool_use", "id": "b", "name": "DrawChart", "input": {}},
        ],
        tool_calls=[_call("a"), _call("b")],
    )

    trimmed = graph._first_tool_call_only(response)

    assert [call["id"] for call in trimmed.tool_calls] == ["a"]
    assert [block.get("id") for block in trimmed.content] == [None, "a"]


def test_툴콜이_하나면_그대로_둔다():
    response = AIMessage(content="", tool_calls=[_call("a")])

    assert graph._first_tool_call_only(response) is response
