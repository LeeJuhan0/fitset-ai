"""종목 카탈로그 저장소·동기화 배치 테스트 — 백엔드 마스터 캐시(2026-08-05 신설).

카탈로그는 있으면 좋고 없어도 서버가 도는 보조 데이터다. 조회 실패·미적재가
부팅이나 요청을 깨뜨리지 않고 폴백(exerciseId null, 종목 마스터 videoUrl)으로 강등되는지 본다.
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
    assert items[1]["video_url"] is None   # 영상 미등록 — 종목 마스터 videoUrl 폴백 대상


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


def test_routine_payload_carries_exercise_id_and_cdn_thumbnail(monkeypatch):
    # §1 루틴 payload — 카탈로그가 있으면 UUID·CDN 썸네일, 없으면 null·적재값 폴백
    from app.routines import service as routines_service
    from app.routines.schemas import RoutineGenerateRequest

    routine = {
        "slug": "chest-45", "name": "가슴 45분", "minutes_per_routine": 45,
        "exercises": [{
            "slug": "barbell-bench-press", "exercise_name": "바벨 벤치프레스",
            "thumbnail_url": "https://fitset-exercise-media.s3.amazonaws.com/thumbnails/x.webp",
            "order_index": 0, "sets": [{"order_index": 0, "reps": 10}],
        }],
    }
    request = RoutineGenerateRequest(
        level="intermediate", muscleGroups=["chest"], minutes=45, includeWarmup=False
    )
    catalog = {"barbell-bench-press": {
        "slug": "barbell-bench-press",
        "exercise_id": "11f18f47-0001-0000-8072-021f94ad3563",
        "thumbnail_url": "https://cdn.example/thumbnails/barbell-bench-press.webp",
    }}
    monkeypatch.setattr(repository, "get_exercise_catalog", lambda: catalog)

    out = routines_service._build_response(routine, request, {}, ({}, {}), {})
    assert out.exercises[0].exercise_id == "11f18f47-0001-0000-8072-021f94ad3563"
    assert out.exercises[0].thumbnail_url.startswith("https://cdn.example/")   # S3 직접 주소 아님

    # 카탈로그 미스 — null + 적재값 폴백, 예외 없음
    monkeypatch.setattr(repository, "get_exercise_catalog", dict)
    out = routines_service._build_response(routine, request, {}, ({}, {}), {})
    assert out.exercises[0].exercise_id is None
    assert "s3.amazonaws.com" in out.exercises[0].thumbnail_url
    assert out.exercises[0].sets[0].weight == 0   # 맨몸·추천 불가 — null 아닌 0


@pytest.fixture
def catalog_only(monkeypatch):
    """카탈로그만 채우고 내부 API는 대역으로 — 영상 URL 출처 분기 검증용."""
    monkeypatch.setattr(repository, "get_exercise_catalog", lambda: {
        "barbell-bench-press": {
            "slug": "barbell-bench-press",
            "exercise_id": "11f18f47-0001-0000-8072-021f94ad3563",
            "video_url": "https://cdn.example/videos/barbell-bench-press.mp4",
        },
        "push-up": {"slug": "push-up", "exercise_id": "11f18f47-0002-0000-8072-021f94ad3563"},
    })


async def test_video_prefers_cdn_without_expiry(catalog_only):
    from app.exercises import service as exercises_service

    video = await exercises_service.get_video("barbell-bench-press")
    assert video["videoUrl"].startswith("https://cdn.example/")
    assert video["expiresAt"] is None            # CDN은 만료가 없다 — 재발급 왕복 불필요
    assert video["exerciseId"].endswith("021f94ad3563")


async def test_video_fallback_rereads_catalog(catalog_only):
    # 재발급(fallback=true)도 같은 카탈로그 CDN을 다시 읽는다 —
    # 구 폴백(내부 API §4.3 videoUrl)은 내부 API 파기(2026-08-12)로 소멸
    from app.exercises import service as exercises_service

    video = await exercises_service.get_video("barbell-bench-press", fallback=True)
    assert video["videoUrl"].startswith("https://cdn.example/")
    assert video["expiresAt"] is None            # 공개 CDN — 만료 없음


async def test_video_missing_in_catalog_is_not_found(catalog_only):
    # 카탈로그에 영상이 없는 종목 — 대체 출처가 없으므로 영상 없음으로 끝난다
    from app.core.errors import VideoNotFoundError
    from app.exercises import service as exercises_service

    with pytest.raises(VideoNotFoundError):
        await exercises_service.get_video("push-up")


def _routine_with(slug: str) -> dict:
    return {
        "slug": "r1", "name": "루틴", "minutes_per_routine": 30,
        "exercises": [{
            "slug": slug, "exercise_name": "종목", "thumbnail_url": "",
            "order_index": 0,
            "sets": [{"order_index": n, "reps": 12} for n in range(2)],
        }],
    }


@pytest.mark.parametrize("kind,expect", [
    ("DURATION", {"reps": 0, "duration": 30}),      # 플랭크·월싯 — 시간만, 나머지 0
    ("REPS_ONLY", {"reps": 12, "duration": 0}),     # 맨몸 스쿼트 — 렙만
    ("WEIGHT_AND_REPS", {"reps": 12, "duration": 0}),
    (None, {"reps": 12, "duration": 0}),            # 카탈로그 미스 — 종전 동작
])
def test_set_shape_follows_exercise_type(monkeypatch, kind, expect):
    from app.routines import service as routines_service
    from app.routines.schemas import RoutineGenerateRequest

    entry = {"slug": "wall-sit", "exercise_id": "uuid-1"}
    if kind:
        entry["exercise_type"] = kind
    monkeypatch.setattr(repository, "get_exercise_catalog", lambda: {"wall-sit": entry})

    request = RoutineGenerateRequest(
        level="beginner", muscleGroups=["core"], minutes=30, includeWarmup=False
    )
    out = routines_service._build_response(_routine_with("wall-sit"), request, {}, ({}, {}), {})
    first = out.exercises[0].sets[0]
    assert first.reps == expect["reps"]
    assert first.duration_seconds == expect["duration"]
    if kind == "DURATION":
        assert first.weight == 0   # 시간 종목은 무게도 0 (null 아님)
    # 세 필드 모두 not null — 클라가 null 분기를 하지 않는다
    assert None not in (first.weight, first.reps, first.duration_seconds)


def test_warmup_shortens_duration_instead_of_weight(monkeypatch):
    # 워밍업 = 부하 낮추기 — 시간 종목은 무게가 없으니 시간을 줄인다
    from app.routines import service as routines_service
    from app.routines.schemas import RoutineGenerateRequest

    monkeypatch.setattr(repository, "get_exercise_catalog", lambda: {
        "hand-plank": {"slug": "hand-plank", "exercise_type": "DURATION"}
    })
    request = RoutineGenerateRequest(
        level="beginner", muscleGroups=["core"], minutes=30, includeWarmup=True
    )
    sets = routines_service._build_response(
        _routine_with("hand-plank"), request, {}, ({}, {}), {}
    ).exercises[0].sets
    # 플랭크 30초 × 초급 0.7 = 20초, 워밍업인 첫 세트만 절반
    assert sets[0].duration_seconds == 10
    assert sets[1].duration_seconds == 20


@pytest.mark.parametrize("slug,level,expect", [
    ("hand-plank", "beginner", 20),        # 30 × 0.7 = 21 → 5초 단위 20
    ("hand-plank", "intermediate", 30),
    ("hand-plank", "advanced", 40),        # 30 × 1.3 = 39 → 40
    ("wall-sit", "intermediate", 45),      # 등척성 하체는 더 길게
    ("wall-sit", "beginner", 30),          # 45 × 0.7 = 31.5 → 30
    ("elbow-side-plank", "intermediate", 25),
    ("abdominals-stretch-variation-one", "intermediate", 20),
])
def test_duration_scales_by_exercise_and_level(monkeypatch, slug, level, expect):
    from app.routines import service as routines_service
    from app.routines.schemas import RoutineGenerateRequest

    monkeypatch.setattr(repository, "get_exercise_catalog", lambda: {
        slug: {"slug": slug, "exercise_type": "DURATION"}
    })
    request = RoutineGenerateRequest(
        level=level, muscleGroups=["core"], minutes=30, includeWarmup=False
    )
    out = routines_service._build_response(_routine_with(slug), request, {}, ({}, {}), {})
    assert out.exercises[0].sets[0].duration_seconds == expect


def test_unknown_duration_exercise_uses_config_default(monkeypatch):
    # 카탈로그에 새로 생긴 시간 종목 — 종목별 표에 없으면 설정 기본값 × 난이도
    from app.routines import service as routines_service
    from app.routines.schemas import RoutineGenerateRequest

    monkeypatch.setattr(repository, "get_exercise_catalog", lambda: {
        "new-hold": {"slug": "new-hold", "exercise_type": "DURATION"}
    })
    request = RoutineGenerateRequest(
        level="advanced", muscleGroups=["core"], minutes=30, includeWarmup=False
    )
    out = routines_service._build_response(_routine_with("new-hold"), request, {}, ({}, {}), {})
    assert out.exercises[0].sets[0].duration_seconds == 40   # 기본 30 × 1.3 → 40
