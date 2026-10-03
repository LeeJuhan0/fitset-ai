from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_region: str = "ap-northeast-2"

    media_cdn_base_url: str = "https://d31w3ih1t93w7x.cloudfront.net"

    workout_days: int = 28

    mysql_host: str | None = None
    mysql_port: int = 3306
    mysql_user: str = "fitset_readonly"
    mysql_password: str = ""
    mysql_database: str = "fitset"
    mysql_connect_timeout: int = 2
    mysql_read_timeout: int = 5

    pg_host: str | None = None
    pg_port: int = 5432
    pg_user: str = "fitset"
    pg_password: str = ""
    pg_database: str = "fitset"
    pg_connect_timeout: int = 2
    pg_statement_timeout: float = 5.0
    pg_pool_max: int = 5

    sync_source_pg_host: str | None = None
    sync_source_pg_password: str = ""

    jwt_public_key_param: str = "/fitset/dev/jwt/public-key"
    jwt_public_key_pem: str | None = None

    embed_model_id: str = "amazon.titan-embed-text-v2:0"
    embed_dimension: int = 1024
    llm_model_id: str = "anthropic.claude-3-haiku-20240307-v1:0"
    llm_max_tokens: int = 1024
    chat_max_tokens: int = 450
    chat_answer_max_chars: int = 500
    chat_message_max_chars: int = 500

    chat_threads_table: str = "chat_threads"
    chat_messages_table: str = "chat_messages"
    user_summaries_table: str = "user_summaries"
    chat_encryption_keys: dict[str, str] = {}
    chat_encryption_key_version: str = "v1"
    max_threads_per_user: int = 10
    thread_ttl_days: int = 14
    chat_context_turns: int = 6
    chat_title_max_length: int = 30
    summary_trigger_turns: int = 6
    agent_max_tool_turns: int = 3
    max_messages_per_thread: int = 1000

    chat_messages_per_minute: int = 10
    routine_generations_per_minute: int = 5

    executor_max_workers: int = 24

    slack_error_webhook_url: str | None = None

    sentry_dsn: str | None = None
    sentry_environment: str = "dev"
    sentry_sample_rate: float = 0.1

    bedrock_connect_timeout: float = 2.0
    bedrock_read_timeout: float = 8.0
    bedrock_max_attempts: int = 1
    bedrock_chat_read_timeout: float = 15.0
    dynamo_connect_timeout: float = 2.0
    dynamo_read_timeout: float = 5.0
    dynamo_max_attempts: int = 2

    cosine_top_k: int = 30
    llm_candidate_count: int = 5
    minutes_tolerance: float = 0.2
    duration_set_seconds: int = 30
    warmup_duration_factor: float = 0.5
    bodyweight_home_ratio: float = 0.7

    guardrail_block_unsafe: bool = True
    guardrail_id: str = ""
    guardrail_version: str = "3"
    scope_model_id: str = ""


@lru_cache
def get_settings() -> Settings:
    """환경변수 기반 설정 객체 전역 싱글턴"""
    return Settings()
