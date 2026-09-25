from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import status
from fastapi.testclient import TestClient

from tests.conftest import USER_ID


@pytest.mark.usefixtures("dynamo_tables")
async def test_유효한_토큰을_가진_사용자는_자신의_대화_목록을_조회할_수_있다(
    auth_client: TestClient,
):
    response = auth_client.get("/ai/v1/threads")

    assert response.status_code == status.HTTP_200_OK


async def test_만료된_토큰을_가진_사용자는_인증에_실패한다(
    client: TestClient,
    signing_key: rsa.RSAPrivateKey,
):
    claims = {"sub": USER_ID, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)}
    headers = {"Authorization": f"Bearer {jwt.encode(claims, signing_key, algorithm='RS256')}"}

    response = client.get("/ai/v1/threads", headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.usefixtures("signing_key")
async def test_다른_키로_서명된_토큰을_가진_사용자는_인증에_실패한다(
    client: TestClient,
):
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {"sub": USER_ID, "exp": datetime.now(timezone.utc) + timedelta(minutes=15)}
    headers = {"Authorization": f"Bearer {jwt.encode(claims, forged_key, algorithm='RS256')}"}

    response = client.get("/ai/v1/threads", headers=headers)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_토큰_없이_요청한_사용자는_인증에_실패한다(
    client: TestClient,
):
    response = client.get("/ai/v1/threads")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
