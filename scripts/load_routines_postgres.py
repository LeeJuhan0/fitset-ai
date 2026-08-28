"""DynamoDB `routines` 또는 S3 스냅샷 → Postgres(pgvector) `routines` 적재 배치.

이미 변환·임베딩된 항목을 그대로 옮긴다. 원천(S3 캐글)에서 다시 변환하거나 Bedrock 을 다시
부르지 않으므로 비용이 없다. slug 기준 upsert 라 몇 번을 재실행해도 같은 결과다.
임베딩이 없는 항목은 스킵한다.

입력은 둘 중 하나다.
  기본        DynamoDB `routines` 를 Scan 한다 (--table, --region).
  --source    JSONL(.gz) 파일. DynamoDB 전량 덤프로, embedding 은 Binary 를 base64 로 적은 것이다.
              s3://fitset-routines-raw/snapshots/routines-strict-20260825.jsonl.gz 또는 로컬 경로.
              DynamoDB routines 를 지운 뒤에는 이쪽만 남는다.

스키마는 scripts/sql/routines_pgvector.sql 을 먼저 적용한다. --ddl 로 넘기면 적재 전에 여기서 적용한다
(IF NOT EXISTS 라 멱등, 이미지에 psql 이 없어 psycopg 로 실행).

접속은 --dsn, PG_DSN, 그리고 앱과 같은 PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DATABASE 순으로 찾는다.
PG_HOST 가 비어 있으면 아직 RDS 가 없는 것이므로 아무것도 하지 않고 0 으로 끝난다 — ArgoCD hook Job 이
apply 전에 돌아도 sync 가 실패하지 않게.

사용 예:
    export PG_DSN="postgresql://fitset:...@fitset-rds-pgvector.../fitset"
    python scripts/load_routines_postgres.py --dry-run
    python scripts/load_routines_postgres.py --limit 200
    python scripts/load_routines_postgres.py
    python scripts/load_routines_postgres.py --ddl scripts/sql/routines_pgvector.sql \
        --source s3://fitset-routines-raw/snapshots/routines-strict-20260825.jsonl.gz
"""

import argparse
import base64
import gzip
import io
import json
import os
import struct
import sys
from decimal import Decimal

import boto3
import psycopg
from pgvector.psycopg import register_vector

LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}
BATCH = 500

UPSERT_ROUTINE = """
INSERT INTO routines (slug, name, description, goal, level, minutes, muscle_groups, equipment,
                      bodyweight_only, exercise_count, exercise_names, body, embedding, embedding_model, source)
VALUES (%(slug)s, %(name)s, %(description)s, %(goal)s, %(level)s, %(minutes)s, %(muscle_groups)s, %(equipment)s,
        %(bodyweight_only)s, %(exercise_count)s, %(exercise_names)s, %(body)s, %(embedding)s, %(embedding_model)s, %(source)s)
ON CONFLICT (slug) DO UPDATE SET
  name = EXCLUDED.name, description = EXCLUDED.description, goal = EXCLUDED.goal, level = EXCLUDED.level,
  minutes = EXCLUDED.minutes, muscle_groups = EXCLUDED.muscle_groups, equipment = EXCLUDED.equipment,
  bodyweight_only = EXCLUDED.bodyweight_only, exercise_count = EXCLUDED.exercise_count,
  exercise_names = EXCLUDED.exercise_names, body = EXCLUDED.body, embedding = EXCLUDED.embedding,
  embedding_model = EXCLUDED.embedding_model, source = EXCLUDED.source, updated_at = now()
"""



def to_plain(value):
    """DynamoDB Decimal 을 int/float 로 되돌린다."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    return value


def unpack_embedding(raw, dimension):
    """float32 리틀엔디언 팩을 리스트로 푼다. 차원이 다르면 None."""
    data = bytes(raw)
    if len(data) != dimension * 4:
        return None
    return list(struct.unpack(f"<{dimension}f", data))


def scan_items(table, limit):
    """routines 전량 Scan. limit 이 있으면 그 수에서 멈춘다."""
    kwargs = {}
    count = 0
    while True:
        page = table.scan(**kwargs)
        for item in page.get("Items", []):
            yield item
            count += 1
            if limit and count >= limit:
                return
        if "LastEvaluatedKey" not in page:
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def open_source(source):
    """--source 를 줄 단위 텍스트 스트림으로 연다. s3:// 와 로컬, .gz 를 모두 받는다."""
    if source.startswith("s3://"):
        bucket, key = source[5:].split("/", 1)
        raw = boto3.client("s3", region_name=ARGS.region).get_object(Bucket=bucket, Key=key)["Body"]
    else:
        raw = open(source, "rb")
    if source.endswith(".gz"):
        raw = gzip.GzipFile(fileobj=raw)
    return io.TextIOWrapper(raw, encoding="utf-8")


def snapshot_items(source, limit):
    """JSONL 스냅샷을 한 줄씩 DynamoDB item 꼴로 돌려준다. embedding 은 base64 를 bytes 로 되돌린다."""
    with open_source(source) as f:
        for count, line in enumerate(f, 1):
            item = json.loads(line)
            if isinstance(item.get("embedding"), str):
                item["embedding"] = base64.b64decode(item["embedding"])
            yield item
            if limit and count >= limit:
                return


def build_row(item, dimension):
    """DynamoDB item 을 routines 행으로 만든다. 임베딩이 없으면 None."""
    embedding = item.get("embedding")
    if embedding is None:
        return None
    vector = unpack_embedding(embedding, dimension)
    if vector is None:
        return None
    plain = to_plain({k: v for k, v in item.items() if k not in ("embedding",)})
    exercises = plain.get("exercises", [])
    equipment = sorted(plain.get("equipment", []))
    minutes = plain.get("minutes_per_routine")
    body = {
        "slug": plain["slug"],
        "name": plain.get("name", ""),
        "minutes_per_routine": minutes,
        "exercises": exercises,
    }
    row = {
        "slug": plain["slug"],
        "name": plain.get("name"),
        "description": plain.get("description"),
        "goal": plain.get("goal"),
        "level": LEVEL_ORDER.get(plain.get("level", "beginner"), 0),
        "minutes": int(minutes) if minutes else None,
        "muscle_groups": sorted(plain.get("muscle_groups", [])),
        "equipment": equipment,
        "bodyweight_only": set(equipment) <= {"bodyweight"},
        "exercise_count": len(exercises),
        "exercise_names": [ex["exercise_name"] for ex in exercises],
        "body": json.dumps(body, ensure_ascii=False),
        "embedding": vector,
        "embedding_model": plain.get("embedding_model", "unknown"),
        "source": "kaggle",
    }
    return row


def resolve_dsn():
    """--dsn, PG_DSN, 앱과 같은 PG_* 환경변수 순으로 접속 문자열을 만든다. host 가 없으면 None."""
    if ARGS.dsn or os.environ.get("PG_DSN"):
        return ARGS.dsn or os.environ["PG_DSN"]
    host = os.environ.get("PG_HOST", "")
    if not host:
        return None
    user = os.environ.get("PG_USER", "fitset")
    password = os.environ.get("PG_PASSWORD", "")
    port = os.environ.get("PG_PORT", "5432")
    database = os.environ.get("PG_DATABASE", "fitset")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def apply_ddl(conn, path):
    """DDL 파일을 실행한다. IF NOT EXISTS 라 재실행해도 안전하다."""
    with open(path, encoding="utf-8") as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print(f"ddl applied: {path}", file=sys.stderr)


def write_batch(conn, rows):
    """routines slug upsert. 한 트랜잭션."""
    with conn.cursor() as cur:
        cur.executemany(UPSERT_ROUTINE, rows)
    conn.commit()


def main():
    dsn = resolve_dsn()
    if not dsn and not ARGS.dry_run:
        print("PG_HOST 미설정 — 적재를 건너뛴다", file=sys.stderr)
        return
    if ARGS.source:
        items = snapshot_items(ARGS.source, ARGS.limit)
    else:
        table = boto3.resource("dynamodb", region_name=ARGS.region).Table(ARGS.table)
        items = scan_items(table, ARGS.limit)
    conn = None
    if not ARGS.dry_run:
        conn = psycopg.connect(dsn)
        if ARGS.ddl:
            apply_ddl(conn, ARGS.ddl)
        register_vector(conn)

    stats = {"scanned": 0, "skipped_no_embedding": 0, "written": 0}
    batch = []
    for item in items:
        stats["scanned"] += 1
        built = build_row(item, ARGS.dimension)
        if built is None:
            stats["skipped_no_embedding"] += 1
            continue
        batch.append(built)
        if len(batch) < BATCH:
            continue
        if conn:
            write_batch(conn, batch)
        stats["written"] += len(batch)
        batch = []
        print(f"  {stats['written']} written", file=sys.stderr)
    if batch:
        if conn:
            write_batch(conn, batch)
        stats["written"] += len(batch)
    if conn:
        conn.close()
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ddl", help="적재 전에 실행할 SQL 파일. 보통 scripts/sql/routines_pgvector.sql")
    parser.add_argument("--source", help="JSONL(.gz) 스냅샷 경로. s3:// 또는 로컬. 없으면 DynamoDB Scan")
    parser.add_argument("--table", default="routines")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--dsn", help="postgresql://user:pw@host:5432/db. 없으면 PG_DSN 환경변수")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true", help="Scan 과 변환만 하고 쓰지 않는다")
    ARGS = parser.parse_args()
    main()
