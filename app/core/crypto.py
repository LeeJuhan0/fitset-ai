import base64
import json
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pynamodb.attributes import MapAttribute, UnicodeAttribute
from pynamodb.constants import MAP, STRING

from app.core.config import get_settings
from app.core.exceptions import DomainError

PREFIX = "enc:"
NONCE_BYTES = 12


@lru_cache
def _aead(version: str) -> AESGCM:
    """버전별 설정 키로 AES-GCM 암호기 생성"""
    key = get_settings().chat_encryption_keys.get(version)
    if not key:
        raise DomainError("대화 암호화 키가 설정되지 않았습니다.")
    return AESGCM(base64.b64decode(key))


def encrypt(plain: str) -> str:
    """현재 버전 키로 버전 접두사 암호문 생성"""
    version = get_settings().chat_encryption_key_version
    nonce = os.urandom(NONCE_BYTES)
    sealed = _aead(version).encrypt(nonce, plain.encode(), None)
    return f"{PREFIX}{version}:" + base64.b64encode(nonce + sealed).decode()


def decrypt(stored: str) -> str:
    """접두사 버전 키로 복호화, 접두사 없으면 평문 통과"""
    if not stored.startswith(PREFIX):
        return stored
    version, body = stored[len(PREFIX):].split(":", 1)
    raw = base64.b64decode(body)
    return _aead(version).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], None).decode()


class EncryptedUnicodeAttribute(UnicodeAttribute):
    """저장 시 암호화, 조회 시 복호화 문자열"""

    def serialize(self, value):
        """평문 문자열 암호화"""
        return encrypt(value)

    def deserialize(self, value):
        """암호문 복호화, 기존 평문 호환"""
        return decrypt(value)


class EncryptedJSONAttribute(UnicodeAttribute):
    """dict JSON 직렬화 후 암호화, 기존 맵 호환"""

    def get_value(self, value):
        """기존 M 타입 항목 원형 전달"""
        if MAP in value:
            return value
        return value[STRING]

    def serialize(self, value):
        """dict JSON 직렬화 후 암호화"""
        return encrypt(json.dumps(value, ensure_ascii=False))

    def deserialize(self, value):
        """암호문 복호화 JSON 로드, 기존 맵 dict 변환"""
        if isinstance(value, dict):
            return MapAttribute().deserialize(value[MAP])
        return json.loads(decrypt(value))
