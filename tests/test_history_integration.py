"""NL2SQL 툴 통합 테스트 — dispatch → 템플릿 select → SQLite 실행 → 결과 표 렌더링.

유닛(test_agent_history)이 바인딩 계약을 보고, 여기서는 유저 발화에서 뽑힌 인자가
실제 조회 결과 표까지 도달하는 유즈케이스 4종(+실패 경로)을 끝까지 태운다.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.agent import tools
from app.clients import mysql
from app.exercises.domain import Equipment, Exercise
from app.workouts.domain import WorkoutHistory, WorkoutHistoryExercise, WorkoutHistorySet
from tests.conftest import seed, uid

USER_ID = "11111111-1111-1111-1111-111111111111"
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def workout_db(backend_engine, monkeypatch):
    """벤치프레스 2세션 + 스쿼트 1세션 — MYSQL_HOST가 설정된 것으로 간주."""
    monkeypatch.setattr(mysql, "is_configured", lambda: True)
    user = mysql.uuid_bytes(USER_ID)
    rows = [
        Equipment(id=uid("eq"), thumbnail_key="equipments/barbell.webp", name="바벨"),
        Exercise(id=uid("bench"), thumbnail_key="thumbnails/barbell-bench-press.webp",
                 video_key="videos/barbell-bench-press.mp4", name="바벨 벤치프레스",
                 equipment_id=uid("eq"), difficulty="INTERMEDIATE",
                 exercise_type="WEIGHT_AND_REPS", instructions=[]),
        Exercise(id=uid("squat"), thumbnail_key="thumbnails/barbell-squat.webp",
                 video_key="videos/barbell-squat.mp4", name="바벨 스쿼트",
                 equipment_id=uid("eq"), difficulty="INTERMEDIATE",
                 exercise_type="WEIGHT_AND_REPS", instructions=[]),
    ]
    plans = [("b1", "bench", 3, [(60, 10), (62.5, 8)]), ("b2", "bench", 1, [(65, 6)]),
             ("s1", "squat", 2, [(80, 10)])]
    for name, slug, day, sets in plans:
        started = NOW - timedelta(days=day)
        rows.append(WorkoutHistory(id=uid(f"w{name}"), user_id=user, started_at=started,
                            ended_at=started + timedelta(hours=1), pause_seconds=600))
        rows.append(WorkoutHistoryExercise(id=uid(f"we{name}"), workout_history_id=uid(f"w{name}"),
                                    exercise_id=uid(slug), order_index=0))
        for index, (kg, reps) in enumerate(sets):
            rows.append(WorkoutHistorySet(id=uid(f"s{name}{index}"), workout_history_exercise_id=uid(f"we{name}"),
                                   order_index=index, duration_seconds=30, rest_seconds=60,
                                   weight=kg, reps=reps))
    seed(backend_engine, rows)
    return backend_engine


async def test_exercise_sets_end_to_end(workout_db):
    # "요즘 벤치 기록 보여줘" — slug는 마스터 검증을 거쳐 바인딩되고 결과 표가 돌아온다
    text, artifact = await tools.dispatch(USER_ID, "QueryWorkoutHistory", {
        "template": "exercise_sets", "exercise": "barbell-bench-press", "days": 7,
    })
    assert artifact is None                      # text 응답 — payload 스킴 아님
    assert "바벨 벤치프레스" in text
    assert "62.5" in text and "65" in text
    assert "80" not in text                      # 스쿼트 세트가 섞이면 안 된다


async def test_exercise_pr_aggregates(workout_db):
    text, _ = await tools.dispatch(USER_ID, "QueryWorkoutHistory", {
        "template": "exercise_pr", "exercise": "barbell-bench-press",
    })
    assert "max_weight_kg" in text
    assert "65" in text                          # 최고 중량
    assert "3" in text                           # total_sets


async def test_session_summary_buckets_by_date(workout_db):
    text, _ = await tools.dispatch(USER_ID, "QueryWorkoutHistory", {
        "template": "session_summary", "days": 7,
    })
    assert "sessions" in text
    assert text.count("\n") >= 4                 # 헤더 + 컬럼행 + 날짜 3행


async def test_volume_ranks_bench_first(workout_db):
    # 볼륨 = 무게×횟수 합: 벤치 600+500+390=1490 > 스쿼트 800 — 1위는 벤치
    text, _ = await tools.dispatch(USER_ID, "QueryWorkoutHistory", {
        "template": "volume_by_exercise", "days": 7,
    })
    lines = text.splitlines()
    first_row = lines[2]                         # 헤더, 컬럼행 다음이 1위
    assert "barbell-bench-press" in first_row


async def test_window_excludes_out_of_range(workout_db):
    text, _ = await tools.dispatch(USER_ID, "QueryWorkoutHistory", {
        "template": "exercise_sets", "exercise": "barbell-bench-press", "days": 2,
    })
    assert "65" in text                          # 1일 전 세션만
    assert "62.5" not in text                    # 3일 전 세션은 창 밖


async def test_no_rows_says_no_data(workout_db):
    text, _ = await tools.dispatch(USER_ID, "QueryWorkoutHistory", {
        "template": "exercise_sets", "exercise": "barbell-squat", "days": 1,
    })
    assert "없습니다" in text
