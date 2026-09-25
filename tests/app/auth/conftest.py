import boto3
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture
def ssm_signing_key(settings):
    """moto SSM 에 공개키 등록 후 개인키 반환"""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    boto3.client("ssm", region_name=settings.aws_region).put_parameter(
        Name=settings.jwt_public_key_param, Value=public_pem, Type="String",
    )
    return private_key
