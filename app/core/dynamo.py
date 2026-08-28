"""DynamoDB 커넥션 — boto3 리소스 핸들과 타입 변환. core 레이어.

DynamoDB 저장 구조는 2단이다 — B+tree 단일 트리가 아니다:
  ① 파티션 키를 해시해서 어느 물리 파티션(서버)인지 O(1)로 결정 → 그래서 = 조건만 가능
     (해시는 범위 개념이 없어 "user_id > X" 같은 질문엔 주소 계산 자체가 불가)
  ② 파티션 안에서는 정렬 키 순으로 정렬 저장 → 범위 조건(>, begins_with, between) 가능
     (스토리지 노드 내부는 B-tree + WAL — USENIX ATC '22 DynamoDB 논문. 접근은 O(log n))

즉 "해시로 서버 직행(O(1)) → 서버 안 트리 탐색(O(log n)) → 정렬 순 순차 읽기".
MySQL과의 차이는 트리 유무가 아니라, 트리가 파티션 단위로 쪼개지고 위에 해시 층이 있다는 배치.

  chat_threads:  파티션 키 user_id(해시)  + 정렬 키 thread_id
  chat_messages: 파티션 키 thread_id(해시) + 정렬 키 message_id(ULID — 사전순=시간순)
  routines:      파티션 키 slug (정렬 키 없음 — 단건 GetItem 전용)

쿼리가 항상 "파티션 키 = 값 + 정렬 키 조건" 꼴인 이유가 이 구조의 문법이다.
기본 키 자체가 인덱스라 GSI/LSI 없이 전 조회가 키 경로를 탄다 (본 프로젝트 보조 인덱스 0개).

키 밖의 속성을 조건 검색하려면 GSI(새 키 조합의 복제본, 쓰기 전파 비용·최종 일관성)나
LSI(같은 파티션 키 + 다른 정렬 키, 테이블 생성 시에만)가 필요 — FilterExpression은
읽은 뒤 버리는 것이라 스캔량만큼 과금되는 가짜 최적화다.

put_item·query 등은 boto3 메서드지만 와이어는 전부 RPC-over-HTTP — REST GET/PUT/DELETE 동사 없이
항상 POST / 에 X-Amz-Target 헤더로 연산명을 싣는다. DynamoDB_20120810.PutItem 같은 헤더로 
"어떤 연산인지"를 담고 본문은 JSON 입력, 서명(SigV4) 재시도는 boto3 담당.
"""
import json
from decimal import Decimal
from functools import lru_cache

import boto3
from botocore.config import Config

from app.core.config import get_settings


def to_plain(value):
    """boto3 리소스 타입(Decimal 등) → 순수 파이썬 값 재귀 변환."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    return value


def to_storable(value):
    """저장용 변환 — DynamoDB 리소스 API는 float를 거부하므로 Decimal로 바꾼다."""
    return json.loads(json.dumps(value), parse_float=Decimal)


@lru_cache
def _resource():
    """테이블 핸들이 공유하는 boto3 리소스 — 타임아웃·재시도·커넥션 풀 설정을 한곳에 둔다."""
    settings = get_settings()
    return boto3.resource(
        "dynamodb",
        region_name=settings.aws_region,
        config=Config(
            retries={"max_attempts": settings.dynamo_max_attempts, "mode": "standard"},
            # 호출 1회의 스레드 점유 상한 — 기본 read 60s면 클라가 끊은 뒤에도 스레드가 물린다
            connect_timeout=settings.dynamo_connect_timeout,
            read_timeout=settings.dynamo_read_timeout,
            # 기본 풀은 10이라 executor 스레드보다 작다 — 스레드 수와 맞춘다
            max_pool_connections=settings.executor_max_workers,
        ),
    )



@lru_cache
def get_exercise_catalog_table():
    """`exercise_catalog` 테이블 핸들 (PK=slug — 백엔드 종목 마스터 캐시, 일 1회 배치 갱신)."""
    return _resource().Table(get_settings().exercise_catalog_table)


@lru_cache
def get_chat_threads_table():
    """`chat_threads` 테이블 핸들 (PK=user_id, SK=thread_id, TTL 속성=expires_at)."""
    return _resource().Table(get_settings().chat_threads_table)


@lru_cache
def get_chat_messages_table():
    """`chat_messages` 테이블 핸들 (PK=thread_id, SK=message_id)."""
    return _resource().Table(get_settings().chat_messages_table)


@lru_cache
def get_user_summaries_table():
    """`user_summaries` 테이블 핸들 (PK=user_id, TTL 없음 — 영속)."""
    return _resource().Table(get_settings().user_summaries_table)
