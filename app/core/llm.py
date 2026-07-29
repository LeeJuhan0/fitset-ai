"""Bedrock 클라이언트 — LLM(converse)과 임베딩(Cohere Embed v4). core 레이어.

동기 함수로 제공한다 — async 서비스에서는 asyncio.to_thread로 감싸 호출한다.
임베딩 모델은 global.cohere.embed-v4:0 고정 (모델 교체 시 전량 재임베딩 필수 — CLAUDE.md).
"""
import json
from functools import lru_cache

import boto3
from botocore.config import Config

from app.core.config import get_settings


@lru_cache
def _runtime():
    settings = get_settings()
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
        config=Config(
            retries={"max_attempts": 5, "mode": "adaptive"},
            # 커넥션 풀을 executor 스레드 수와 맞춘다
            max_pool_connections=settings.executor_max_workers,
        ),
    )


def complete(system: str, user: str, max_tokens: int | None = None) -> str:
    """LLM 1회 호출 — converse API. 응답 텍스트를 그대로 반환한다."""
    settings = get_settings()
    response = _runtime().converse(
        modelId=settings.llm_model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": max_tokens or settings.llm_max_tokens, "temperature": 0.3},
    )
    return response["output"]["message"]["content"][0]["text"]


def embed_query(text: str) -> list[float]:
    """쿼리 1건 임베딩 — input_type=search_query (문서 임베딩과 짝을 이루는 검색용)."""
    settings = get_settings()
    body = {
        "texts": [text],
        "input_type": "search_query",
        "embedding_types": ["float"],
        "output_dimension": settings.embed_dimension,
    }
    response = _runtime().invoke_model(modelId=settings.embed_model_id, body=json.dumps(body))
    return json.loads(response["body"].read())["embeddings"]["float"][0]
