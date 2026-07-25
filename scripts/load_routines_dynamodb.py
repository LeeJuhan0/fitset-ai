"""S3 원본 루틴 → 변환 → DynamoDB `routines` 적재 배치.

원본: s3://fitset-routines-raw/routines/{slug}.json (캐글 필드 원문 보존형).
변환: 운동명 → 종목 slug 해석(alias + 휴리스틱), 종목 한글명·썸네일 URL 채움,
level/goal 서버 enum 재매핑, equipment/muscle_groups는 매칭된 종목 metadata 합집합.
적재: PK=slug 멱등 PutItem — 몇 번을 재실행해도 같은 결과.

생존 규칙 (미달 세션은 스킵):
- 운동명이 해석되고 reps > 0(시간 기반 인코딩 아님)인 운동만 유지
- 유지된 운동 >= --min-exercises(기본 2) 그리고 원본 대비 유지율 >= --min-keep-ratio(기본 0.6)
- 같은 프로그램에서 운동 구성(slug·세트·렙)이 동일한 세션은 첫 등장만 유지 (주차 반복 제거)

사용 예:
    uv run --with boto3 python scripts/load_routines_dynamodb.py --dry-run
    uv run --with boto3 python scripts/load_routines_dynamodb.py --limit 200
    uv run --with boto3 python scripts/load_routines_dynamodb.py            # 전량 적재
    # --src-dir <로컬 미러>  : S3 대신 로컬 디렉토리에서 원본을 읽는다 (내용 동일 전제)
"""

import argparse
import ast
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_METADATA = REPO_ROOT / "data" / "exercise-metadata.ko.json"
DEFAULT_ALIAS = REPO_ROOT / "data" / "kaggle-exercise-alias.json"

# 서버 enum 표기 변환 (CLAUDE.md 팀 공통 열거형 기준)
MUSCLE_KEY = {m: m.lower() for m in [
    "Back", "Biceps", "Calves", "Chest", "Core", "Forearms", "Glutes",
    "Hamstrings", "Quadriceps", "Shoulders", "Trapezius", "Triceps",
]}
EQUIP_KEY = {
    "Band": "band", "Barbell": "barbell", "Bodyweight": "bodyweight",
    "Cable Machine": "cableMachine", "Dumbbell": "dumbbell",
    "Kettlebell": "kettlebell", "Machine": "machine",
}

# 캐글 level 리스트 중 최소 요구 수준 채택 (level은 유저 수준 상한 필터)
LEVEL_RANK = {"Beginner": 0, "Novice": 0, "Intermediate": 1, "Advanced": 2}
LEVEL_BY_RANK = {0: "beginner", 1: "intermediate", 2: "advanced"}

# 캐글 goal 토큰 → 서버 GOAL enum (convert_kaggle_routines.py와 동일 — 전수 확인된 매핑)
GOAL_MAP = {
    "Bodybuilding": "hypertrophy",
    "Muscle & Sculpting": "hypertrophy",
    "Powerlifting": "strength",
    "Powerbuilding": "strength",
    "Olympic Weightlifting": "strength",
    "Athletics": "endurance",
    "Bodyweight Fitness": "endurance",
    "At-Home & Calisthenics": "endurance",
}
GOAL_PRIORITY = ["hypertrophy", "strength", "endurance", "weightLoss"]

MAX_EXERCISES = 100  # 서버 비즈니스 규칙: 루틴당 종목 1~100
MAX_SETS = 100       # 서버 비즈니스 규칙: 종목당 세트 1~100


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_tokens(raw):
    if not raw:
        return []
    if raw.startswith("["):
        try:
            return list(ast.literal_eval(raw))
        except (ValueError, SyntaxError):
            return [raw]
    return [raw]


def map_level(raw):
    ranks = [LEVEL_RANK[t] for t in parse_tokens(raw) if t in LEVEL_RANK]
    return LEVEL_BY_RANK[min(ranks)] if ranks else "beginner"


def map_goal(raw):
    votes = [GOAL_MAP[t] for t in parse_tokens(raw) if t in GOAL_MAP]
    if not votes:
        return "hypertrophy"
    return max(GOAL_PRIORITY, key=lambda g: (votes.count(g), -GOAL_PRIORITY.index(g)))


class ExerciseResolver:
    """캐글 운동명 → 종목 slug. alias 우선, 없으면 휴리스틱(괄호 장비 전치·복수형 보정)."""

    def __init__(self, metadata, alias):
        self.slugs = {e["slug"] for e in metadata}
        self.name_to_slug = {slugify(e["name"]): e["slug"] for e in metadata}
        self.alias = alias  # 값 None = 의도적 드랍 (Cardio, Stretching 등)
        self.cache = {}

    def _heuristic(self, name):
        candidates = [name]
        m = re.match(r"^(.*?)\s*\((.+?)\)\s*$", name)
        if m:
            base, equip = m.group(1), m.group(2)
            candidates += [f"{equip} {base}", base, f"{base} {equip}"]
        for c in list(candidates):
            candidates.append(c.rstrip("s"))
        for c in candidates:
            s = slugify(c)
            for variant in (s, s + "s", s.rstrip("s")):
                if variant in self.slugs:
                    return variant
                if variant in self.name_to_slug:
                    return self.name_to_slug[variant]
        return None

    def resolve(self, name):
        if name not in self.cache:
            if name in self.alias:
                self.cache[name] = self.alias[name]
            else:
                self.cache[name] = self._heuristic(name)
        return self.cache[name]


def build_item(raw, resolver, exercise_meta, media_base, now_iso, stats):
    """S3 원본 문서 1건 → DynamoDB item. 생존 규칙 미달이면 None."""
    src_exercises = raw.get("exercises", [])
    exercises = []
    for ex in src_exercises:
        name = ex["exercise_name"].strip()
        slug = resolver.resolve(name)
        stats["exercise_total"] += 1
        reps = ex.get("reps")
        if slug is None or reps is None or reps <= 0:
            continue
        stats["exercise_kept"] += 1
        n_sets = min(max(1, int(ex.get("sets") or 1)), MAX_SETS)
        exercises.append({
            "slug": slug,
            "exercise_name": exercise_meta[slug]["name_ko"],
            "thumbnail_url": f"{media_base}/thumbnails/{slug}.webp",
            "order_index": len(exercises),
            "sets": [
                {"order_index": i, "weight": None, "reps": int(round(reps))}
                for i in range(n_sets)
            ],
        })
        if len(exercises) >= MAX_EXERCISES:
            break

    if not src_exercises or len(exercises) < ARGS.min_exercises:
        return None
    if len(exercises) / len(src_exercises) < ARGS.min_keep_ratio:
        return None

    equipment, muscles = set(), set()
    for ex in exercises:
        meta = exercise_meta[ex["slug"]]
        equipment.update(EQUIP_KEY[e] for e in meta["equipment"])
        muscles.update(MUSCLE_KEY[m] for m in meta["primaryMuscles"])

    program = raw.get("program", {})
    source = raw.get("source", {})
    minutes = program.get("time_per_workout_min")
    week, day = source.get("week"), source.get("day")
    return {
        "slug": raw["slug"],
        "name": f"{source.get('title', raw['slug'])} — {week}주차 {day}일",
        "description": (program.get("description") or "").strip(),
        "exercises": exercises,
        "level": map_level(program.get("level")),
        "equipment": sorted(equipment),
        "goal": map_goal(program.get("goal")),
        "muscle_groups": sorted(muscles),
        "minutes_per_routine": int(minutes) if minutes else None,
        "created_at": now_iso,
        "updated_at": now_iso,
    }


def dedup_key(item, raw):
    """같은 프로그램 내 동일 운동 구성(주차 반복) 판별 키."""
    title = raw.get("source", {}).get("title", "")
    composition = tuple(
        (ex["slug"], len(ex["sets"]), ex["sets"][0]["reps"]) for ex in item["exercises"]
    )
    return (title, composition)


def iter_raw_local(src_dir, limit):
    paths = sorted(Path(src_dir).glob("*.json"))
    for path in paths[:limit] if limit else paths:
        yield json.loads(path.read_text())


def iter_raw_s3(bucket, prefix, workers, limit):
    s3 = boto3.client("s3")
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(o["Key"] for o in page.get("Contents", []))
        if limit and len(keys) >= limit:
            keys = keys[:limit]
            break

    def fetch(key):
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())

    with ThreadPoolExecutor(max_workers=workers) as pool:
        yield from pool.map(fetch, keys)


def main():
    metadata = json.loads(Path(ARGS.metadata).read_text())
    alias = json.loads(Path(ARGS.alias).read_text())
    resolver = ExerciseResolver(metadata, alias)
    exercise_meta = {e["slug"]: e for e in metadata}
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if ARGS.src_dir:
        raw_iter = iter_raw_local(ARGS.src_dir, ARGS.limit)
    else:
        raw_iter = iter_raw_s3(ARGS.raw_bucket, ARGS.raw_prefix, ARGS.workers, ARGS.limit)

    stats = {"read": 0, "kept": 0, "dropped": 0, "dup": 0,
             "exercise_total": 0, "exercise_kept": 0}
    seen = set()
    items = []
    for raw in raw_iter:
        stats["read"] += 1
        item = build_item(raw, resolver, exercise_meta, ARGS.media_base_url, now_iso, stats)
        if item is None:
            stats["dropped"] += 1
            continue
        key = dedup_key(item, raw)
        if key in seen:
            stats["dup"] += 1
            continue
        seen.add(key)
        stats["kept"] += 1
        items.append(item)
        if stats["read"] % 10000 == 0:
            print(f"  ...{stats['read']:,} 읽음 / {stats['kept']:,} 생존", file=sys.stderr)

    print(f"읽음 {stats['read']:,} → 생존 {stats['kept']:,} "
          f"(규칙 미달 {stats['dropped']:,} · 주차 중복 {stats['dup']:,}) | "
          f"운동 행 유지 {stats['exercise_kept']:,}/{stats['exercise_total']:,} "
          f"({stats['exercise_kept'] / max(stats['exercise_total'], 1) * 100:.1f}%)")

    if ARGS.dry_run:
        print("dry-run — DynamoDB 쓰기 생략")
        return

    table = boto3.resource("dynamodb", region_name=ARGS.region).Table(ARGS.table)
    written = 0
    with table.batch_writer(overwrite_by_pkeys=["slug"]) as batch:
        for item in items:
            batch.put_item(Item=item)
            written += 1
            if written % 10000 == 0:
                print(f"  ...{written:,}/{len(items):,} 적재", file=sys.stderr)
    print(f"DynamoDB `{ARGS.table}` 적재 완료: {written:,}건 (멱등 upsert)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-bucket", default="fitset-routines-raw")
    parser.add_argument("--raw-prefix", default="routines/")
    parser.add_argument("--src-dir", help="S3 대신 읽을 로컬 원본 디렉토리")
    parser.add_argument("--table", default="routines")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--metadata", default=str(DEFAULT_METADATA))
    parser.add_argument("--alias", default=str(DEFAULT_ALIAS))
    parser.add_argument("--media-base-url",
                        default="https://fitset-exercise-media.s3.ap-northeast-2.amazonaws.com")
    parser.add_argument("--min-exercises", type=int, default=2)
    parser.add_argument("--min-keep-ratio", type=float, default=0.6)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    ARGS = parser.parse_args()
    main()
