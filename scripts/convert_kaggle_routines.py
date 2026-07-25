"""캐글 600k-programs 데이터셋 → routines 스키마 변환 배치.

원본: programs_detailed_boostcamp_kaggle.csv (운동 1행 단위, 프로그램별 연속 정렬).
프로그램의 (week, day) 세션 하나를 루틴 문서 하나로 변환하고,
같은 프로그램 안에서 운동 구성이 동일한 세션은 1개만 남긴다(주차 반복 제거).

출력은 S3 원본 규약과 같은 JSON 문서(JSONL 1줄 1문서, --per-file이면 slug.json 개별 파일).
S3 → MongoDB 적재 배치가 _id(slug)로 멱등 upsert 하는 것을 전제로 한다.

사용 예:
    python scripts/convert_kaggle_routines.py \
        --src ~/fitset-recsys-data/600k-programs/programs_detailed_boostcamp_kaggle.csv \
        --out routines.jsonl --limit 100
"""

import argparse
import ast
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ── 매핑 테이블 (원본 토큰 집합은 닫혀 있음 — 전수 확인 완료) ──

# 원본 level 리스트 중 최소 요구 수준을 루틴 level로 사용 (유저 수준 상한 필터)
LEVEL_RANK = {"Beginner": 0, "Novice": 0, "Intermediate": 1, "Advanced": 2}
LEVEL_BY_RANK = {0: "beginner", 1: "intermediate", 2: "advanced"}

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

# 원본 equipment는 체육관 유형 1개 — 필요 장비 목록으로 근사 (팀 합의로 조정 가능)
EQUIPMENT_MAP = {
    "Full Gym": ["barbell", "dumbbell", "machine", "cable", "bench", "rack", "pullup_bar"],
    "Garage Gym": ["barbell", "dumbbell", "bench", "rack", "pullup_bar"],
    "Dumbbell Only": ["dumbbell"],
    "At Home": [],
    "": [],
}

# 운동명 키워드 → 근육군. 순서 중요: 먼저 매칭된 패턴 1개만 사용
# (예: "leg curl"이 일반 "curl"보다 앞이어야 함)
MUSCLE_PATTERNS = [
    (r"leg curl|hamstring curl|nordic", ["hamstrings"]),
    (r"leg extension", ["quads"]),
    (r"back extension|hyperextension|good morning", ["back", "hamstrings"]),
    (r"romanian|rdl|stiff.?leg", ["hamstrings", "glutes"]),
    (r"deadlift", ["back", "hamstrings", "glutes"]),
    (r"calf|tibialis", ["calves"]),
    (r"squat|leg press|lunge|step.?up|pistol", ["quads", "glutes"]),
    (r"hip thrust|glute|hip abduct", ["glutes"]),
    (r"fly|flye|pec deck|chest press|bench|push.?up|dip", ["chest", "triceps"]),
    (r"shoulder press|overhead press|military|ohp|lateral raise|front raise"
     r"|rear delt|face pull|upright row|shrug", ["shoulders"]),
    (r"row|pulldown|pull.?down|pull.?up|chin.?up|lat |lat$|pullover", ["back"]),
    (r"curl", ["biceps"]),
    (r"pushdown|push.?down|skull|close.?grip|tricep|extension", ["triceps"]),
    (r"plank|crunch|sit.?up|ab |abs|ab$|hollow|leg raise|knee raise"
     r"|russian twist|dead bug|bird dog|rollout|core", ["core"]),
    (r"wrist|farmer|grip", ["forearms"]),
]
MUSCLE_PATTERNS = [(re.compile(p, re.IGNORECASE), g) for p, g in MUSCLE_PATTERNS]


def parse_tokens(raw):
    """"['Beginner', 'Novice']" 형태의 리스트 문자열 → 토큰 리스트."""
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
    if not ranks:
        return "beginner"
    return LEVEL_BY_RANK[min(ranks)]


def map_goal(raw):
    votes = [GOAL_MAP[t] for t in parse_tokens(raw) if t in GOAL_MAP]
    if not votes:
        return "hypertrophy"
    return max(GOAL_PRIORITY, key=lambda g: (votes.count(g), -GOAL_PRIORITY.index(g)))


def map_muscles(exercise_names):
    groups = []
    for name in exercise_names:
        for pattern, mapped in MUSCLE_PATTERNS:
            if pattern.search(name):
                for m in mapped:
                    if m not in groups:
                        groups.append(m)
                break
    return groups


def parse_reps(raw):
    """reps 파싱. 음수는 시간 기반(초) 인코딩 → 횟수 없음(None)으로 처리."""
    try:
        reps = float(raw)
    except (TypeError, ValueError):
        return None
    if reps <= 0:
        return None
    return int(round(reps))


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def parse_dt(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").isoformat()
    except (TypeError, ValueError):
        return None


def build_routine(title, meta, week, day, rows, slug, exercise_map, stats):
    exercises = []
    for order_index, row in enumerate(rows):
        name = row["exercise_name"].strip()
        exercise_id = exercise_map.get(name.lower())
        stats["exercise_total"] += 1
        if exercise_id:
            stats["exercise_matched"] += 1
        try:
            n_sets = max(1, int(float(row["sets"])))
        except (TypeError, ValueError):
            n_sets = 1
        reps = parse_reps(row["reps"])
        exercises.append({
            "exercise_id": exercise_id,
            "exercise_name": name,
            "order_index": order_index,
            "sets": [
                {"order_index": i, "weight": None, "reps": reps}
                for i in range(n_sets)
            ],
        })

    minutes = None
    try:
        minutes = int(float(meta["time_per_workout"]))
    except (TypeError, ValueError):
        pass

    return {
        "_id": slug,
        "name": f"{title} (Week {week} Day {day})",
        "description": meta["description"].strip(),
        "exercises": exercises,
        "level": map_level(meta["level"]),
        "equipment": EQUIPMENT_MAP.get(meta["equipment"], []),
        "goal": map_goal(meta["goal"]),
        "muscle_groups": map_muscles([e["exercise_name"] for e in exercises]),
        "minutes_per_routine": minutes,
        "created_at": parse_dt(meta["created"]),
        "updated_at": parse_dt(meta["last_edit"]),
    }


def iter_programs(src_path):
    """CSV를 스트리밍하며 프로그램(title 연속 구간) 단위로 행 묶음을 낸다."""
    with open(src_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        current_title = None
        rows = []
        for row in reader:
            title = row["title"]
            if title != current_title:
                if rows:
                    yield current_title, rows
                current_title = title
                rows = []
            rows.append(row)
        if rows:
            yield current_title, rows


def convert(src_path, out_path, exercise_map, limit, per_file):
    stats = {
        "programs": 0, "sessions": 0, "deduped": 0, "written": 0,
        "exercise_total": 0, "exercise_matched": 0,
    }
    seen_slugs = set()
    out_file = None
    if per_file:
        out_path.mkdir(parents=True, exist_ok=True)
    else:
        out_file = open(out_path, "w", encoding="utf-8")

    try:
        for title, rows in iter_programs(src_path):
            stats["programs"] += 1
            sessions = {}
            for row in rows:
                key = (row["week"], row["day"])
                sessions.setdefault(key, []).append(row)

            seen_content = set()
            for (week, day), session_rows in sessions.items():
                stats["sessions"] += 1
                content_key = tuple(
                    (r["exercise_name"], r["sets"], r["reps"]) for r in session_rows
                )
                if content_key in seen_content:
                    stats["deduped"] += 1
                    continue
                seen_content.add(content_key)

                week_n = int(float(week)) if week else 0
                day_n = int(float(day)) if day else 0
                slug = f"{slugify(title)}-w{week_n}-d{day_n}"
                suffix = 2
                while slug in seen_slugs:
                    slug = f"{slugify(title)}-w{week_n}-d{day_n}-{suffix}"
                    suffix += 1
                seen_slugs.add(slug)

                doc = build_routine(
                    title, session_rows[0], week_n, day_n,
                    session_rows, slug, exercise_map, stats,
                )
                if per_file:
                    path = out_path / f"{slug}.json"
                    path.write_text(
                        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                else:
                    out_file.write(json.dumps(doc, ensure_ascii=False) + "\n")
                stats["written"] += 1
                if limit and stats["written"] >= limit:
                    return stats
    finally:
        if out_file:
            out_file.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", required=True, help="programs_detailed CSV 경로")
    parser.add_argument("--out", required=True,
                        help="출력 경로 (JSONL 파일 또는 --per-file 시 디렉토리)")
    parser.add_argument("--exercise-map", default=None,
                        help="운동명(소문자) → exercise_id(uuid) JSON 매핑 파일. "
                             "없으면 exercise_id는 전부 null")
    parser.add_argument("--limit", type=int, default=None, help="최대 루틴 수 (테스트용)")
    parser.add_argument("--per-file", action="store_true",
                        help="slug.json 개별 파일로 출력 (S3 업로드용)")
    args = parser.parse_args()

    exercise_map = {}
    if args.exercise_map:
        with open(args.exercise_map, encoding="utf-8") as f:
            exercise_map = {k.lower(): v for k, v in json.load(f).items()}

    stats = convert(
        Path(args.src).expanduser(), Path(args.out).expanduser(),
        exercise_map, args.limit, args.per_file,
    )

    matched = stats["exercise_matched"]
    total = stats["exercise_total"] or 1
    print(f"프로그램 {stats['programs']}개 / 세션 {stats['sessions']}개 읽음", file=sys.stderr)
    print(f"루틴 {stats['written']}개 출력 (중복 세션 {stats['deduped']}개 제거)", file=sys.stderr)
    print(f"exercise_id 매칭 {matched}/{stats['exercise_total']} "
          f"({matched / total:.1%})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
