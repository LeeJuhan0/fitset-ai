import json
import logging
from functools import lru_cache

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError
from botocore.config import Config
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger("fitset")

LLM_ERRORS = (BotoCoreError, ClientError, KeyError, IndexError, ValueError)

INFERENCE_PROFILE_PREFIXES = ("us", "eu", "apac", "global")


@lru_cache
def _runtime() -> BaseClient:
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
def chat_runtime() -> BaseClient:
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


def model_provider(model_id: str) -> str:
    """모델 아이디에서 추론 프로필 접두사 제외 프로바이더 추출"""
    parts = model_id.split(".")
    if parts and parts[0] in INFERENCE_PROFILE_PREFIXES:
        parts = parts[1:]
    return parts[0] if parts else "amazon"


@lru_cache
def _structured_model() -> ChatBedrockConverse:
    """루틴 경로 구조화 출력용 채팅 모델 전역 싱글턴"""
    settings = get_settings()
    return ChatBedrockConverse(
        model=settings.llm_model_id,
        provider=model_provider(settings.llm_model_id),
        region_name=settings.aws_region,
        client=_runtime(),
        max_tokens=settings.llm_max_tokens,
        temperature=0.3,
    )


def structured(schema: type[BaseModel], system: str, user: str) -> BaseModel | None:
    """스키마 강제 도구 호출로 검증된 객체 반환"""
    model = _structured_model().with_structured_output(schema)
    return model.invoke([SystemMessage(content=system), HumanMessage(content=user)])


def try_structured(purpose: str, schema: type[BaseModel], system: str, user: str) -> BaseModel | None:
    """구조화 출력 실패 시 로그 기록 후 None 반환"""
    try:
        result = structured(schema, system, user)
    except LLM_ERRORS:
        logger.warning("%s LLM failed, falling back", purpose, exc_info=True)
        return None
    if result is None:
        logger.warning("%s LLM returned no structured output, falling back", purpose)
    return result


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
    except LLM_ERRORS:
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
    except LLM_ERRORS:
        logger.exception("query embedding failed")
        return None
