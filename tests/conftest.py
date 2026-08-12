"""공용 픽스처 — 백엔드 MySQL 직조회 테스트용 SQLite 인메모리 DB.

실제 쿼리를 실행하는 테스트(repository·통합)가 공유한다. 엔티티 모듈을 전부
임포트해야 create_all이 모든 테이블을 만든다.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.clients import mysql
from app.core.orm import BackendBase
from app.exercises import domain as exercises_domain   # noqa: F401 — 테이블 등록
from app.users import domain as users_domain           # noqa: F401
from app.workouts import domain as workouts_domain     # noqa: F401


def uid(seed: str) -> bytes:
    """결정적 BINARY(16) — 같은 시드 문자열은 같은 id를 재현한다."""
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).bytes


def seed(engine, rows) -> None:
    """엔티티 인스턴스 목록을 한 트랜잭션으로 커밋한다."""
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()


@pytest.fixture
def backend_engine(monkeypatch):
    """스키마가 깔린 SQLite 인메모리 엔진 — clients/mysql.fetch_all이 이 엔진을 쓴다.

    :memory:는 커넥션마다 별개 DB라, asyncio.to_thread로 도는 조회(서비스·툴 경로)가
    빈 DB를 보지 않도록 StaticPool로 단일 커넥션을 스레드 간 공유한다.
    """
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    BackendBase.metadata.create_all(engine)
    monkeypatch.setattr(mysql, "_engine", lambda: engine)
    return engine
