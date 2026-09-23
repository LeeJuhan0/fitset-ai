import random

import pytest

from app.agent.markdown import MarkdownStreamFilter, strip_markdown

SAMPLES = [
    "**가슴** 루틴이에요.\n\n## 구성\n- 벤치프레스 3세트\n- *인클라인* 덤벨 프레스\n1. 워밍업부터 하세요.",
    "3 * 4 = 12, 2*3도 유지. `코드`와 [링크](https://x.com)\n---\n> 인용",
    "무게는 __80kg__ 입니다. 파일명 bench_press 유지",
]


def test_strip_markdown_removes_symbols_keeps_numbered_list_and_math():
    assert strip_markdown(SAMPLES[0]) == "가슴 루틴이에요.\n\n구성\n벤치프레스 3세트\n인클라인 덤벨 프레스\n1. 워밍업부터 하세요."
    assert strip_markdown(SAMPLES[1]) == "3 * 4 = 12, 2*3도 유지. 코드와 링크\n\n인용"
    assert strip_markdown(SAMPLES[2]) == "무게는 80kg 입니다. 파일명 bench_press 유지"


@pytest.mark.parametrize("sample", SAMPLES)
def test_stream_filter_matches_full_text_for_any_split(sample):
    # SSE 조각은 토큰 단위로 잘려 온다. 어디서 잘려도 전체를 한 번에 정제한 결과와 같아야 한다
    for seed in range(300):
        rng = random.Random(seed)
        stream = MarkdownStreamFilter()
        out, index = "", 0
        while index < len(sample):
            size = rng.randint(1, 6)
            out += stream.feed(sample[index:index + size])
            index += size
        out += stream.flush()
        assert out == strip_markdown(sample)
