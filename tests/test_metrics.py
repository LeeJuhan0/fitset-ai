"""메트릭 노출(/metrics) 테스트 — Alloy 사이드카가 수집하는 엔드포인트의 계약을 검증한다."""
from fastapi.testclient import TestClient

from app.main import app


def test_메트릭_엔드포인트가_프로메테우스_형식으로_열린다():
    client = TestClient(app)
    client.get("/health")
    res = client.get("/metrics")
    assert res.status_code == 200
    assert "http_request" in res.text


def test_핸들러_라벨은_경로_템플릿으로_집계된다():
    # 라벨이 실제 경로(스레드 id 등)로 붙으면 시리즈가 무한 증식한다 — 템플릿 경로만 허용
    client = TestClient(app)
    client.get("/health")
    res = client.get("/metrics")
    assert 'handler="/health"' in res.text


def test_메트릭_자신은_계측에서_제외된다():
    client = TestClient(app)
    client.get("/metrics")
    res = client.get("/metrics")
    assert 'handler="/metrics"' not in res.text
