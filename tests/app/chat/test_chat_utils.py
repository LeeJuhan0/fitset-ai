from datetime import timedelta

import pytest

from app.chat import utils
from app.chat.enums import MessageRole
from app.chat.models import MessageRecord, ThreadRecord
from tests.conftest import NOW


def test_보존_기한은_마지막_활동_시각에_보존_일수를_더한_epoch_초다():
    last_message_at = NOW

    expires_at = utils.ttl_epoch(last_message_at, days=14)

    assert expires_at == int((NOW + timedelta(days=14)).timestamp())


def test_대화_목록은_최근_활동순으로_정렬하고_활동이_없는_대화는_맨_뒤에_둔다(activity_threads: list[ThreadRecord]):
    threads = activity_threads

    ordered = utils.sorted_by_activity(threads)

    assert [thread.thread_id for thread in ordered] == ["01B", "01D", "01A", "01C"]


def test_LLM_이력은_답변이_없는_유저_메시지를_뺀다(unanswered_history: list[MessageRecord]):
    messages = unanswered_history

    kept = utils.context_messages(messages)

    assert [message.content for message in kept] == ["스쿼트 몇 세트 해요", "5세트요", "데드는요", "3세트요"]


def test_LLM_이력은_모두_답한_대화를_그대로_둔다(unanswered_history: list[MessageRecord]):
    messages = unanswered_history[:2]

    kept = utils.context_messages(messages)

    assert kept == messages


def test_LLM_이력은_빈_대화면_비어_있다():
    kept = utils.context_messages([])

    assert kept == []


@pytest.mark.parametrize("content, expected", [
    ("  가\n  나   다 ", "가 나 다"),
    ("가나다라마", "가나다라마"),
    ("가나다라마바", "가나다라마…"),
    ("가나다라 마바", "가나다라…"),
])
def test_폴백_제목은_공백을_정리하고_최대_길이를_넘으면_말줄임한다(content: str, expected: str):
    title = utils.fallback_title(content, max_length=5)

    assert title == expected


@pytest.mark.parametrize("count, expected", [(9, False), (10, True), (11, True)])
def test_요약은_미요약_메시지가_임계치에_닿으면_필요하다(count: int, expected: bool):
    result = utils.needs_summary(count, threshold=10)

    assert result is expected


def test_답변_메시지_응답은_스킴과_순수_dict_페이로드를_싣는다(chart_message: MessageRecord):
    message = chart_message

    view = utils.to_message_view(message)

    assert view == {
        "message_id": message.message_id,
        "role": MessageRole.ASSISTANT,
        "content": "이번 달 기록이에요",
        "response_scheme": "chart",
        "payload": {"chartType": "bar", "x": ["9/14", "9/21"], "series": [{"name": "운동 횟수", "values": [2, 1]}]},
        "created_at": message.created_at,
    }
    assert type(view["payload"]) is dict


def test_유저_메시지_응답은_스킴과_페이로드를_싣지_않는다(chart_message: MessageRecord):
    message = chart_message
    message.role = MessageRole.USER

    view = utils.to_message_view(message)

    assert (view["response_scheme"], view["payload"]) == (None, None)


def test_메시지_응답은_비어_있는_본문과_시각을_빈_문자열로_채운다(chart_message: MessageRecord):
    message = chart_message
    message.content = None
    message.created_at = None

    view = utils.to_message_view(message)

    assert (view["content"], view["created_at"]) == ("", "")
