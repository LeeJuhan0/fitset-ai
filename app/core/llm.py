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
            retries={"max_attempts": settings.bedrock_max_attempts, "mode": "adaptive"},
            # 호출 1회의 스레드 점유 상한 — 기본값(read 60s × 5회)이면 클라가 끊은 뒤에도 스레드가 물린다
            connect_timeout=settings.bedrock_connect_timeout,
            read_timeout=settings.bedrock_read_timeout,
            # 커넥션 풀을 executor 스레드 수와 맞춘다
            max_pool_connections=settings.executor_max_workers,
        ),
    )


@lru_cache
def chat_runtime():
    """채팅 에이전트용 bedrock-runtime 클라이언트 — LangGraph ChatBedrockConverse에 주입한다.

    주입하지 않으면 langchain이 기본 설정(read 60s, 재시도 다수)으로 자체 클라이언트를
    만들어 이 모듈의 타임아웃 규약이 통째로 빠진다 — 클라가 30s에 포기한 요청을
    서버가 수 분간 계속 굴리게 된다(감사 F15). 채팅 턴은 툴콜 왕복이 있어
    루틴 경로(8s)보다 여유 있는 read 상한을 따로 쓴다.
    """
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
