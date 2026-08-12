"""백엔드 종목 마스터 → DynamoDB `exercise_catalog` 동기화 배치 (일 1회).

AI 서버가 자체 생성할 수 없는 값만 들여온다 — 백엔드 UUID(exerciseId), 종목 수행 방식
(exerciseType — 세트를 무게·렙으로 줄지 시간으로 줄지 가른다), CDN 썸네일·영상 URL.
한글명·부위·장비 등은 repo 동봉 metadata(206종)가 정본이라 저장하지 않는다.

출처는 백엔드 공개 API `GET /api/v1/exercises` — 무인증으로 200을 반환한다(2026-08-05 실측).
유저 데이터 엔드포인트는 401이므로 이 공개 여부는 마스터 카탈로그에 한정된 정책이다.

조인 키는 slug — 백엔드 206종과 로컬 metadata 206종이 1:1로 일치함을 확인했다(2026-08-05).
불일치가 생기면 백엔드에 종목이 추가·삭제된 것이므로 경고로 남기고 교집합만 적재한다.

사용 예:
    uv run python scripts/sync_exercise_catalog.py --dry-run
    uv run python scripts/sync_exercise_catalog.py --create-table   # 최초 1회
    uv run python scripts/sync_exercise_catalog.py                  # 정기 실행
"""
import argparse
import json
import sys
from pathlib import Path

import boto3
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402


def fetch_backend_catalog(url: str, timeout: float) -> dict[str, dict]:
    """백엔드 공개 API → slug 키 딕셔너리. 응답 봉투({traceId, data.items})를 벗긴다."""
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    items = response.json()["data"]["items"]
    return {item["slug"]: item for item in items if item.get("slug")}


def local_slugs(metadata_path: str) -> set[str]:
    """repo 동봉 종목 마스터의 slug 집합 — 조인 대상."""
    entries = json.loads(Path(metadata_path).read_text())
    return {entry["slug"] for entry in entries}


def build_items(backend: dict[str, dict], slugs: set[str]) -> list[dict]:
    """교집합 slug만 카탈로그 항목으로 변환. 저장 필드는 UUID·CDN URL 3종뿐."""
    items = []
    for slug in sorted(slugs & set(backend)):
        entry = backend[slug]
        items.append({
            "slug": slug,
            "exercise_id": entry["id"],
            # 세트 구성 분기 키 — WEIGHT_AND_REPS(166) · REPS_ONLY(32) · DURATION(8)
            "exercise_type": entry.get("exerciseType"),
            "thumbnail_url": entry.get("thumbnailUrl"),
            "video_url": entry.get("videoUrl"),
        })
    return items


def create_table(table_name: str, region: str) -> None:
    """카탈로그 테이블 생성 (PK=slug, 온디맨드). 이미 있으면 조용히 넘어간다."""
    client = boto3.client("dynamodb", region_name=region)
    try:
        client.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "slug", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "slug", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName=table_name)
        print(f"테이블 생성 완료: {table_name}")
    except client.exceptions.ResourceInUseException:
        print(f"테이블 이미 존재: {table_name}")


def put_items(table_name: str, region: str, items: list[dict]) -> None:
    """PK=slug 멱등 적재 — 몇 번 재실행해도 같은 결과."""
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item={k: v for k, v in item.items() if v is not None})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="적재 없이 조인 결과만 출력")
    parser.add_argument("--create-table", action="store_true", help="적재 전 테이블 생성 시도")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    settings = get_settings()
    backend = fetch_backend_catalog(settings.backend_exercises_url, args.timeout)
    slugs = local_slugs(settings.exercise_metadata_path)

    only_backend = sorted(set(backend) - slugs)
    only_local = sorted(slugs - set(backend))
    if only_backend:
        print(f"경고: 백엔드에만 있는 종목 {len(only_backend)}종 — {only_backend[:5]}")
    if only_local:
        print(f"경고: 로컬에만 있는 종목 {len(only_local)}종 — {only_local[:5]}")

    items = build_items(backend, slugs)
    missing_video = [i["slug"] for i in items if not i["video_url"]]
    print(f"백엔드 {len(backend)}종 · 로컬 {len(slugs)}종 → 적재 대상 {len(items)}종")
    print(f"영상 URL 없는 종목: {len(missing_video)}종 (종목 마스터 videoUrl 폴백 대상)")

    if args.dry_run:
        print("dry-run — 적재 생략. 샘플:")
        print(json.dumps(items[:2], ensure_ascii=False, indent=2))
        return 0

    if args.create_table:
        create_table(settings.exercise_catalog_table, settings.aws_region)
    put_items(settings.exercise_catalog_table, settings.aws_region, items)
    print(f"적재 완료: {settings.exercise_catalog_table} ← {len(items)}종")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
