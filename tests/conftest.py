import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from moto import mock_aws
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agent import graph
from app.auth import repository as auth_repository
from app.chat import service as chat_service
from app.chat.models import MessageRecord, ThreadRecord
from app.clients import bedrock, mysql, postgres
from app.core import clock, dynamo, ulid
from app.core.config import Settings, get_settings
from app.core.orm import BackendBase
from app.exercises import models as exercises_models  # noqa: F401
from app.exercises import repository as exercises_repository
from app.main import app
from app.memory.models import UserSummaryRecord
from app.routines import service as routines_service
from app.users import models as users_models  # noqa: F401
from app.workouts import models as workouts_models  # noqa: F401


NOW = datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc)
USER_ID = "11111111-1111-1111-1111-111111111111"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
PG_TEST_DSN = os.environ.get("PG_TEST_DSN")


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    """.env 와 셸 환경변수 없는 기본값 설정"""
    current = get_settings()
    defaults = Settings.model_construct()
    for name in Settings.model_fields:
        monkeypatch.setattr(current, name, getattr(defaults, name))
    return current


@pytest.fixture(autouse=True)
def caches():
    """테스트 사이 새는 앱 싱글턴 캐시 비우기"""
    for cached in (
        chat_service.chat_limiter,
        routines_service.routine_limiter,
        auth_repository._public_key,
        bedrock._runtime,
        bedrock.chat_runtime,
        bedrock._structured_model,
        graph._model,
        postgres._engine,
    ):
        cached.cache_clear()

@pytest.fixture(autouse=True)
def mysql_engine(monkeypatch):
    """테스트마다 빈 백엔드 MySQL 대역 SQLite"""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    BackendBase.metadata.create_all(engine)
    monkeypatch.setattr(mysql, "_engine", lambda: engine)
    monkeypatch.setattr(mysql, "is_configured", lambda: True)
    exercises_repository._load_catalog.cache_clear()
    yield engine
    exercises_repository._load_catalog.cache_clear()
    engine.dispose()


@pytest.fixture(autouse=True)
def now(monkeypatch):
    """현재 시각을 NOW 로 고정"""
    monkeypatch.setattr(clock, "now_utc", lambda: NOW)
    monkeypatch.setattr(ulid, "now_utc", lambda: NOW)
    return NOW


@pytest.fixture
def seed(mysql_engine):
    """엔티티 목록을 백엔드 대역 DB 에 커밋"""
    def insert(*rows):
        """행 추가 후 한 트랜잭션 커밋"""
        with Session(mysql_engine, expire_on_commit=False) as session:
            session.add_all(rows)
            session.commit()
        return rows
    return insert

@pytest.fixture(scope="session")
def pg_schema():
    """실제 pgvector 에 Alembic 마이그레이션 적용"""
    if not PG_TEST_DSN:
        pytest.skip("PG_TEST_DSN 미설정")
    engine = create_engine(PG_TEST_DSN)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS routines, alembic_version"))
    config = Config(ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", PG_TEST_DSN)
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_engine(pg_schema, monkeypatch):
    """테스트마다 비운 routines 를 앱 PG 엔진으로"""
    with pg_schema.begin() as conn:
        conn.execute(text("TRUNCATE routines RESTART IDENTITY"))
    monkeypatch.setattr(postgres, "_engine", lambda: pg_schema)
    monkeypatch.setattr(postgres, "is_configured", lambda: True)
    return pg_schema

@pytest.fixture
def client():
    """lifespan 없이 실제 라우터를 부르는 클라이언트"""
    return TestClient(app)


@pytest.fixture(autouse=True)
def aws(monkeypatch):
    """가짜 자격증명과 moto 로 실제 AWS 차단"""
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(key, "testing")
    with mock_aws():
        yield


@pytest.fixture
def dynamo_tables(aws):
    """moto 위 채팅과 유저 요약 테이블 생성"""
    dynamo.bind_tables()
    for model in (ThreadRecord, MessageRecord, UserSummaryRecord):
        model.create_table(billing_mode="PAY_PER_REQUEST", wait=True)


@pytest.fixture
def llm(monkeypatch):
    """Bedrock 호출을 테스트가 정한 응답으로 대체"""
    fake = SimpleNamespace(
        text="테스트 응답",
        structured={},
        embedding=[1.0] + [0.0] * (get_settings().embed_dimension - 1),
    )
    monkeypatch.setattr(bedrock, "complete", lambda system, user, max_tokens=None: fake.text)
    monkeypatch.setattr(bedrock, "structured", lambda schema, system, user: fake.structured.get(schema))
    monkeypatch.setattr(bedrock, "embed_query", lambda text: fake.embedding)
    return fake


@pytest.fixture
def chat_model(monkeypatch):
    """에이전트 모델을 테스트가 넣은 답변 순서로 대체"""
    replies = []
    model = GenericFakeChatModel(messages=iter(replies), disable_streaming=True)
    monkeypatch.setattr(graph, "_model", lambda: model)
    monkeypatch.setattr(graph, "_forced_model", lambda tool: model)
    return replies

@pytest.fixture
def signing_key(monkeypatch):
    """테스트 키페어 생성 후 공개키 주입"""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setattr(get_settings(), "jwt_public_key_pem", public_pem)
    return private_key


@pytest.fixture
def auth_client(signing_key):
    """테스트 키페어로 서명한 토큰을 단 클라이언트"""
    claims = {"sub": USER_ID, "exp": datetime.now(timezone.utc) + timedelta(minutes=15)}
    token = jwt.encode(claims, signing_key, algorithm="RS256")
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})
