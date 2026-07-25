"""DynamoDB `routines` 임베딩 배치 — 묘사 임베딩을 계산해 `embedding` 컬럼에 저장.

임베딩 텍스트 = 한글 종목명 나열 + 프로그램 description(영문) — 다국어 모델이라 혼용 무방.
요청 경로에서는 유저 쿼리 1회만 임베딩하고, 후보 30개와의 코사인 유사도는
이 컬럼을 인메모리에 로드해 계산한다 (요청당 Bedrock 호출 1회).

- 모델: Bedrock `global.cohere.embed-v4:0` (다국어), 1024차원 float32
- 저장: `embedding` Binary — float32 리틀엔디언 팩(4KB), `embedding_model` 태그로 버전 관리
- 멱등: 같은 모델 태그의 임베딩이 이미 있으면 스킵 (--force로 재계산)

사용 예:
    uv run --with boto3 python scripts/embed_routines.py --limit 100   # 테스트
    uv run --with boto3 python scripts/embed_routines.py               # 전량
"""

import argparse
import json
import random
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

MAX_TEXT_CHARS = 2000  # description이 긴 프로그램 대비 절단
MAX_THROTTLE_RETRIES = 8  # Bedrock 토큰 스로틀링 대비 지수 백오프


def embedding_text(item):
    names = ", ".join(ex["exercise_name"] for ex in item.get("exercises", []))
    desc = (item.get("description") or "").strip()
    return f"{names}. {desc}"[:MAX_TEXT_CHARS]


def pack_floats(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def scan_targets(table, model_tag, force, limit):
    """임베딩 대상 스캔. 이미 같은 태그로 임베딩된 항목은 스킵."""
    targets = []
    kwargs = {
        "ProjectionExpression": "slug, description, exercises, embedding_model",
    }
    while True:
        page = table.scan(**kwargs)
        for item in page["Items"]:
            if not force and item.get("embedding_model") == model_tag:
                continue
            targets.append(item)
            if limit and len(targets) >= limit:
                return targets
        if "LastEvaluatedKey" not in page:
            return targets
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def main():
    model_tag = f"{ARGS.model}#{ARGS.dimension}#float32"
    table = boto3.resource("dynamodb", region_name=ARGS.region).Table(ARGS.table)
    runtime = boto3.client(
        "bedrock-runtime", region_name=ARGS.region,
        config=Config(retries={"max_attempts": 10, "mode": "adaptive"}),
    )

    print("대상 스캔 중...", file=sys.stderr)
    targets = scan_targets(table, model_tag, ARGS.force, ARGS.limit)
    print(f"임베딩 대상: {len(targets):,}건 (모델 태그 {model_tag})")
    if ARGS.dry_run or not targets:
        return

    def embed_batch(batch):
        body = {
            "texts": [embedding_text(item) for item in batch],
            "input_type": "search_document",
            "embedding_types": ["float"],
            "output_dimension": ARGS.dimension,
        }
        for attempt in range(MAX_THROTTLE_RETRIES):
            try:
                resp = runtime.invoke_model(modelId=ARGS.model, body=json.dumps(body))
                break
            except ClientError as e:
                if e.response["Error"]["Code"] != "ThrottlingException":
                    raise
                if attempt == MAX_THROTTLE_RETRIES - 1:
                    raise
                delay = min(2 ** attempt, 60) + random.uniform(0, 1)
                print(f"  스로틀링 — {delay:.0f}s 대기 후 재시도", file=sys.stderr)
                time.sleep(delay)
        vectors = json.loads(resp["body"].read())["embeddings"]["float"]
        return [(item["slug"], pack_floats(vec)) for item, vec in zip(batch, vectors)]

    batches = [targets[i:i + ARGS.batch_size] for i in range(0, len(targets), ARGS.batch_size)]
    done = 0
    with ThreadPoolExecutor(max_workers=ARGS.workers) as pool:
        for results in pool.map(embed_batch, batches):
            for slug, packed in results:
                table.update_item(
                    Key={"slug": slug},
                    UpdateExpression="SET embedding = :e, embedding_model = :m",
                    ExpressionAttributeValues={":e": packed, ":m": model_tag},
                )
            done += len(results)
            if done % 5000 < ARGS.batch_size:
                print(f"  ...{done:,}/{len(targets):,}", file=sys.stderr)

    print(f"임베딩 저장 완료: {done:,}건 → `{ARGS.table}.embedding` ({ARGS.dimension}d float32 Binary)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", default="routines")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--model", default="global.cohere.embed-v4:0")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=32, help="Bedrock 호출당 텍스트 수 (최대 96)")
    parser.add_argument("--workers", type=int, default=2, help="스로틀링 한도 내 동시 호출 수")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true", help="기존 임베딩도 재계산")
    parser.add_argument("--dry-run", action="store_true")
    ARGS = parser.parse_args()
    main()
