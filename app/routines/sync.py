import logging

import psycopg
from sqlalchemy import URL

from app.clients import postgres
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.routines.models import Routine

logger = logging.getLogger("fitset")

COLUMNS = ", ".join(column.name for column in Routine.__table__.columns)


def _conninfo(url: URL) -> str:
    """SQLAlchemy URL 을 psycopg 접속 문자열로"""
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def source_url() -> URL:
    """복사 원본 dev Postgres 접속 URL"""
    settings = get_settings()
    if not settings.sync_source_pg_host:
        raise RuntimeError("SYNC_SOURCE_PG_HOST 가 없다")
    return postgres.url().set(host=settings.sync_source_pg_host, password=settings.sync_source_pg_password)


def copy_routines(source: str, target: str) -> int:
    """원본 routines 전체로 대상 routines 교체"""
    with psycopg.connect(target) as dst:
        dst.execute("CREATE TEMP TABLE routines_sync (LIKE routines INCLUDING DEFAULTS) ON COMMIT DROP")
        with (
            psycopg.connect(source) as src,
            src.cursor().copy(f"COPY (SELECT {COLUMNS} FROM routines) TO STDOUT") as out,
            dst.cursor().copy(f"COPY routines_sync ({COLUMNS}) FROM STDIN") as into,
        ):
            for chunk in out:
                into.write(chunk)
        count = dst.execute("SELECT count(*) FROM routines_sync").fetchone()[0]
        if count == 0:
            raise RuntimeError("원본 routines 가 비어 있어 복사를 멈춘다")
        dst.execute("TRUNCATE routines")
        dst.execute(f"INSERT INTO routines ({COLUMNS}) OVERRIDING SYSTEM VALUE SELECT {COLUMNS} FROM routines_sync")
        dst.execute("SELECT setval(pg_get_serial_sequence('routines', 'id'), max(id)) FROM routines")
    return count


def main() -> None:
    """dev routines 를 prod 로 복사"""
    configure_logging()
    count = copy_routines(_conninfo(source_url()), _conninfo(postgres.url()))
    logger.info("routines synced rows=%d", count)


if __name__ == "__main__":
    main()
