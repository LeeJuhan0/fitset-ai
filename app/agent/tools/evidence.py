import asyncio
import logging

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.clients import bedrock, postgres
from app.core.exceptions import DomainError
from app.evidence import repository

logger = logging.getLogger("fitset")

TOP_K = 3
MAX_DISTANCE = 0.55
RELATIVE_MARGIN = 0.1
NO_EVIDENCE = "관련 논문 근거가 없다. 일반 상식으로 짧게 답하고 논문이나 연구를 인용하지 않는다."


class FindEvidence(BaseModel):
    """운동, 식단, 영양, 회복, 보충제 방법이 효과 있는지, 얼마나 어떻게 해야 하는지 묻는 질문에 논문 근거를 찾는다.

    세트 수, 휴식 시간, 반복 횟수, 빈도, 단백질량, 보충제, 다이어트, 회복처럼 답을 안다고 생각되는 상식 질문도 답하기 전에 먼저 부른다.
    기록 조회, 차트, 루틴 추천, 종목 자세, 인사와 잡담에는 쓰지 않는다.
    """

    situation: str = Field(
        max_length=120,
        description=(
            "유저가 처한 상황과 고민을 사용자 말투 한 문장으로, '사람'으로 끝낸다. "
            "대화와 기록에서 알 수 있는 빈도, 세트, 무게는 넣고 결론은 쓰지 않는다. "
            "예: 근육을 키우려고 매 세트 실패 지점까지 해야 하는지 고민하는 사람"
        ),
    )


def _author_year(paper: str) -> str:
    """논문 이름 2017_Jager_ISSN-protein 에서 저자 연도"""
    year, author, *_ = paper.split("_")
    return f"{author} {year}"


def _format(rows: list[dict]) -> str:
    """근거 청크를 LLM 답변 근거 문단으로"""
    blocks = [
        f"[{index}] {row['title']}\n대상: {row['population']}\n방법: {row['method']}\n효과: {row['effect']}"
        for index, row in enumerate(rows, start=1)
    ]
    return (
        "논문 근거다. 이 내용만 근거로 결론과 핵심 근거를 반드시 해요체(~해요, ~예요) 두 문장으로만 답한다. 반말을 쓰지 않는다. "
        "근거에 없는 수치를 만들지 않고, 연구 대상이 유저와 다르면 그 한계를 짧게 덧붙인다. "
        "논문 이름, 저자, 링크와 근거별 대상, 방법, 효과는 쓰지 않는다. 앱이 답변 아래에 참고 논문 목록으로 붙인다.\n\n" + "\n\n".join(blocks)
    )


def footer(citations: list[dict]) -> str:
    """답변 아래 붙이는 참고 논문 고정 템플릿"""
    if not citations:
        return ""
    lines = [
        f"{index}. {item['title']} ({item['author_year']})\n"
        f"대상: {item['population']}\n방법: {item['method']}\n효과: {item['effect']}\n{item['doi_url']}"
        for index, item in enumerate(citations, start=1)
    ]
    return "\n\n참고 논문\n" + "\n".join(lines)


async def run(situation: str) -> tuple[str, dict | None]:
    """상황 문장 임베딩 후 근거 청크와 인용 목록 반환"""
    if not postgres.is_configured():
        return NO_EVIDENCE, None
    vector = await asyncio.to_thread(bedrock.try_embed_query, situation)
    if vector is None:
        return NO_EVIDENCE, None
    try:
        rows = await asyncio.to_thread(repository.search, vector, TOP_K, MAX_DISTANCE)
    except DomainError:
        logger.warning("evidence search failed", exc_info=True)
        return NO_EVIDENCE, None
    logger.info("evidence search hits=%s", [(row["id"], round(row["distance"], 3)) for row in rows])
    rows = [row for row in rows if row["distance"] <= rows[0]["distance"] + RELATIVE_MARGIN] if rows else []
    if not rows:
        return NO_EVIDENCE, None
    citations = [
        {
            "id": row["id"], "title": row["title"], "author_year": _author_year(row["paper"]),
            "population": row["population"], "method": row["method"], "effect": row["effect"], "doi_url": row["doi_url"],
        }
        for row in rows
    ]
    return _format(rows), {"citations": citations}


@tool(
    "FindEvidence",
    args_schema=FindEvidence,
    description=FindEvidence.__doc__,
    response_format="content_and_artifact",
)
async def find_evidence(situation: str) -> tuple[str, dict | None]:
    """논문 근거 도구 진입점, run 위임"""
    return await run(situation)
