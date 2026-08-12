"""백엔드 MySQL 읽기 전용 실행기 — 외부 경계(clients 레이어).

자유 SQL은 받지 않는다 — 각 패키지 repository와 agent/tools/history.py가 도메인
엔티티(core/orm.BackendBase 계열)로 조립한 SELECT 문만 실행하며, 값은 전부
바인딩 파라미터로만 들어간다 (문자열 조립 없음).

계정은 SELECT 전용 권한(fitset_readonly)을 전제하고, 세션도 READ ONLY로 열어
이중으로 막는다. 전 함수 동기 — async 호출부가 asyncio.to_thread로 감싼다.
DB 값 ↔ 팀 와이어 표기(UUID 문자열, camelCase enum, Z 접미 시각) 변환도 이 경계의 몫.
"""
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import URL, Select, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import DomainError


def is_configured() -> bool:
    """직접 조회 설정 여부 — 미설정 환경(로컬 기본)에서 호출부가 정중히 물러나게 한다."""
    return get_settings().mysql_host is not None


def uuid_bytes(user_id: str) -> bytes:
    """JWT sub(UUID 문자열) → DB BINARY(16) 바인딩 값."""
    return uuid.UUID(user_id).bytes


def uuid_str(value: bytes) -> str:
    """DB BINARY(16) → 표준 UUID 문자열."""
    return str(uuid.UUID(bytes=value))


def camel_enum(value: str | None) -> str | None:
    """DB의 UPPER_SNAKE enum 값을 팀 공통 camelCase 표기로 바꾼다 (구 내부 API 책임 승계)."""
    if value is None:
        return None
    head, *rest = value.lower().split("_")
    return head + "".join(word.capitalize() for word in rest)


def iso_from_db(moment: datetime) -> str:
    """DB datetime(naive, UTC 저장 — JDBC serverTimezone=UTC) → 팀 규약 Z 접미 문자열."""
    return moment.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@lru_cache
def _engine():
    """프로세스 전역 엔진 — 커넥션 풀·타임아웃 설정을 한곳에 둔다."""
    settings = get_settings()
    return create_engine(
        URL.create(
            "mysql+pymysql",
            username=settings.mysql_user,
            password=settings.mysql_password,
            host=settings.mysql_host,
            port=settings.mysql_port,
            database=settings.mysql_database,
        ),
        # RDS wait_timeout에 끊긴 커넥션 재사용을 막는다 — 체크아웃 시 ping, 1시간마다 교체
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={
            "connect_timeout": settings.mysql_connect_timeout,
            "read_timeout": settings.mysql_read_timeout,
            # 읽기 전용 계정 전제 + 세션 READ ONLY — 쓰기가 섞여도 DB가 거부한다
            "init_command": "SET SESSION TRANSACTION READ ONLY",
            "charset": "utf8mb4",
        },
    )


def fetch_all(stmt: Select) -> list[dict]:
    """SELECT 문 1건 실행 — 컬럼명 dict 행 목록을 반환한다.

    DB 장애는 DomainError(500)로 번역한다 — 전역 핸들러가 INTERNAL_ERROR로 응답하고,
    툴 경로에서는 failures.is_server_fault가 서버 장애 안내로 분기하는 기존 계약 유지.
    """
    try:
        with Session(_engine()) as session:
            return [dict(row) for row in session.execute(stmt).mappings().all()]
    except SQLAlchemyError as exc:
        raise DomainError("기록 DB 조회에 실패했습니다.") from exc
