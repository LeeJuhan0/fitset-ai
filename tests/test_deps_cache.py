"""의존성 캐시 검증 — 라우터 공통 가드 + 파라미터 Depends 이중 선언 시 JWT 검증 횟수.

get_current_user_id는 라우터 dependencies와 핸들러 파라미터 양쪽에 선언돼 있다(deps.py).
FastAPI가 요청 스코프에서 같은 의존성을 캐시해 검증이 1회만 도는지 실측한다.
호출 횟수는 auth.verify_token을 카운터로 대체해 센다.

pytest -s tests/test_deps_cache.py 로 실행하면 횟수가 print로 보인다.
"""
from fastapi import Depends
from fastapi.testclient import TestClient

from app import deps
from app.core import auth
from app.main import app

HEADERS = {"Authorization": "Bearer dummy"}


class _Counter:
    """verify_token 대역 — 호출 횟수를 세고 고정 userId를 돌려준다."""

    def __init__(self):
        self.calls = 0

    def __call__(self, token: str) -> str:
        self.calls += 1
        return "11111111-1111-1111-1111-111111111111"


def test_이중_선언이어도_요청당_검증은_1회(monkeypatch):
    counter = _Counter()
    monkeypatch.setattr(auth, "verify_token", counter)

    client = TestClient(app)
    res = client.get("/ai/v1/threads", headers=HEADERS)

    print(f"\n[캐시 켜짐] GET /ai/v1/threads 1요청 → verify_token {counter.calls}회")
    assert res.status_code == 200
    assert counter.calls == 1   # 가드 + 파라미터 = 2자리지만 캐시로 1회


def test_캐시는_요청_스코프라_요청마다_재검증(monkeypatch):
    counter = _Counter()
    monkeypatch.setattr(auth, "verify_token", counter)

    client = TestClient(app)
    for _ in range(3):
        client.get("/ai/v1/threads", headers=HEADERS)

    print(f"\n[요청 스코프] 같은 라우트 3요청 → verify_token {counter.calls}회")
    assert counter.calls == 3   # 요청을 넘어 캐시되면 보안 문제 — 반드시 요청당 1회


def test_use_cache_False면_자리마다_재실행(monkeypatch):
    """비교군 — 캐시를 끄면 이중 선언이 그대로 2회가 됨을 보여 캐시가 원인임을 증명한다."""
    counter = _Counter()
    monkeypatch.setattr(auth, "verify_token", counter)

    # 비교군 라우트는 앱에 1회만 추가한다 — 테스트 재실행 시 중복 등록 방지
    if not any(getattr(route, "path", None) == "/prove/nocache" for route in app.routes):
        @app.get("/prove/nocache", dependencies=[Depends(deps.get_current_user_id, use_cache=False)])
        def nocache(user_id: str = Depends(deps.get_current_user_id, use_cache=False)):
            return {"user": user_id}

    client = TestClient(app)
    res = client.get("/prove/nocache", headers=HEADERS)

    print(f"\n[캐시 꺼짐] GET /prove/nocache 1요청 → verify_token {counter.calls}회")
    assert res.status_code == 200
    assert counter.calls == 2   # 같은 의존성이라도 use_cache=False면 자리마다 실행
