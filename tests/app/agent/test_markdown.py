import pytest

from app.agent.markdown import LINK_HOLD_LIMIT, MarkdownStreamFilter, strip_markdown


def _stream(text: str, size: int) -> str:
    """고정 길이 조각으로 흘려 넣은 필터 출력 전체"""
    stream_filter = MarkdownStreamFilter()
    pieces = [stream_filter.feed(text[start:start + size]) for start in range(0, len(text), size)]
    return "".join(pieces) + stream_filter.flush()


def test_마크다운_답변을_앱_표시용_평문으로_바꾼다(markdown_answer: str, plain_answer: str):
    text = markdown_answer

    plain = strip_markdown(text)

    assert plain == plain_answer


@pytest.mark.parametrize("text, expected", [
    ("### 제목", "제목"),
    ("- 항목\n+ 항목\n* 항목", "항목\n항목\n항목"),
    ("> 인용", "인용"),
    ("**굵게** __굵게__", "굵게 굵게"),
    ("*기울임*", "기울임"),
    ("[링크](https://a.b/c)", "링크"),
    ("`코드`", "코드"),
    ("```python\nprint(1)\n```", "print(1)\n"),
    ("앞\n***\n뒤", "앞\n\n뒤"),
])
def test_마크다운_기호를_제거한다(text: str, expected: str):
    plain = strip_markdown(text)

    assert plain == expected


@pytest.mark.parametrize("text", [
    "1. 첫째\n2. 둘째",
    "  들여쓴 줄",
    "2*3 은 6",
    "a * b",
    "#해시태그",
    "5-3=2",
])
def test_번호_목록_들여쓰기_곱셈_해시태그는_유지한다(text: str):
    plain = strip_markdown(text)

    assert plain == text


def test_들여쓴_목록은_기호만_빼고_들여쓰기를_유지한다():
    text = "  - 하위 항목"

    plain = strip_markdown(text)

    assert plain == "  하위 항목"


@pytest.mark.parametrize("size", [1, 2, 3, 5, 8, 1000])
def test_스트리밍_필터는_조각_크기와_무관하게_한번에_정제한_결과와_같다(
    markdown_answer: str,
    plain_answer: str,
    size: int,
):
    text = markdown_answer

    streamed = _stream(text, size)

    assert streamed == plain_answer


def test_스트리밍_필터는_쪼개진_굵게_기호를_다음_조각까지_보류한다():
    stream_filter = MarkdownStreamFilter()

    first = stream_filter.feed("**굵")
    second = stream_filter.feed("게*")
    third = stream_filter.feed("* 끝")

    assert (first, second, third, stream_filter.flush()) == ("굵", "게", " 끝", "")


def test_스트리밍_필터는_줄머리_헤더_기호를_다음_조각까지_보류한다():
    stream_filter = MarkdownStreamFilter()

    first = stream_filter.feed("##")
    second = stream_filter.feed(" 제목\n")

    assert (first, second) == ("", "제목\n")


def test_스트리밍_필터는_링크가_닫힐_때까지_글자를_보류한다():
    stream_filter = MarkdownStreamFilter()

    first = stream_filter.feed("보기 [가이")
    second = stream_filter.feed("드](https://a.b)")

    assert (first, second) == ("보기 ", "가이드")


def test_스트리밍_필터는_보류_한도를_넘긴_대괄호를_내보낸다():
    stream_filter = MarkdownStreamFilter()
    text = "[" + "가" * LINK_HOLD_LIMIT

    emitted = stream_filter.feed(text)

    assert emitted == text


def test_스트리밍_필터는_종료_시_보류분을_내보내고_비운다():
    stream_filter = MarkdownStreamFilter()
    stream_filter.feed("문장 끝 *")

    rest = stream_filter.flush()

    assert (rest, stream_filter.flush()) == ("*", "")
