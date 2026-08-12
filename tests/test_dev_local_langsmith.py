"""dev_local LangSmith 연동 테스트 — 키 유무에 따라 트레이싱 env가 정확히 세팅되는지.

트레이스에 대화 내용이 올라가는 기능이라, 키가 없을 때 어떤 env도 건드리지 않는 것
(프로덕션·CI에서 우발적으로 켜지지 않는 것)이 핵심 검증 대상이다.
"""
import importlib.util
import os
from pathlib import Path

import pytest

_ENV_KEYS = (
    "LANGSMITH_API_KEY", "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2",
    "LANGSMITH_PROJECT", "LANGCHAIN_PROJECT",
)


@pytest.fixture
def dev_local():
    spec = importlib.util.spec_from_file_location(
        "dev_local", Path(__file__).parent.parent / "scripts" / "dev_local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_no_api_key_leaves_env_untouched(dev_local, clean_env):
    assert dev_local.enable_langsmith() is False
    assert not any(os.environ.get(key) for key in _ENV_KEYS)


def test_api_key_enables_tracing_with_defaults(dev_local, clean_env):
    clean_env.setenv("LANGSMITH_API_KEY", "lsv2-test")
    assert dev_local.enable_langsmith() is True
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "fitset-dev-local"
    assert os.environ["LANGCHAIN_PROJECT"] == "fitset-dev-local"


def test_explicit_project_name_is_kept(dev_local, clean_env):
    # 사용자가 프로젝트명을 직접 줬으면 기본값으로 덮지 않는다 (setdefault 규약)
    clean_env.setenv("LANGSMITH_API_KEY", "lsv2-test")
    clean_env.setenv("LANGSMITH_PROJECT", "my-experiment")
    assert dev_local.enable_langsmith() is True
    assert os.environ["LANGSMITH_PROJECT"] == "my-experiment"
    assert os.environ["LANGCHAIN_PROJECT"] == "my-experiment"
