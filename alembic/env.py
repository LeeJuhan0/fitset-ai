from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.clients import postgres
from app.routines.models import Routine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Routine.metadata


def _url():
    """테스트 주입 URL 우선, 없으면 앱 PG 설정"""
    return config.get_main_option("sqlalchemy.url") or postgres.url()


def run_migrations_offline() -> None:
    """DB 접속 없이 SQL 스크립트만 출력"""
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """앱과 같은 PG 설정으로 접속해 마이그레이션 실행"""
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
