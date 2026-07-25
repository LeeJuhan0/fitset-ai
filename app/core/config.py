"""환경 설정 — 환경변수로 재정의 가능한 서버 설정. core 레이어.

배포(ECS)는 태스크 정의의 environment로, 로컬은 .env 파일로 주입한다.
필드명 대문자 스네이크가 환경변수 이름 (예: aws_region → AWS_REGION).
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AWS
    aws_region: str = "ap-northeast-2"

    # DynamoDB routines (변환 완료본 + 임베딩)
    routines_table: str = "routines"
    routines_scan_limit: int | None = None   # 개발용 — 부팅 로드 건수 제한

    # 종목 마스터 (206종, repo 동봉)
    exercise_metadata_path: str = "data/exercise-metadata.ko.json"

    # 백엔드 내부 API (스프링)
    spring_internal_base_url: str = "http://localhost:8080/internal"
    workout_days: int = 28   # 최근 기록 조회 기간 — 내부 API days 파라미터

    # 인증 (JWT RS256) — SSM 공개키, 로컬 개발은 PEM 직접 주입으로 우회
    ssm_jwt_public_key_name: str = "/fitset/auth/jwt-public-key"
    jwt_public_key_pem: str | None = None

    # Bedrock — 임베딩 모델은 global.cohere.embed-v4:0 고정 (CLAUDE.md)
    embed_model_id: str = "global.cohere.embed-v4:0"
    embed_dimension: int = 1024
    llm_model_id: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    llm_max_tokens: int = 1024

    # 루틴 추천 파라미터 (CLAUDE.md 룰 필터·랭킹 확정 규칙)
    cosine_top_k: int = 30           # 코사인 상위 N
    llm_candidate_count: int = 5     # LLM 최종 선택 후보 수
    minutes_tolerance: float = 0.2   # 요청 시간 ±20%
    bodyweight_home_ratio: float = 0.7   # 맨몸 비율 ≥ 70% → 홈트 유저 판정


@lru_cache
def get_settings() -> Settings:
    """프로세스 전역 설정 싱글턴."""
    return Settings()
