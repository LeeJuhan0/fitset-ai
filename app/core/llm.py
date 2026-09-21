import json
import logging
from functools import lru_cache

import boto3
from botocore.config import Config

from app.core.config import get_settings

logger = logging.getLogger("fitset")


@lru_cache
def _runtime():
    """루틴 경로용 Bedrock 런타임 클라이언트 전역 싱글턴"""
    settings = get_settings()
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
        config=Config(
            retries={"max_attempts": settings.bedrock_max_attempts, "mode": "adaptive"},
            connect_timeout=settings.bedrock_connect_timeout,
            read_timeout=settings.bedrock_read_timeout,
            max_pool_connections=settings.executor_max_workers,
        ),
    )


@lru_cache
def chat_runtime():
    """채팅 에이전트용 Bedrock 런타임 클라이언트 전역 싱글턴"""
    settings = get_settings()
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
        config=Config(
            retries={"max_attempts": settings.bedrock_max_attempts, "mode": "adaptive"},
            connect_timeout=settings.bedrock_connect_timeout,
            read_timeout=settings.bedrock_chat_read_timeout,
            max_pool_connections=settings.executor_max_workers,
        ),
    )


def complete(system: str, user: str, max_tokens: int | None = None) -> str:
    """Converse API 단발 호출 후 응답 텍스트 반환"""
    settings = get_settings()
    response = _runtime().converse(
        modelId=settings.llm_model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": max_tokens or settings.llm_max_tokens, "temperature": 0.3},
    )
    return response["output"]["message"]["content"][0]["text"]


def try_complete(purpose: str, system: str, user: str, max_tokens: int | None = None) -> str | None:
    """LLM 호출 실패 시 로그 기록 후 None 반환"""
    try:
        return complete(system, user, max_tokens)
    except Exception:
        logger.warning("%s LLM failed, falling back", purpose, exc_info=True)
        return None


def embed_query(text: str) -> list[float]:
    """검색 쿼리 1건 Cohere 임베딩 벡터 생성"""
    settings = get_settings()
    body = {
        "texts": [text],
        "input_type": "search_query",
        "embedding_types": ["float"],
        "output_dimension": settings.embed_dimension,
    }
    response = _runtime().invoke_model(modelId=settings.embed_model_id, body=json.dumps(body))
    return json.loads(response["body"].read())["embeddings"]["float"][0]


def try_embed_query(text: str) -> list[float] | None:
    """쿼리 임베딩 실패 시 로그 기록 후 None 반환"""
    try:
        return embed_query(text)
    except Exception:
        logger.exception("query embedding failed")
        return None
