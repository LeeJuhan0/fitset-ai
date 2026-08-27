"""DynamoDB `routines` → Postgres(pgvector) `routines` 적재 배치.

DynamoDB 에 이미 변환·임베딩된 항목을 그대로 옮긴다. 원천(S3 캐글)에서 다시 변환하거나
Bedrock 을 다시 부르지 않으므로 비용이 없고, 인메모리 스토어가 부팅 때 하던 Scan 과 같은 입력을 쓴다.
slug 기준 upsert 라 몇 번을 재실행해도 같은 결과다. 임베딩이 없는 항목은 스킵한다.

스키마는 scripts/sql/routines_pgvector.sql 을 먼저 적용한다.

사용 예:
    export PG_DSN="postgresql://admin:...@fitset-rds-pgvector.../fitset"
    uv run --with boto3 --with "psycopg[binary]" --with pgvector python scripts/load_routines_postgres.py --dry-run
    uv run --with boto3 --with "psycopg[binary]" --with pgvector python scripts/load_routines_postgres.py --limit 200
    uv run --with boto3 --with "psycopg[binary]" --with pgvector python scripts/load_routines_postgres.py
"""

import argparse
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
RETURNING id
"""

INSERT_EXERCISE = """
INSERT INTO routine_exercises (routine_id, order_index, exercise_slug, exercise_name)
VALUES (%s, %s, %s, %s)
"""

INSERT_SET = """
INSERT INTO routine_sets (routine_id, order_index, set_index, reps, weight, duration_sec)
VALUES (%s, %s, %s, %s, %s, %s)
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


def build_row(item, dimension):
    """DynamoDB item 을 routines 행과 정규화 행으로 나눈다. 임베딩이 없으면 None."""
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
    return row, exercises


def write_batch(conn, rows):
    """routines upsert 후 정규화 행을 지우고 다시 넣는다. 한 트랜잭션."""
    with conn.cursor() as cur:
        for row, exercises in rows:
            cur.execute(UPSERT_ROUTINE, row)
            routine_id = cur.fetchone()[0]
            cur.execute("DELETE FROM routine_exercises WHERE routine_id = %s", (routine_id,))
            for ex in exercises:
                cur.execute(INSERT_EXERCISE, (routine_id, ex["order_index"], ex["slug"], ex["exercise_name"]))
                cur.executemany(INSERT_SET, [
                    (routine_id, ex["order_index"], s["order_index"], s.get("reps"), s.get("weight"), s.get("duration_seconds"))
                    for s in ex.get("sets", [])
                ])
    conn.commit()


def main():
    dsn = ARGS.dsn or os.environ.get("PG_DSN")
    if not dsn and not ARGS.dry_run:
        sys.exit("PG_DSN 환경변수 또는 --dsn 이 필요하다")
    table = boto3.resource("dynamodb", region_name=ARGS.region).Table(ARGS.table)
    conn = None
    if not ARGS.dry_run:
        conn = psycopg.connect(dsn)
        register_vector(conn)

    stats = {"scanned": 0, "skipped_no_embedding": 0, "written": 0}
    batch = []
    for item in scan_items(table, ARGS.limit):
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
    parser.add_argument("--table", default="routines")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--dsn", help="postgresql://user:pw@host:5432/db. 없으면 PG_DSN 환경변수")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true", help="Scan 과 변환만 하고 쓰지 않는다")
    ARGS = parser.parse_args()
    main()
