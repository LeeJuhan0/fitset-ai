import pytest

from app.agent.markdown import InternalBlockFilter, LINK_HOLD_LIMIT, MarkdownStreamFilter, strip_internal_blocks, strip_markdown


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


@pytest.mark.parametrize("size", [1, 2, 3, 5, 8])
def test_스트리밍_필터는_언어_태그가_붙은_코드_펜스를_쪼개져도_지운다(size: int):
    text = "예시\n```python\nprint(1)\n```\n끝"

    streamed = _stream(text, size)

    assert streamed == strip_markdown(text) == "예시\nprint(1)\n끝"


def test_스트리밍_필터는_코드_펜스_줄을_줄바꿈까지_보류한다():
    stream_filter = MarkdownStreamFilter()

    first = stream_filter.feed("```py")
    second = stream_filter.feed("thon\nprint(1)")

    assert (first, second) == ("", "print(1)")


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


def _stream_blocks(chunks: list[str]) -> str:
    """조각을 내부 블록 필터에 차례로 넣은 전체 출력"""
    blocks = InternalBlockFilter()
    return "".join(blocks.feed(chunk) for chunk in chunks) + blocks.flush()


def test_답변에_새어_나온_대화방_상태_블록을_지운다():
    text = "그래프로 보여드릴게요. <thread_summary>진행 중인 요청: PR 확인</thread_summary>"

    assert strip_internal_blocks(text) == "그래프로 보여드릴게요. "


def test_쪼개져_들어온_내부_블록도_스트림에서_지운다():
    chunks = ["보여드릴게요. <thr", "ead_summ", "ary>진행 중인 요청", "</thread_sum", "mary> 끝"]

    assert _stream_blocks(chunks) == "보여드릴게요.  끝"


def test_내부_태그가_아닌_부등호는_그대로_보낸다():
    assert _stream_blocks(["세트 수 5 <", " 10 이면 ", "<b>굵게</b>"]) == "세트 수 5 < 10 이면 <b>굵게</b>"


def test_닫히지_않은_내부_블록은_끝까지_버린다():
    assert _stream_blocks(["좋아요 <thread_summary>합의한 결정: 하체"]) == "좋아요 "
