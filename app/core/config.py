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

    # 운동 가이드 영상 (비공개 S3) — 재생 URL은 요청 시점에 presigned GET으로 서명한다
    exercise_video_bucket: str = "fitset-media"
    exercise_video_key_template: str = "exercises/{slug}/guide.mp4"   # 내부 API videoKey 미제공 시 폴백
    presign_expires_seconds: int = 3600   # 1시간 — 클라는 만료 시 §6-B로 재발급

    # 백엔드 내부 API (스프링)
    spring_internal_base_url: str = "http://localhost:8080/internal"
    workout_days: int = 28   # 최근 기록 조회 기간 — 내부 API days 파라미터

    # 인증 (JWT RS256) — SSM 공개키, 로컬 개발은 PEM 직접 주입으로 우회
    ssm_jwt_public_key_name: str = "/fitset/auth/jwt-public-key"
    jwt_public_key_pem: str | None = None

    # Bedrock — 임베딩 모델은 global.cohere.embed-v4:0 고정 (CLAUDE.md)
    embed_model_id: str = "global.cohere.embed-v4:0"
    embed_dimension: int = 1024
    # LLM: Amazon Nova 2 Lite ($0.30/$2.50 — Haiku 4.5 대비 입력 1/3.3·출력 1/2, 2026-07-29 결정).
    # 반드시 global 프로필로 호출 — 1세대 Nova(apac 프로필 강제)는 조직 SCP가 APAC 리전을
    # 차단해 사용 불가(실측). global 라우팅은 Claude와 동일하게 SCP 통과.
    # 품질 문제 시 롤백: LLM_MODEL_ID=global.anthropic.claude-haiku-4-5-20251001-v1:0
    llm_model_id: str = "global.amazon.nova-2-lite-v1:0"
    llm_max_tokens: int = 1024

    # 채팅 (DynamoDB — docs/document-structure.md)
    chat_threads_table: str = "chat_threads"
    chat_messages_table: str = "chat_messages"
    user_summaries_table: str = "user_summaries"
    max_threads_per_user: int = 5     # 초과 생성 시 마지막 활동이 가장 오래된 스레드 삭제 (LRU)
    thread_ttl_days: int = 14         # 마지막 메시지 후 미활동 만료 — expires_at(TTL 속성)
    chat_context_turns: int = 6       # LLM 컨텍스트로 싣는 최근 메시지 수 (payload 제외 조회).
                                      # 12→6 축소(2026-07-29 토큰 다이어트) — 6턴 밖 문맥은 스레드 요약(summary_text)이 담당
    chat_title_max_length: int = 30   # 제목 생성 LLM 실패 시 첫 발화 앞 N자
    summary_trigger_turns: int = 6    # 미요약 메시지가 이만큼 쌓이면 백그라운드 재요약.
                                      # 컨텍스트 턴 수(chat_context_turns)와 같게 유지 — 트리거가 더 크면
                                      # 컨텍스트에도 요약에도 없는 사각지대 메시지가 생긴다
    agent_max_tool_turns: int = 3     # 툴콜 루프 상한 — 무한 왕복 차단
    max_messages_per_thread: int = 100   # 도달 시 409 THREAD_FULL — 무제한 파티션 증가 방지 (감사 F14, 500→100 2026-07-29)

    # 레이트리밋 — 인스턴스 로컬 토큰버킷, LLM 비용 abuse 차단 (감사 F17).
    # 다중 인스턴스에선 허용량이 인스턴스 수만큼 느슨해진다 — 정밀해지면 WAF·카운터로 승격
    chat_messages_per_minute: int = 10       # 유저별 메시지 전송(LLM 포함)
    routine_generations_per_minute: int = 5  # 유저별 루틴 생성(LLM 포함)

    # 블로킹 호출용 스레드풀 크기. 기본값은 컨테이너 CPU 제한을 못 읽어 태스크당 목표 동시 요청 수로 명시한다
    executor_max_workers: int = 24

    # 블로킹 호출 타임아웃 — asyncio.to_thread는 취소가 안 되므로(클라가 끊어도 스레드는 계속 물림)
    # 스레드 점유 상한을 botocore 쪽에서 건다. max_attempts는 재시도 횟수라 총 시도는 +1,
    # 최악 = (max_attempts+1) × (connect + read). Bedrock 20초 — 클라 30초 타임아웃 안쪽
    bedrock_connect_timeout: float = 2.0
    bedrock_read_timeout: float = 8.0
    bedrock_max_attempts: int = 1
    # 채팅 에이전트 전용 read 상한 — 툴콜 왕복이 있어 루틴 경로(8s)보다 여유, 클라 30s 안쪽 (감사 F15)
    bedrock_chat_read_timeout: float = 15.0
    dynamo_connect_timeout: float = 2.0
    dynamo_read_timeout: float = 5.0
    dynamo_max_attempts: int = 2

    # 루틴 추천 파라미터 (CLAUDE.md 룰 필터·랭킹 확정 규칙)
    cosine_top_k: int = 30           # 코사인 상위 N
    llm_candidate_count: int = 5     # LLM 최종 선택 후보 수
    minutes_tolerance: float = 0.2   # 요청 시간 ±20%
    bodyweight_home_ratio: float = 0.7   # 맨몸 비율 ≥ 70% → 홈트 유저 판정

    # 가드레일 — 룰 필터로 표현할 수 없는 안전 제약(동작 패턴·가동범위·질환)이 감지되면 추천을 중단한다.
    # 끄면 제약을 로그로만 남기고 추천을 계속한다(임베딩 경로에는 여전히 반영됨)
    guardrail_block_unsafe: bool = True


@lru_cache
def get_settings() -> Settings:
    """프로세스 전역 설정 싱글턴."""
    return Settings()
