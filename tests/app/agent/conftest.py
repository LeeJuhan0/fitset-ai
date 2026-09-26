import pytest

from app.core.schemas import ResponseScheme


@pytest.fixture
def markdown_answer() -> str:
    """헤더 목록 인용 강조 링크 섞인 LLM 답변"""
    return (
        "## 오늘 루틴\n"
        "- **스쿼트** 5x5\n"
        "- *벤치프레스* 3 * 10\n"
        "> 무릎이 발끝을 넘지 않게\n"
        "1. 워밍업 먼저\n"
        "---\n"
        "[가이드 영상](https://fitset.kro.kr/guide) 보기 `RPE 8` __꼭__ 지키기"
    )


@pytest.fixture
def plain_answer() -> str:
    """markdown_answer 기호 제거 후 기대 평문"""
    return (
        "오늘 루틴\n"
        "스쿼트 5x5\n"
        "벤치프레스 3 * 10\n"
        "무릎이 발끝을 넘지 않게\n"
        "1. 워밍업 먼저\n"
        "\n"
        "가이드 영상 보기 RPE 8 꼭 지키기"
    )


@pytest.fixture
def chart_payload() -> dict:
    """주별 운동 횟수 막대 차트 페이로드"""
    return {
        "chartType": "bar",
        "metric": "workoutFrequency",
        "title": "주별 운동 횟수",
        "xLabel": "주차",
        "yLabel": "횟수",
        "x": ["9/14", "9/21"],
        "series": [{"name": "운동 횟수", "values": [2, 1]}],
    }


@pytest.fixture
def payload_schemes() -> list[ResponseScheme]:
    """페이로드를 반드시 싣는 응답 스킴 목록"""
    return [ResponseScheme.CHART, ResponseScheme.EXERCISE_GIF, ResponseScheme.ROUTINE]
