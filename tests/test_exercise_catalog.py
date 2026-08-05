"""종목 카탈로그 저장소·동기화 배치 테스트 — 백엔드 마스터 캐시(2026-08-05 신설).

카탈로그는 있으면 좋고 없어도 서버가 도는 보조 데이터다. 조회 실패·미적재가
부팅이나 요청을 깨뜨리지 않고 폴백(exerciseId null, presign 영상)으로 강등되는지 본다.
"""
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import dynamo  # noqa: E402
from app.exercises import repository  # noqa: E402
from scripts.sync_exercise_catalog import build_items  # noqa: E402

BACKEND_SAMPLE = {
    "barbell-bench-press": {
        "id": "11f18f47-0001-0000-8072-021f94ad3563",
        "slug": "barbell-bench-press",
        "name": "바벨 벤치프레스",
        "thumbnailUrl": "https://cdn.example/thumbnails/barbell-bench-press.webp",
        "videoUrl": "https://cdn.example/videos/barbell-bench-press.mp4",
    },
    "push-up": {
        "id": "11f18f47-0002-0000-8072-021f94ad3563",
        "slug": "push-up",
        "name": "푸시업",
        "thumbnailUrl": None,
        "videoUrl": None,
    },
    "backend-only-exercise": {   # 로컬 metadata에 없는 신규 종목
        "id": "11f18f47-0003-0000-8072-021f94ad3563",
        "slug": "backend-only-exercise",
        "name": "신규 종목",
        "thumbnailUrl": "https://cdn.example/thumbnails/new.webp",
        "videoUrl": "https://cdn.example/videos/new.mp4",
    },
}


def _clear_caches():
    dynamo._resource.cache_clear()
    dynamo.get_exercise_catalog_table.cache_clear()
    repository.get_exercise_catalog.cache_clear()


@pytest.fixture
def catalog_table(monkeypatch):
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(key, "testing")
    with mock_aws():
        _clear_caches()
        resource = boto3.resource("dynamodb", region_name="ap-northeast-2")
        resource.create_table(
            TableName="exercise_catalog",
            KeySchema=[{"AttributeName": "slug", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "slug", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield resource.Table("exercise_catalog")
    _clear_caches()


def test_build_items_keeps_only_joined_slugs():
    # 로컬 metadata에 없는 백엔드 신규 종목은 적재하지 않는다 — 조인 키는 slug
    items = build_items(BACKEND_SAMPLE, {"barbell-bench-press", "push-up"})
    assert [i["slug"] for i in items] == ["barbell-bench-press", "push-up"]
    assert items[0]["exercise_id"] == "11f18f47-0001-0000-8072-021f94ad3563"
    assert items[1]["video_url"] is None   # 영상 미등록 — presign 폴백 대상


def test_catalog_lookup_returns_uuid_and_cdn_urls(catalog_table):
    for item in build_items(BACKEND_SAMPLE, {"barbell-bench-press"}):
        catalog_table.put_item(Item={k: v for k, v in item.items() if v is not None})

    assert repository.exercise_id("barbell-bench-press") == "11f18f47-0001-0000-8072-021f94ad3563"
    assert repository.cdn_video_url("barbell-bench-press").endswith("barbell-bench-press.mp4")
    assert repository.cdn_thumbnail_url("barbell-bench-press").endswith("barbell-bench-press.webp")


def test_catalog_miss_returns_none_not_error(catalog_table):
    # 배치가 아직 안 돈 종목 — 예외가 아니라 None으로 폴백 경로를 태운다
    assert repository.exercise_id("unknown-slug") is None
    assert repository.cdn_video_url("unknown-slug") is None


def test_catalog_load_failure_degrades_to_empty(monkeypatch):
    # 테이블 부재·권한 오류 등 — 서버를 깨뜨리지 않고 빈 카탈로그로 강등
    repository.get_exercise_catalog.cache_clear()

    def boom():
        raise RuntimeError("dynamodb down")

    monkeypatch.setattr(repository, "get_exercise_catalog_table", boom)
    assert repository.get_exercise_catalog() == {}
    repository.get_exercise_catalog.cache_clear()
