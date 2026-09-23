"""JWT 검증 테스트 — 테스트 생성 키페어로 서명해 네트워크(SSM) 없이 검증 규칙을 확인한다."""
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from botocore.exceptions import ClientError

from app.auth import repository as auth_repository
from app.auth import service as auth
from app.core.config import Settings
from app.auth.exceptions import UnauthorizedError
from app.core.exceptions import DomainError

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_PEM = _PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_PUBLIC_PEM = _OTHER_KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()


def _token(key=_PRIVATE_KEY, **overrides) -> str:
    """유효한 access 토큰(sub·exp — 백엔드 실물과 동일)을 만들고 overrides로 비틀어 깨뜨린다."""
    claims = {"sub": "user-1", "exp": int(time.time()) + 60}
    claims.update(overrides)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-kid"})


@pytest.fixture
def pem_mode(monkeypatch):
    """PEM 우회 경로(로컬 개발) — SSM 없이 주입된 공개키로 검증한다."""
    monkeypatch.setattr(auth_repository, "get_settings", lambda: Settings(jwt_public_key_pem=_PUBLIC_PEM))


@pytest.fixture
def ssm_mode(monkeypatch):
    """SSM 경로(배포) — 파라미터 조회를 스텁으로 바꾼다. lru_cache를 타지 않도록 함수째 바꾼다."""
    monkeypatch.setattr(auth_repository, "get_settings", lambda: Settings(jwt_public_key_pem=None))

    def install(read_public_key):
        monkeypatch.setattr(auth_repository, "_public_key", read_public_key)

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


def test_garbage_token_rejected(pem_mode):
    # 백엔드 refresh 토큰(랜덤 문자열)도 이 경로로 걸린다 — JWT 형식이 아니라 서명 검증 불가
    with pytest.raises(UnauthorizedError):
        auth.verify_token("h2Kk91_random_opaque_refresh_token")
    with pytest.raises(UnauthorizedError):
        auth.verify_token("not-a-token")


def test_ssm_key_verifies_when_no_pem(ssm_mode):
    ssm_mode(lambda: _PUBLIC_PEM)
    assert auth.verify_token(_token()) == "user-1"


def test_other_environment_key_is_unauthorized(ssm_mode):
    # 다른 환경의 공개키가 실린 경우 — 우리 토큰을 서명한 키가 아니므로 401.
    # kid 매칭이 사라졌으므로 서명 검증이 이 구분을 맡는다.
    ssm_mode(lambda: _OTHER_PUBLIC_PEM)
    with pytest.raises(UnauthorizedError):
        auth.verify_token(_token())


def test_missing_bearer_header_rejected():
    from app.auth import deps

    with pytest.raises(UnauthorizedError):
        deps.get_current_user_id(SimpleNamespace(headers={}))


def test_ssm_outage_is_server_error(ssm_mode):
    # SSM 장애·권한 누락은 토큰 문제가 아니다 — 401로 위장하지 않고 500으로 구분
    def raise_client_error():
        raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetParameter")

    ssm_mode(raise_client_error)
    with pytest.raises(DomainError) as caught:
        auth.verify_token(_token())
    assert not isinstance(caught.value, UnauthorizedError)


def test_rejected_token_reason_is_logged(pem_mode, caplog):
    # 401 응답은 이유를 숨기지만 로그에는 원인(만료·서명 등)이 남아야 한다 (2026-08-05)
    import logging as _logging
    with caplog.at_level(_logging.INFO, logger="fitset"):
        with pytest.raises(UnauthorizedError):
            auth.verify_token(_token(exp=int(time.time()) - 60))
    msgs = [r.getMessage() for r in caplog.records if r.name == "fitset"]
    assert any("token rejected" in m and "ExpiredSignatureError" in m for m in msgs), msgs
