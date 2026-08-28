"""clients/postgres 경계 유닛 테스트 — 설정 판정·URL 조립·DB 장애 번역·헬스 ping."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.clients import postgres
from app.core.errors import DomainError


def _settings(**overrides):
    base = dict(pg_host=None, pg_port=5432, pg_user="admin", pg_password="pw", pg_database="fitset",
                pg_connect_timeout=2, pg_statement_timeout=2.0, pg_pool_max=5)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_is_configured_requires_host(monkeypatch):
    monkeypatch.setattr(postgres, "get_settings", lambda: _settings())
    assert postgres.is_configured() is False
    monkeypatch.setattr(postgres, "get_settings", lambda: _settings(pg_host=""))
    assert postgres.is_configured() is False
    monkeypatch.setattr(postgres, "get_settings", lambda: _settings(pg_host="db"))
    assert postgres.is_configured() is True


def test_url_uses_psycopg_driver_and_hides_password(monkeypatch):
    monkeypatch.setattr(postgres, "get_settings", lambda: _settings(pg_host="db"))
    value = postgres.url()
    assert value.drivername == "postgresql+psycopg"
    assert value.host == "db" and value.database == "fitset"
    assert "pw" not in str(value)


class _FailingEngine:
    def connect(self):
        raise OperationalError("SELECT 1", {}, Exception("down"))


def test_fetch_all_translates_db_error(monkeypatch):
    monkeypatch.setattr(postgres, "_engine", lambda: _FailingEngine())
    with pytest.raises(DomainError):
        postgres.fetch_all(select(1))


def test_ping_returns_false_on_error(monkeypatch):
    monkeypatch.setattr(postgres, "_engine", lambda: _FailingEngine())
    assert postgres.ping() is False


def test_close_is_noop_without_engine():
    postgres._engine.cache_clear()
    postgres.close()
