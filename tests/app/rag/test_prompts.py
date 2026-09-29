import pytest

from app.rag import prompts
from app.rag.query import clean_query_analysis


def test_묘사문_변환_프롬프트는_요청을_한글_라벨로_풀고_문맥을_태그로_감싼다():
    system, user = prompts.describe_query("hypertrophy", "beginner", ["quadriceps", "glutes"], 60, "무릎이 아파요")

    assert system == prompts.DESCRIBE_SYSTEM
    assert user == (
        "목표: 근비대\n수준: 초급\n부위: 대퇴사두근, 둔근\n운동 시간: 60분\n"
        "<user_context>무릎이 아파요</user_context>"
    )


def test_묘사문_변환_프롬프트는_문맥이_없으면_빈_태그를_둔다():
    _, user = prompts.describe_query("strength", "advanced", ["chest"], 45, None)

    assert user.endswith("<user_context></user_context>")


def test_템플릿_묘사문은_부위_수준_목표_시간으로_조립한다():
    description = prompts.fallback_description("weightLoss", "intermediate", ["chest", "triceps"], 45)

    assert description == "가슴, 삼두근 부위를 단련하는 중급 45분 체중감량 루틴입니다."


def test_템플릿_묘사문은_라벨이_없는_값을_그대로_쓴다():
    description = prompts.fallback_description("power", "expert", ["neck"], 30)

    assert description == "neck 부위를 단련하는 expert 30분 power 루틴입니다."


def test_최종_선택_프롬프트는_후보에_번호를_매겨_요약한다(candidates: list[dict]):
    system, user = prompts.pick_best(candidates, "하체 근비대 루틴")

    assert system == prompts.PICK_SYSTEM
    assert user == (
        "사용자 요청: 하체 근비대 루틴\n\n후보:\n"
        "1. [초급/근비대/60분] 부위: 대퇴사두근, 둔근 | 종목: 바벨 스쿼트, 런지\n"
        "2. [중급/근력향상/70분] 부위: 대퇴사두근 | 종목: 프론트 스쿼트\n"
        "3. [초급/체력유지/50분] 부위: 햄스트링 | 종목: 루마니안 데드리프트"
    )


def test_최종_선택_프롬프트는_종목을_8개까지만_싣는다(candidates: list[dict]):
    candidate = {**candidates[0], "exercise_names": [f"종목{index}" for index in range(10)]}

    _, user = prompts.pick_best([candidate], "하체")

    assert user.endswith("종목: 종목0, 종목1, 종목2, 종목3, 종목4, 종목5, 종목6, 종목7")


def test_분석_결과_정제는_묘사문_공백을_다듬고_허용_부위와_빈칸_아닌_제약만_남긴다():
    result = clean_query_analysis("  스쿼트 위주 하체 루틴 ", ["hamstrings", "knee", "back"], ["점프 금지", "  "])

    assert result == ("스쿼트 위주 하체 루틴", {"hamstrings", "back"}, ["점프 금지"])


@pytest.mark.parametrize("description", ["", "   \n"])
def test_분석_결과_정제는_묘사문이_비면_나머지도_버린다(description: str):
    result = clean_query_analysis(description, ["hamstrings"], ["점프 금지"])

    assert result == (None, set(), [])
