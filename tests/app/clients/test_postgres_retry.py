import pytest
from psycopg.errors import QueryCanceled, UndefinedTable
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.clients import postgres
from app.core.exceptions import DomainError


def _failing(errors: list[Exception], calls: list[int]):
    """앞에서부터 오류를 던지고 다 쓰면 빈 결과 반환"""
    def rows(stmt):
        """호출 기록 후 남은 오류 던지기"""
        calls.append(1)
        if errors:
            raise errors.pop(0)
        return [{"ok": 1}]
    return rows


def _timeout() -> OperationalError:
    """statement_timeout 취소 오류"""
    return OperationalError("stmt", {}, QueryCanceled("canceling statement due to statement timeout"))


def test_timeout_retries_once(monkeypatch):
    """타임아웃 1회면 두 번째 시도 결과 반환"""
    calls = []
    monkeypatch.setattr(postgres, "_rows", _failing([_timeout()], calls))
    assert postgres.fetch_all(select(1)) == [{"ok": 1}]
    assert len(calls) == 2


def test_timeout_twice_fails(monkeypatch):
    """연속 타임아웃은 두 번만 시도 후 DomainError"""
    calls = []
    monkeypatch.setattr(postgres, "_rows", _failing([_timeout(), _timeout()], calls))
    with pytest.raises(DomainError):
        postgres.fetch_all(select(1))
    assert len(calls) == 2


def test_other_error_not_retried(monkeypatch):
    """타임아웃 아닌 오류는 즉시 DomainError"""
    calls = []
    error = ProgrammingError("stmt", {}, UndefinedTable("no table"))
    monkeypatch.setattr(postgres, "_rows", _failing([error], calls))
    with pytest.raises(DomainError):
        postgres.fetch_all(select(1))
    assert len(calls) == 1
