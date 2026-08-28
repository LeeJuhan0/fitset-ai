"""clients/postgres 경계 유닛 테스트 — 설정 판정·DSN 조립·DB 장애 번역·헬스 ping."""
from types import SimpleNamespace

import psycopg
import pytest

from app.clients import postgres
from app.core.errors import DomainError


def _settings(**overrides):
    base = dict(pg_host=None, pg_port=5432, pg_user="admin", pg_password="pw", pg_database="fitset",
                pg_connect_timeout=2, pg_statement_timeout=2.0, pg_pool_min=1, pg_pool_max=5)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_is_configured_requires_host(monkeypatch):
    monkeypatch.setattr(postgres, "get_settings", lambda: _settings())
    assert postgres.is_configured() is False
    monkeypatch.setattr(postgres, "get_settings", lambda: _settings(pg_host="db"))
    assert postgres.is_configured() is True


def test_dsn_includes_statement_timeout_ms(monkeypatch):
    monkeypatch.setattr(postgres, "get_settings", lambda: _settings(pg_host="db", pg_statement_timeout=1.5))
    value = postgres.dsn()
    assert "host=db" in value and "dbname=fitset" in value
    assert "statement_timeout=1500" in value


class _FailingPool:
    def connection(self):
        raise psycopg.OperationalError("down")


def test_fetch_all_translates_db_error(monkeypatch):
    monkeypatch.setattr(postgres, "_pool", lambda: _FailingPool())
    with pytest.raises(DomainError):
        postgres.fetch_all("SELECT 1", {})


def test_ping_returns_false_on_error(monkeypatch):
    monkeypatch.setattr(postgres, "_pool", lambda: _FailingPool())
    assert postgres.ping() is False


def test_close_is_noop_without_pool():
    postgres._pool.cache_clear()
    postgres.close()
