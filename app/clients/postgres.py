"""루틴 검색 저장소 Postgres(pgvector) 실행기 — 외부 경계(clients 레이어).

mysql.py 와 같은 꼴이다. routines/repository 가 엔티티(core/orm.SearchBase)로 조립한 SELECT 만
실행하고 값은 전부 바인딩 파라미터로 들어간다. 세션은 READ ONLY, statement_timeout 으로 검색
한 건이 스레드를 오래 물지 못하게 한다. 전 함수 동기 — async 호출부가 asyncio.to_thread 로 감싼다.
설계는 docs/루틴 저장소 pgvector.md.
"""
import logging
from functools import lru_cache

from sqlalchemy import URL, Select, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import DomainError

logger = logging.getLogger("fitset")


def is_configured() -> bool:
    """검색 저장소 설정 여부 — 미설정 환경(로컬 기본)에서 루틴 생성이 503 으로 물러나게 한다.

    빈 문자열도 미설정으로 본다. 매니페스트가 값 없는 env 를 내보내는 경우를 막는다.
    """
    return bool(get_settings().pg_host)


def url() -> URL:
    """설정값으로 조립한 접속 URL — str() 로 찍어도 비밀번호는 가려진다."""
    settings = get_settings()
    return URL.create(
        "postgresql+psycopg",
        username=settings.pg_user,
        password=settings.pg_password,
        host=settings.pg_host,
        port=settings.pg_port,
        database=settings.pg_database,
    )


@lru_cache
def _engine():
    """프로세스 전역 엔진 — 파드당 커넥션 최대 5, 레플리카 2 면 RDS 에 10."""
    settings = get_settings()
    return create_engine(
        url(),
        pool_size=settings.pg_pool_max,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={
            "connect_timeout": settings.pg_connect_timeout,
            # 세션 READ ONLY + 쿼리 상한. 검색 1건이 2초를 넘기면 DB 가 끊는다
            "options": (
                f"-c default_transaction_read_only=on "
                f"-c statement_timeout={int(settings.pg_statement_timeout * 1000)}"
            ),
        },
    )


def fetch_all(stmt: Select) -> list[dict]:
    """SELECT 문 1건 실행 — 컬럼명 dict 행 목록을 반환한다. DB 장애는 DomainError(500) 로 번역한다."""
    try:
        with Session(_engine()) as session:
            return [dict(row) for row in session.execute(stmt).mappings().all()]
    except SQLAlchemyError as exc:
        raise DomainError("루틴 검색 저장소 조회에 실패했습니다.") from exc


def ping() -> bool:
    """헬스체크용 SELECT 1. 실패는 False 로 돌려주고 로그만 남긴다."""
    try:
        with _engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("postgres ping failed", exc_info=True)
        return False
    return True


def close() -> None:
    """종료 시 풀 정리 — 만들어진 적이 없으면 아무것도 하지 않는다."""
    if _engine.cache_info().currsize == 0:
        return
    _engine().dispose()
    _engine.cache_clear()
