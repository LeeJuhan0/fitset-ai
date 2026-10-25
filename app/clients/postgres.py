import logging
from functools import lru_cache

from psycopg.errors import QueryCanceled
from sqlalchemy import URL, Engine, Select, create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import DomainError

logger = logging.getLogger("fitset")


def is_configured() -> bool:
    """루틴 검색 Postgres 호스트 설정 여부 확인"""
    return bool(get_settings().pg_host)


def url() -> URL:
    """설정값 기반 Postgres 접속 URL 조립"""
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
def _engine() -> Engine:
    """읽기 전용 Postgres 엔진 커넥션 풀 전역 싱글턴"""
    settings = get_settings()
    return create_engine(
        url(),
        pool_size=settings.pg_pool_max,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={
            "connect_timeout": settings.pg_connect_timeout,
            "options": (
                f"-c default_transaction_read_only=on "
                f"-c statement_timeout={int(settings.pg_statement_timeout * 1000)}"
            ),
        },
    )


FETCH_FAILED = "루틴 검색 저장소 조회에 실패했습니다."


def _rows(stmt: Select) -> list[dict]:
    """SELECT 1건 실행 후 dict 행 목록 반환"""
    with Session(_engine()) as session:
        return [dict(row) for row in session.execute(stmt).mappings().all()]


def fetch_all(stmt: Select) -> list[dict]:
    """SELECT 실행, 콜드 캐시 타임아웃 취소는 1회 재시도"""
    try:
        return _rows(stmt)
    except OperationalError as exc:
        if not isinstance(exc.orig, QueryCanceled):
            raise DomainError(FETCH_FAILED) from exc
        logger.warning("postgres statement timeout, retrying once on warmed cache")
    except SQLAlchemyError as exc:
        raise DomainError(FETCH_FAILED) from exc
    try:
        return _rows(stmt)
    except SQLAlchemyError as exc:
        raise DomainError(FETCH_FAILED) from exc


def ping() -> bool:
    """헬스체크용 SELECT 1 실행 성공 여부 반환"""
    try:
        with _engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("postgres ping failed", exc_info=True)
        return False
    return True


def close() -> None:
    """서버 종료 시 엔진 커넥션 풀 정리"""
    if _engine.cache_info().currsize == 0:
        return
    _engine().dispose()
    _engine.cache_clear()
