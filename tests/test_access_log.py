"""액세스 로그 미들웨어 — 요청 완료 1줄과 레벨 분기 (2026-08-05 신설).

uvicorn 기본 액세스 로그를 끈 대신 미들웨어가 유일한 액세스 로그를 낸다.
성공도 남아야 하고(종전에는 실패만 남았다), 헬스체크는 남지 않아야 한다.
"""
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def _lines(caplog):
    return [r for r in caplog.records if r.name == "fitset"]


def test_success_is_logged_with_status_and_duration(client, caplog):
    # 종전에는 성공 요청이 앱 로그에 전혀 남지 않았다 — 그것이 이 변경의 이유
    with caplog.at_level(logging.INFO, logger="fitset"):
        client.get("/ai/v1/threads")   # 인증 없어 401이지만 경로·상태가 남는지 본다
    msgs = [r.getMessage() for r in _lines(caplog)]
    assert any("GET /ai/v1/threads 401" in m for m in msgs), msgs
    assert any("ms" in m for m in msgs)


def test_health_is_not_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger="fitset"):
        client.get("/health")
    assert not [r for r in _lines(caplog) if "/health" in r.getMessage()]


@pytest.mark.parametrize("path,expected", [
    ("/ai/v1/threads", logging.WARNING),      # 401 — 클라 잘못
    ("/ai/v1/nope", logging.WARNING),          # 404
])
def test_level_follows_status_class(client, caplog, path, expected):
    with caplog.at_level(logging.INFO, logger="fitset"):
        client.get(path)
    rec = [r for r in _lines(caplog) if path in r.getMessage()]
    assert rec and rec[0].levelno == expected, [(r.levelno, r.getMessage()) for r in rec]


def test_trace_id_is_attached_to_access_log(client, caplog):
    with caplog.at_level(logging.INFO, logger="fitset"):
        res = client.get("/ai/v1/threads", headers={"X-Trace-Id": "trace-abc"})
    assert res.headers["X-Trace-Id"] == "trace-abc"
    rec = [r for r in _lines(caplog) if "GET /ai/v1/threads" in r.getMessage()]
    assert rec and getattr(rec[0], "trace_id", None) == "trace-abc"


def test_uvicorn_access_logger_disabled():
    # 같은 요청이 두 줄로 남지 않도록 서버 기본 액세스 로그를 끈다
    assert logging.getLogger("uvicorn.access").disabled is True
