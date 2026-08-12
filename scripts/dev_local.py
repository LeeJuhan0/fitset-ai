"""로컬 실험 하네스 — 외부 의존을 가짜로 바꾸고 채팅 UI를 띄운다.

대체되는 것: DynamoDB(chat·routines) → 인메모리, 스프링 내부 API → 가짜 유저 기록,
JWT 인증 → 고정 유저. LLM(Bedrock Nova Lite)만 실제 호출한다.

LangSmith 트레이싱(선택): LANGSMITH_API_KEY가 있으면 자동으로 켜져 그래프·LLM 호출이
smith.langchain.com 프로젝트 fitset-dev-local에 기록된다. 트레이스에 대화 내용이 그대로
올라가므로 로컬 가짜 데이터 전용 — 프로덕션 태스크에는 이 키를 넣지 말 것.
--fake-llm 모드는 LangChain을 거치지 않아 트레이스가 남지 않는다.

사용:
    uv run python scripts/dev_local.py               # 실제 Bedrock 호출 (AWS 자격증명 필요)
    uv run python scripts/dev_local.py --fake-llm    # 자격증명 없이 UI·흐름만 확인
    LANGSMITH_API_KEY=<키> uv run python scripts/dev_local.py   # + LangSmith 트레이싱

브라우저: http://localhost:8000
"""
import argparse
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 실험 편의 — 레이트리밋 완화 (설정이 처음 읽히기 전에 넣어야 한다)
os.environ.setdefault("CHAT_MESSAGES_PER_MINUTE", "1000")
os.environ.setdefault("ROUTINE_GENERATIONS_PER_MINUTE", "100")

import numpy as np
import uvicorn
from fastapi.responses import FileResponse

from app import deps
from app.chat import repository
from app.chat.domain import ThreadRecord
from app.exercises import repository as exercise_repository
from app.users import repository as users_repository
from app.workouts import repository as workouts_repository
from app.core import llm
from app.core.clock import iso_utc
from app.main import app
from app.exercises.repository import get_exercise_meta
from app.routines.repository import get_routine_store

USER_ID = "11111111-1111-1111-1111-111111111111"
NOW = datetime.now(timezone.utc)


# ── 채팅 저장소 (인메모리) ─────────────────────────────────────────────

class InMemoryChatStore:
    """repository 모듈과 같은 시그니처의 동기 함수 묶음 — DynamoDB 대체."""

    def __init__(self):
        self.threads: dict[tuple[str, str], dict] = {}
        self.messages: dict[str, list[dict]] = {}
        self.user_summaries: dict[str, str] = {}

    def _sorted(self, thread_id):
        return [dict(m) for m in sorted(self.messages.get(thread_id, []), key=lambda m: m["message_id"])]

    def list_threads(self, user_id):
        return [t.model_copy() for (uid, _), t in self.threads.items() if uid == user_id]

    def get_thread(self, user_id, thread_id):
        thread = self.threads.get((user_id, thread_id))
        return thread.model_copy() if thread else None

    def put_thread(self, record):
        self.threads[(record.user_id, record.thread_id)] = record.model_copy()

    def delete_thread(self, user_id, thread_id):
        self.threads.pop((user_id, thread_id), None)

    def touch_thread(self, user_id, thread_id, last_message_at, expires_at, title=None):
        thread = self.threads.get((user_id, thread_id))
        if thread is None:
            return
        thread.last_message_at = last_message_at
        thread.expires_at = expires_at
        if title is not None and thread.title is None:
            thread.title = title

    def save_summary(self, user_id, thread_id, summary_text, summary_upto):
        thread = self.threads.get((user_id, thread_id))
        if thread is None or (thread.summary_upto or "") >= summary_upto:
            return
        thread.summary_text = summary_text
        thread.summary_upto = summary_upto

    def messages_page(self, thread_id, limit, cursor=None):
        messages = self._sorted(thread_id)
        if cursor is not None:
            messages = [m for m in messages if m["message_id"] < cursor]
        page = messages[-limit:]
        next_cursor = page[0]["message_id"] if len(messages) > limit else None
        return page, next_cursor

    def recent_messages(self, thread_id, limit):
        return self._sorted(thread_id)[-limit:]

    def messages_after(self, thread_id, summary_upto):
        messages = self._sorted(thread_id)
        if summary_upto is None:
            return messages
        return [m for m in messages if m["message_id"] > summary_upto]

    def count_messages(self, thread_id, after):
        return len(self.messages_after(thread_id, after))

    def put_message(self, item):
        self.messages.setdefault(item["thread_id"], []).append(dict(item))

    def delete_messages(self, thread_id):
        self.messages.pop(thread_id, None)

    def get_user_summary(self, user_id):
        return self.user_summaries.get(user_id)

    def save_user_summary(self, user_id, summary_text, updated_at):
        self.user_summaries[user_id] = summary_text


# ── 가짜 유저 기록 (스프링 내부 API 대체) ──────────────────────────────

FAV_EXERCISES = ["barbell-bench-press", "barbell-squat", "dumbbell-curl", "pull-ups", "push-up"]
BASE_WEIGHT = {"barbell-bench-press": 60, "barbell-squat": 80, "dumbbell-curl": 14}


def _fake_workouts(days=28):
    """주 3회꼴 세션, 종목별 무게가 완만히 상승하는 기록."""
    sessions = []
    rng = random.Random(42)
    for i in range(days, 0, -1):
        if i % 2 == 0 or i % 7 == 0:
            continue
        started = NOW - timedelta(days=i, hours=rng.randint(0, 3))
        exercises = []
        for order, slug in enumerate(rng.sample(FAV_EXERCISES, 3)):
            base = BASE_WEIGHT.get(slug)
            progress = (days - i) * 0.15
            sets = [
                {
                    "id": f"s-{i}-{order}-{n}",
                    "orderIndex": n,
                    "weight": round(base + progress, 1) if base else None,
                    "reps": rng.choice([8, 10, 12]),
                    "durationSec": rng.randint(25, 45),
                    "restSec": 90 if n < 3 else None,
                }
                for n in range(4)
            ]
            meta = get_exercise_meta().get(slug, {})
            exercises.append({
                "id": f"e-{i}-{order}",
                "slug": slug,
                "exerciseName": meta.get("name_ko", slug),
                "orderIndex": order,
                "sets": sets,
            })
        sessions.append({
            "id": f"w-{i}",
            "startedAt": iso_utc(started),
            "endedAt": iso_utc(started + timedelta(minutes=55)),
            "exercises": exercises,
        })
    return sessions


FAKE_WORKOUTS = _fake_workouts()


def _install_fake_backend():
    """DB 직조회 함수들을 가짜 데이터로 대체한다 — 로컬은 MySQL 없이 돈다."""
    def get_profile(user_id):
        return {
            "heightCm": 175.0, "weightKg": 72.4, "gender": "MALE",
            "birthDate": "1998-04-12", "goal": "hypertrophy",
            "level": "intermediate", "avoidBodyParts": [],
        }

    def get_recent_workouts(user_id, days):
        return FAKE_WORKOUTS

    def get_exercise(slug):
        meta = get_exercise_meta().get(slug) or {}
        return {
            "slug": slug,
            "name": meta.get("name_ko", slug),
            "primaryMuscles": [m.lower() for m in meta.get("primaryMuscles", [])],
            "secondaryMuscles": [m.lower() for m in meta.get("secondaryMuscles", [])],
            "equipment": (meta.get("equipment") or ["unknown"])[0],
            "difficulty": meta.get("difficulty", "beginner"),
            "thumbnailUrl": f"https://cdn.local/exercises/{slug}/thumb.jpg",
            "videoUrl": f"https://cdn.local/exercises/{slug}/guide.mp4",
            "instructions": [
                "시작 자세를 잡고 코어에 힘을 준다.",
                "동작 구간 전체를 통제하며 내린다.",
                "반동 없이 시작 자세로 밀어 올린다.",
            ],
        }

    def get_body_weights(user_id, days):
        points = []
        for week in range(24, -1, -1):
            moment = NOW - timedelta(weeks=week)
            weight = 76.0 - (24 - week) * 0.15 + random.Random(week).uniform(-0.3, 0.3)
            points.append({"measuredAt": iso_utc(moment), "weightKg": round(weight, 1)})
        return points

    def get_exercise_sets(user_id, slug, days):
        rows = []
        for session in FAKE_WORKOUTS:
            for exercise in session["exercises"]:
                if exercise["slug"] != slug:
                    continue
                for record in exercise["sets"]:
                    rows.append({
                        "workoutId": session["id"],
                        "performedAt": session["startedAt"],
                        "orderIndex": record["orderIndex"],
                        "weight": record["weight"],
                        "reps": record["reps"],
                        "durationSec": record["durationSec"],
                        "restSec": record["restSec"],
                    })
        return rows

    def get_workout_sessions(user_id, days):
        return [
            {
                "id": session["id"],
                "routineId": None,
                "startedAt": session["startedAt"],
                "endedAt": session["endedAt"],
                "activeDurationSeconds": random.Random(session["id"]).randint(2400, 4200),
                "exerciseCount": len(session["exercises"]),
                "setCount": sum(len(e["sets"]) for e in session["exercises"]),
            }
            for session in FAKE_WORKOUTS
        ]

    users_repository.get_profile = get_profile
    users_repository.get_body_weights = get_body_weights
    workouts_repository.get_recent_workouts = get_recent_workouts
    workouts_repository.get_exercise_sets = get_exercise_sets
    workouts_repository.get_workout_sessions = get_workout_sessions
    exercise_repository.get_exercise = get_exercise
    # 카탈로그 캐시가 로컬에 없다 — 영상 URL도 가짜 CDN으로
    exercise_repository.cdn_video_url = lambda slug: f"https://cdn.local/exercises/{slug}/guide.mp4"


# ── 가짜 루틴 스토어 ──────────────────────────────────────────────────

LIGHT_ROUTINES = [
    {"slug": "chest-push-45", "level": "beginner", "goal": "hypertrophy",
     "muscle_groups": ["chest", "triceps", "shoulders"], "equipment": ["barbell", "dumbbell"],
     "minutes_per_routine": 45, "exercise_names": ["바벨 벤치프레스", "덤벨 인클라인 컬", "푸시업"]},
    {"slug": "leg-day-60", "level": "intermediate", "goal": "strength",
     "muscle_groups": ["quadriceps", "glutes", "hamstrings"], "equipment": ["barbell"],
     "minutes_per_routine": 60, "exercise_names": ["바벨 스쿼트", "포워드 런지", "굿모닝"]},
    {"slug": "back-pull-50", "level": "intermediate", "goal": "hypertrophy",
     "muscle_groups": ["back", "biceps"], "equipment": ["bodyweight", "dumbbell"],
     "minutes_per_routine": 50, "exercise_names": ["풀업", "덤벨 로우 한 팔", "친업"]},
    {"slug": "home-full-30", "level": "beginner", "goal": "endurance",
     "muscle_groups": ["chest", "core", "quadriceps"], "equipment": ["bodyweight"],
     "minutes_per_routine": 30, "exercise_names": ["푸시업", "맨몸 스쿼트", "마운틴 클라이머"]},
]

ROUTINE_EXERCISES = {
    "chest-push-45": [("barbell-bench-press", "바벨 벤치프레스"), ("push-up", "푸시업"),
                      ("bench-dips", "벤치 딥스")],
    "leg-day-60": [("barbell-squat", "바벨 스쿼트"), ("forward-lunge", "포워드 런지"),
                   ("good-mornings", "굿모닝")],
    "back-pull-50": [("pull-ups", "풀업"), ("chin-ups", "친업"), ("inverted-row", "인버티드 로우")],
    "home-full-30": [("push-up", "푸시업"), ("bodyweight-squat", "맨몸 스쿼트"),
                     ("mountain-climber", "마운틴 클라이머")],
}

FULL_ROUTINES = {
    light["slug"]: {
        "slug": light["slug"],
        "name": " ".join(light["exercise_names"][:2]) + " 루틴",
        "minutes_per_routine": light["minutes_per_routine"],
        "exercises": [
            {
                "slug": slug, "exercise_name": name, "order_index": order,
                "thumbnail_url": f"https://cdn.local/exercises/{slug}/thumb.jpg",
                "sets": [{"order_index": n, "reps": 10} for n in range(4)],
            }
            for order, (slug, name) in enumerate(ROUTINE_EXERCISES[light["slug"]])
        ],
    }
    for light in LIGHT_ROUTINES
}


def _install_fake_routine_store():
    store = get_routine_store()
    store.routines = [dict(r) for r in LIGHT_ROUTINES]
    store.vectors = np.zeros((len(LIGHT_ROUTINES), 1024), dtype=np.float32)
    store.ready = True
    store.load = lambda: None   # lifespan의 실제 Scan 차단
    store.get_full = lambda slug: FULL_ROUTINES.get(slug)


# ── LLM 대체 (선택) ───────────────────────────────────────────────────

def _install_fake_llm():
    """--fake-llm — AWS 자격증명 없이 UI·payload 렌더링을 확인하기 위한 캔드 응답."""
    from app.agent import graph

    async def fake_run_turn(user_id, system_prompt, history):
        content = getattr(history[-1], "content", "") or ""
        if "차트" in content or "체중" in content or "변화" in content:
            payload = {
                "chartType": "line", "metric": "bodyWeight", "title": "체중 변화",
                "xLabel": "날짜", "yLabel": "체중(kg)",
                "x": ["5/1", "5/15", "6/1", "6/15", "7/1", "7/15"],
                "series": [{"name": "체중", "values": [76.0, 75.2, 74.5, 73.8, 73.0, 72.4]}],
            }
            return graph.AgentResult("최근 두 달간 3.6kg 감량 중이에요. 좋은 추세예요!", "chart", payload)
        if "루틴" in content:
            payload = {
                "slug": "chest-push-45", "name": "가슴 집중 45분", "estimatedMinutes": 45,
                "exercises": [
                    {"slug": "barbell-bench-press", "exerciseName": "바벨 벤치프레스",
                     "thumbnailUrl": None, "orderIndex": 0,
                     "sets": [{"orderIndex": n, "weight": 60.0, "reps": 10} for n in range(4)]},
                    {"slug": "push-up", "exerciseName": "푸시업", "thumbnailUrl": None,
                     "orderIndex": 1,
                     "sets": [{"orderIndex": n, "weight": None, "reps": 15} for n in range(3)]},
                ],
            }
            return graph.AgentResult("최근 기록 기준으로 가슴 위주 루틴을 짜봤어요.", "routine", payload)
        return graph.AgentResult(f"(fake-llm) '{content[:40]}' 잘 받았어요.", "text", None)

    async def fake_run_turn_stream(user_id, system_prompt, history):
        # 캔드 응답을 8자씩 쪼개 delta로 — SSE 렌더링을 자격증명 없이 확인한다
        result = await fake_run_turn(user_id, system_prompt, history)
        for start in range(0, len(result.content), 8):
            yield "delta", result.content[start:start + 8]
        yield "result", result

    graph.run_turn = fake_run_turn
    graph.run_turn_stream = fake_run_turn_stream


# ── 조립 ──────────────────────────────────────────────────────────────

def patch_everything(fake_llm: bool):
    chat_store = InMemoryChatStore()
    for name in (
        "list_threads", "get_thread", "put_thread", "delete_thread", "touch_thread",
        "save_summary", "messages_page", "recent_messages", "messages_after",
        "count_messages", "put_message", "delete_messages",
        "get_user_summary", "save_user_summary",
    ):
        setattr(repository, name, getattr(chat_store, name))

    _install_fake_backend()
    _install_fake_routine_store()

    # 임베딩은 Cohere 모델 액세스 의존을 끊는다 — 로컬 랭킹 품질은 실험 대상 아님
    rng = np.random.default_rng(7)
    llm.embed_query = lambda text: rng.standard_normal(1024).astype(np.float32).tolist()

    app.dependency_overrides[deps.get_current_user_id] = lambda: USER_ID

    if fake_llm:
        _install_fake_llm()


HTML_PATH = Path(__file__).parent / "dev_chat.html"


@app.get("/", include_in_schema=False)
async def chat_page():
    return FileResponse(HTML_PATH)


def enable_langsmith() -> bool:
    """LANGSMITH_API_KEY가 있으면 LangSmith 트레이싱 환경변수를 채운다. 켜졌으면 True."""
    if not os.environ.get("LANGSMITH_API_KEY"):
        return False
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")   # 구 env 이름만 읽는 버전 대비
    os.environ.setdefault("LANGSMITH_PROJECT", "fitset-dev-local")
    os.environ.setdefault("LANGCHAIN_PROJECT", os.environ["LANGSMITH_PROJECT"])
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fake-llm", action="store_true", help="Bedrock 없이 캔드 응답으로 UI 확인")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    patch_everything(args.fake_llm)
    mode = "FAKE LLM" if args.fake_llm else "실제 Bedrock (Nova Lite)"
    print(f"\n▶ FitSet 로컬 챗봇 실험 — LLM: {mode}")
    if enable_langsmith():
        if args.fake_llm:
            print("▶ LangSmith: 키는 있지만 --fake-llm은 LangChain을 안 거쳐 트레이스가 안 남음")
        else:
            print(f"▶ LangSmith 트레이싱 켜짐 — smith.langchain.com 프로젝트 {os.environ['LANGSMITH_PROJECT']}")
    else:
        print("▶ LangSmith 꺼짐 — 켜려면 LANGSMITH_API_KEY 환경변수 설정")
    print(f"▶ http://localhost:{args.port}\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
