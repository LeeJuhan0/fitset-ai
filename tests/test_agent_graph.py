"""에이전트 그래프 배선 테스트 — 감사 F15.

ChatBedrockConverse에 core 클라이언트가 주입됐는지 검증한다 — 미주입이면 langchain이
기본 설정(read 60s·재시도 다수)으로 자체 클라이언트를 만들어 타임아웃 규약이 빠진다.
"""
from app.agent import graph
from app.core import llm


def test_chat_model_uses_tuned_bedrock_client():
    graph._model.cache_clear()
    llm.chat_runtime.cache_clear()
    model = graph._model()
    # bind_tools 결과(RunnableBinding)의 원본 모델이 core 클라이언트를 그대로 쓴다
    assert model.bound.client is llm.chat_runtime()


def test_chat_client_has_tuned_timeouts():
    llm.chat_runtime.cache_clear()
    config = llm.chat_runtime().meta.config
    assert config.read_timeout == 15.0
    assert config.connect_timeout == 2.0
    # botocore가 max_attempts(재시도 1)를 total_max_attempts(총 시도 2)로 정규화한다
    assert config.retries == {"mode": "adaptive", "total_max_attempts": 2}
