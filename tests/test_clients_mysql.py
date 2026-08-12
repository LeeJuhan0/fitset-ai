"""clients/mysql 경계 유닛 테스트 — 표기 변환·설정 판정·DB 장애 번역.

이 모듈은 DB 값과 팀 와이어 표기 사이의 유일한 변환 지점이라, 변환 규칙이
조용히 어긋나면 모든 직조회 응답이 한꺼번에 틀어진다.
"""
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select

from app.clients import mysql
from app.core.errors import DomainError
from app.exercises.domain import Exercise

USER_ID = "11111111-1111-1111-1111-111111111111"


def test_camel_enum_converts_upper_snake():
    # DB 네이티브 ENUM(대문자) → 팀 카탈로그 key(camelCase) — 구 내부 API 책임 승계
    assert mysql.camel_enum("WEIGHT_LOSS") == "weightLoss"
    assert mysql.camel_enum("HYPERTROPHY") == "hypertrophy"
    assert mysql.camel_enum("WEIGHT_AND_REPS") == "weightAndReps"
    assert mysql.camel_enum("BEGINNER") == "beginner"


def test_camel_enum_passes_none_through():
    assert mysql.camel_enum(None) is None


def test_uuid_bytes_and_str_roundtrip():
    # JWT sub(문자열) → BINARY(16) → 응답 문자열이 원본과 같아야 한다
    raw = mysql.uuid_bytes(USER_ID)
    assert len(raw) == 16
    assert mysql.uuid_str(raw) == USER_ID
    assert uuid.UUID(USER_ID).bytes == raw


def test_iso_from_db_appends_z_without_shifting():
    # DB naive datetime은 UTC 저장(JDBC serverTimezone=UTC) — 시간대 이동 없이 Z만 붙인다
    assert mysql.iso_from_db(datetime(2026, 8, 12, 9, 30, 0)) == "2026-08-12T09:30:00Z"


def test_is_configured_follows_mysql_host(monkeypatch):
    monkeypatch.setattr(mysql, "get_settings", lambda: SimpleNamespace(mysql_host=None))
    assert mysql.is_configured() is False
    monkeypatch.setattr(mysql, "get_settings", lambda: SimpleNamespace(mysql_host="db.internal"))
    assert mysql.is_configured() is True


def test_fetch_all_translates_db_failure_to_domain_error(monkeypatch):
    # 테이블 없는 엔진 → SQLAlchemyError — 전역 핸들러·failures가 아는 DomainError(500)로 번역
    empty_engine = create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(mysql, "_engine", lambda: empty_engine)
    with pytest.raises(DomainError) as excinfo:
        mysql.fetch_all(select(Exercise.slug))
    assert excinfo.value.status_code == 500
    assert "조회에 실패" in excinfo.value.message


def test_fetch_all_returns_column_named_dicts(backend_engine):
    from tests.conftest import seed, uid

    seed(backend_engine, [Exercise(
        id=uid("e1"), slug="push-up", name="푸시업", equipment_id=uid("eq"),
        difficulty="BEGINNER", exercise_type="REPS_ONLY", instructions=[],
    )])
    rows = mysql.fetch_all(select(Exercise.slug, Exercise.name))
    assert rows == [{"slug": "push-up", "name": "푸시업"}]
