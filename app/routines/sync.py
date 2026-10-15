import logging

import psycopg
from sqlalchemy import URL, Table

from app.clients import postgres
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.evidence.models import EvidenceChunk, EvidenceSituation
from app.routines.models import Routine

logger = logging.getLogger("fitset")

TABLES: list[Table] = [Routine.__table__, EvidenceChunk.__table__, EvidenceSituation.__table__]


def _conninfo(url: URL) -> str:
    """SQLAlchemy URL 을 psycopg 접속 문자열로"""
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def source_url() -> URL:
    """복사 원본 dev Postgres 접속 URL"""
    settings = get_settings()
    if not settings.sync_source_pg_host:
        raise RuntimeError("SYNC_SOURCE_PG_HOST 가 없다")
    return postgres.url().set(host=settings.sync_source_pg_host, password=settings.sync_source_pg_password)


def _columns(table: Table) -> str:
    """테이블 컬럼 이름 목록 문자열"""
    return ", ".join(column.name for column in table.columns)


def copy_tables(source: str, target: str, tables: list[Table] = TABLES) -> dict[str, int]:
    """원본 테이블 전체로 대상 테이블 한 번에 교체"""
    counts = {}
    with psycopg.connect(target) as dst, psycopg.connect(source) as src:
        for table in tables:
            name, columns = table.name, _columns(table)
            dst.execute(f"CREATE TEMP TABLE {name}_sync (LIKE {name} INCLUDING DEFAULTS) ON COMMIT DROP")
            with (
                src.cursor().copy(f"COPY (SELECT {columns} FROM {name}) TO STDOUT") as out,
                dst.cursor().copy(f"COPY {name}_sync ({columns}) FROM STDIN") as into,
            ):
                for chunk in out:
                    into.write(chunk)
            counts[name] = dst.execute(f"SELECT count(*) FROM {name}_sync").fetchone()[0]
            if counts[name] == 0:
                raise RuntimeError(f"원본 {name} 가 비어 있어 복사를 멈춘다")
        dst.execute(f"TRUNCATE {', '.join(table.name for table in tables)}")
        for table in tables:
            name, columns = table.name, _columns(table)
            dst.execute(f"INSERT INTO {name} ({columns}) OVERRIDING SYSTEM VALUE SELECT {columns} FROM {name}_sync")
            if table.c.id.identity is not None:
                dst.execute(f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), max(id)) FROM {name}")
    return counts


def main() -> None:
    """dev routines, 논문 근거 테이블을 prod 로 복사"""
    configure_logging()
    counts = copy_tables(_conninfo(source_url()), _conninfo(postgres.url()))
    logger.info("tables synced %s", counts)


if __name__ == "__main__":
    main()
