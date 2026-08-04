"""JWT 검증 테스트 — 테스트 생성 키페어로 서명해 네트워크(JWKS) 없이 검증 규칙을 확인한다."""
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from app.core import auth
from app.core.config import Settings
from app.core.errors import DomainError, UnauthorizedError

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_PEM = _PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(key=_PRIVATE_KEY, **overrides) -> str:
    """유효한 access 토큰을 기본으로 만들고 overrides로 클레임을 비틀어 깨뜨린다."""
    claims = {"sub": "user-1", "type": "access", "exp": int(time.time()) + 60}
    claims.update(overrides)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-kid"})


@pytest.fixture
def pem_mode(monkeypatch):
    """PEM 우회 경로(로컬 개발) — JWKS 없이 주입된 공개키로 검증한다."""
    monkeypatch.setattr(auth, "get_settings", lambda: Settings(jwt_public_key_pem=_PUBLIC_PEM))


@pytest.fixture
def jwks_mode(monkeypatch):
    """JWKS 경로(prod) — 클라이언트를 스텁으로 바꿔 kid 조회 결과를 주입한다."""
    monkeypatch.setattr(auth, "get_settings", lambda: Settings(jwt_public_key_pem=None))

    def install(get_signing_key_from_jwt):
        stub = SimpleNamespace(get_signing_key_from_jwt=get_signing_key_from_jwt)
        monkeypatch.setattr(auth, "_jwks_client", lambda: stub)

    return install


def test_valid_token_returns_sub(pem_mode):
    assert auth.verify_token(_token()) == "user-1"


def test_expired_token_rejected(pem_mode):
    with pytest.raises(UnauthorizedError):
        auth.verify_token(_token(exp=int(time.time()) - 60))


def test_foreign_signature_rejected(pem_mode):
    # 다른 키페어로 서명된 토큰 — 백엔드가 발급하지 않은 토큰이다
    with pytest.raises(UnauthorizedError):
        auth.verify_token(_token(key=_OTHER_KEY))


def test_missing_sub_rejected(pem_mode):
    with pytest.raises(UnauthorizedError):
        auth.verify_token(_token(sub=None))


def test_non_access_type_rejected(pem_mode):
    # refresh 토큰이 access처럼 통하면 안 된다 — type 검사 유지 중 (백엔드 확인 전)
    with pytest.raises(UnauthorizedError):
        auth.verify_token(_token(type="refresh"))
    with pytest.raises(UnauthorizedError):
        auth.verify_token(_token(type=None))


def test_garbage_token_rejected(pem_mode):
    with pytest.raises(UnauthorizedError):
        auth.verify_token("not-a-token")


def test_jwks_key_verifies_when_no_pem(jwks_mode):
    jwks_mode(lambda token: SimpleNamespace(key=_PRIVATE_KEY.public_key()))
    assert auth.verify_token(_token()) == "user-1"


def test_unknown_kid_is_unauthorized(jwks_mode):
    # JWKS에 없는 kid — 우리 키로 서명되지 않은 토큰이므로 401
    def raise_unknown(token):
        raise PyJWKClientError('Unable to find a signing key that matches: "test-kid"')

    jwks_mode(raise_unknown)
    with pytest.raises(UnauthorizedError):
        auth.verify_token(_token())


def test_missing_bearer_header_rejected():
    from app import deps

    with pytest.raises(UnauthorizedError):
        deps.get_current_user_id(SimpleNamespace(headers={}))


def test_jwks_outage_is_server_error(jwks_mode):
    # 엔드포인트 장애는 토큰 문제가 아니다 — 401로 위장하지 않고 500으로 구분
    def raise_connection(token):
        raise PyJWKClientConnectionError("connection refused")

    jwks_mode(raise_connection)
    with pytest.raises(DomainError) as caught:
        auth.verify_token(_token())
    assert not isinstance(caught.value, UnauthorizedError)
