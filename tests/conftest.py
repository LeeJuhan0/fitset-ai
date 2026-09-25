from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.chat.models import MessageRecord, ThreadRecord
from app.clients import bedrock, mysql
from app.core import dynamo
from app.core.config import get_settings
from app.core.orm import BackendBase
from app.exercises import models as exercises_models  # noqa: F401
from app.exercises import repository as exercises_repository
from app.main import app
from app.memory.models import UserSummaryRecord
from app.users import models as users_models  # noqa: F401
from app.workouts import models as workouts_models  # noqa: F401


USER_ID = "11111111-1111-1111-1111-111111111111"


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
def auth_client(monkeypatch):
    """테스트 키페어로 서명한 토큰을 단 클라이언트"""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setattr(get_settings(), "jwt_public_key_pem", public_pem)
    claims = {"sub": USER_ID, "exp": datetime.now(timezone.utc) + timedelta(minutes=15)}
    token = jwt.encode(claims, private_key, algorithm="RS256")
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})
