"""DynamoDB 커넥션 — PynamoDB 모델이 공유하는 접속 설정. core 레이어.

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

save·query 등은 PynamoDB 메서드지만 와이어는 전부 RPC-over-HTTP — REST GET/PUT/DELETE 동사 없이
항상 POST / 에 X-Amz-Target 헤더로 연산명을 싣는다. DynamoDB_20120810.PutItem 같은 헤더로 
"어떤 연산인지"를 담고 본문은 JSON 입력, 서명(SigV4)은 botocore, 재시도는 PynamoDB 담당.

모델(ODM)은 각 도메인 패키지 domain.py가 소유한다 — 여기는 공통 베이스와 부팅 바인딩만 둔다.
타입 변환(Decimal 등)은 PynamoDB 속성이 하므로 별도 변환 함수가 없다.
"""
from pynamodb.models import Model

from app.core.config import get_settings


class DynamoModel(Model):
    """DynamoDB 모델 공통 베이스 — 하위 모델은 Meta.table_name에 논리 이름만 적는다.

    실제 테이블명과 접속 설정은 bind_tables가 부팅 때 설정값으로 채운다 — 모델 선언이
    import 시점에 설정을 읽지 않게 하기 위해서다.
    """


def bind_tables() -> None:
    """모든 모델에 환경별 테이블명과 접속 설정을 건다 — 부팅·스크립트·테스트 시작 때 1회.

    논리 이름 X의 실제 테이블명은 설정 필드 X_table이다 (예: chat_threads → chat_threads_table).
    캐시된 커넥션도 비워 다음 호출이 새 설정으로 접속하게 한다.
    """
    settings = get_settings()
    for model in DynamoModel.__subclasses__():
        meta = model.Meta
        # 재호출에도 같은 필드를 찾도록 논리 이름을 따로 보관한다
        if not hasattr(meta, "logical_name"):
            meta.logical_name = meta.table_name
        meta.table_name = getattr(settings, f"{meta.logical_name}_table")
        meta.region = settings.aws_region
        # 호출 1회의 스레드 점유 상한 — 기본 read 60s면 클라가 끊은 뒤에도 스레드가 물린다
        meta.connect_timeout_seconds = settings.dynamo_connect_timeout
        meta.read_timeout_seconds = settings.dynamo_read_timeout
        meta.max_retry_attempts = settings.dynamo_max_attempts
        # 기본 풀은 10이라 executor 스레드보다 작다 — 스레드 수와 맞춘다
        meta.max_pool_connections = settings.executor_max_workers
        model._connection = None
