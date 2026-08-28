"""루틴 검색 저장소 Postgres(pgvector) 실행기 — 외부 경계(clients 레이어).

routines 테이블 한 곳만 읽는다. SQL 은 routines/repository 가 조립하고 값은 전부 바인딩
파라미터로만 들어간다. 세션은 READ ONLY 로 열고 statement_timeout 을 걸어 검색 한 건이
스레드를 오래 물지 못하게 한다. 전 함수 동기 — async 호출부가 asyncio.to_thread 로 감싼다.
설계는 docs/루틴 저장소 pgvector.md.
"""
import logging
from functools import lru_cache

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import get_settings
from app.core.errors import DomainError

logger = logging.getLogger("fitset")


def is_configured() -> bool:
    """검색 저장소 설정 여부 — 미설정 환경(로컬 기본)에서 루틴 생성이 503 으로 물러나게 한다."""
    return get_settings().pg_host is not None


def dsn() -> str:
    """설정값으로 조립한 접속 문자열 — 비밀번호는 로그에 남기지 않는다."""
    settings = get_settings()
    return psycopg.conninfo.make_conninfo(
        host=settings.pg_host,
        port=settings.pg_port,
        user=settings.pg_user,
        password=settings.pg_password,
        dbname=settings.pg_database,
        connect_timeout=settings.pg_connect_timeout,
        options=f"-c statement_timeout={int(settings.pg_statement_timeout * 1000)}",
    )


def _configure(conn: psycopg.Connection) -> None:
    """풀이 커넥션을 만들 때마다 — vector 어댑터 등록, 세션 READ ONLY."""
    register_vector(conn)
    conn.read_only = True


@lru_cache
def _pool() -> ConnectionPool:
    """프로세스 전역 커넥션 풀 — 파드당 최대 5, 레플리카 2 면 RDS 에 10 커넥션."""
    settings = get_settings()
    return ConnectionPool(
        dsn(),
        min_size=settings.pg_pool_min,
        max_size=settings.pg_pool_max,
        configure=_configure,
        kwargs={"row_factory": dict_row},
        open=True,
    )


def fetch_all(sql: str, params: dict) -> list[dict]:
    """SELECT 1건 실행 — 컬럼명 dict 행 목록을 반환한다.

    DB 장애는 DomainError(500)로 번역한다 — 전역 핸들러가 INTERNAL_ERROR 로 응답한다.
    """
    try:
        with _pool().connection() as conn:
            return conn.execute(sql, params).fetchall()
    except psycopg.Error as exc:
        raise DomainError("루틴 검색 저장소 조회에 실패했습니다.") from exc


def ping() -> bool:
    """헬스체크용 — 풀에서 커넥션을 하나 빌려 SELECT 1. 실패는 False 로 돌려주고 로그만 남긴다."""
    try:
        with _pool().connection() as conn:
            conn.execute("SELECT 1").fetchone()
    except psycopg.Error:
        logger.warning("postgres ping failed", exc_info=True)
        return False
    return True


def close() -> None:
    """종료 시 풀 정리 — 만들어진 적이 없으면 아무것도 하지 않는다."""
    if _pool.cache_info().currsize == 0:
        return
    _pool().close()
    _pool.cache_clear()
