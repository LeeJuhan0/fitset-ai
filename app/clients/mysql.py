import uuid
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import URL, Select, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql import expression

from app.core.config import get_settings
from app.core.exceptions import DomainError


class seconds_between(expression.FunctionElement):
    name = "seconds_between"
    inherit_cache = True


@compiles(seconds_between)
def _seconds_between_mysql(element, compiler, **kw):
    """초 단위 시각 차 MySQL TIMESTAMPDIFF 컴파일"""
    start, end = list(element.clauses)
    return f"TIMESTAMPDIFF(SECOND, {compiler.process(start, **kw)}, {compiler.process(end, **kw)})"


@compiles(seconds_between, "sqlite")
def _seconds_between_sqlite(element, compiler, **kw):
    """초 단위 시각 차 SQLite julianday 컴파일"""
    start, end = list(element.clauses)
    return (
        f"CAST(ROUND((julianday({compiler.process(end, **kw)}) - "
        f"julianday({compiler.process(start, **kw)})) * 86400) AS INTEGER)"
    )


def is_configured() -> bool:
    """MySQL 직접 조회 호스트 설정 여부 확인"""
    return get_settings().mysql_host is not None


def uuid_bytes(user_id: str) -> bytes:
    """UUID 문자열을 BINARY 16 바인딩 값으로 변환"""
    return uuid.UUID(user_id).bytes


def uuid_str(value: bytes) -> str:
    """BINARY 16 값을 표준 UUID 문자열로 변환"""
    return str(uuid.UUID(bytes=value))


def camel_enum(value: str | None) -> str | None:
    """UPPER SNAKE enum 값을 camelCase 표기로 변환"""
    if value is None:
        return None
    head, *rest = value.lower().split("_")
    return head + "".join(word.capitalize() for word in rest)


def camel_kebab(value: str | None) -> str | None:
    """kebab case 미디어 키를 camelCase 표기로 변환"""
    if value is None:
        return None
    head, *rest = value.split("-")
    return head + "".join(word.capitalize() for word in rest)


def iso_from_db(moment: datetime) -> str:
    """naive UTC datetime 을 Z 접미 ISO 문자열로 변환"""
    return moment.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@lru_cache
def _engine():
    """읽기 전용 MySQL 엔진 커넥션 풀 전역 싱글턴"""
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
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={
            "connect_timeout": settings.mysql_connect_timeout,
            "read_timeout": settings.mysql_read_timeout,
            "init_command": "SET SESSION TRANSACTION READ ONLY",
            "charset": "utf8mb4",
        },
    )


def fetch_all(stmt: Select) -> list[dict]:
    """SELECT 1건 실행 후 dict 행 목록 반환"""
    try:
        with Session(_engine()) as session:
            return [dict(row) for row in session.execute(stmt).mappings().all()]
    except SQLAlchemyError as exc:
        raise DomainError("기록 DB 조회에 실패했습니다.") from exc
