"""차트 통합 테스트 — SQLite 시드 → charts.service → 직조회 repository → domain 집계.

metric 9종의 유즈케이스를 실제 쿼리로 태워 §7 chart payload 계약(chartType·metric·
x·series 길이 일치)까지 확인한다. 종목 메타(주동근)는 repo 동봉 metadata 실물을 쓴다.
"""
from datetime import datetime, timedelta, timezone

from app.charts import service as charts_service
from app.clients import mysql
from app.exercises.domain import Equipment, Exercise
from app.users.domain import BodyWeightLog, UserProfile
from app.workouts.domain import Workout, WorkoutExercise, WorkoutSet
from tests.conftest import seed, uid

USER_ID = "11111111-1111-1111-1111-111111111111"
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _weight(day_offset: int, kg: float) -> BodyWeightLog:
    return BodyWeightLog(id=uid(f"bw{day_offset}"), user_id=mysql.uuid_bytes(USER_ID),
                         weight_kg=kg, measured_at=NOW - timedelta(days=day_offset))


def _session(name: str, day_offset: int, sets: list[tuple[float, int]]) -> list:
    """벤치프레스 세션 1개 — (무게, 렙) 목록으로 세트를 만든다."""
    started = NOW - timedelta(days=day_offset)
    rows = [
        Workout(id=uid(f"w{name}"), user_id=mysql.uuid_bytes(USER_ID), started_at=started,
                ended_at=started + timedelta(hours=1), active_duration_seconds=3000 + day_offset),
        WorkoutExercise(id=uid(f"we{name}"), workout_id=uid(f"w{name}"),
                        exercise_id=uid("bench"), order_index=0),
    ]
    for index, (kg, reps) in enumerate(sets):
        rows.append(WorkoutSet(id=uid(f"s{name}{index}"), workout_exercise_id=uid(f"we{name}"),
                               order_index=index, duration_seconds=30, rest_seconds=60,
                               weight_kg=kg, reps=reps))
    return rows


def _seed_all(engine) -> None:
    seed(engine, [
        UserProfile(id=uid("p"), user_id=mysql.uuid_bytes(USER_ID), gender="MALE",
                    birth_date=datetime(1995, 4, 12).date(), height_cm=175.0,
                    workout_goal="HYPERTROPHY", level="INTERMEDIATE"),
        Equipment(id=uid("eq"), slug="barbell", name="바벨"),
        # slug는 실제 마스터에 있는 종목 — muscleVolume이 metadata의 주동근(chest)을 참조한다
        Exercise(id=uid("bench"), slug="barbell-bench-press", name="바벨 벤치프레스",
                 equipment_id=uid("eq"), difficulty="INTERMEDIATE",
                 exercise_type="WEIGHT_AND_REPS", instructions=[]),
        _weight(20, 74.5), _weight(10, 73.2), _weight(1, 72.4),
        *_session("a", 9, [(60, 10), (60, 10)]),
        *_session("b", 2, [(65, 8), (67.5, 6)]),
    ])


def _assert_payload_contract(payload: dict, metric: str) -> None:
    assert payload["metric"] == metric
    assert payload["chartType"] in ("line", "bar")
    assert len(payload["x"]) == len(payload["series"][0]["values"])
    assert payload["x"], metric   # 빈 차트면 None이어야지 빈 배열이면 안 된다


async def test_body_weight_line_from_db(backend_engine):
    _seed_all(backend_engine)
    payload = await charts_service.build_payload(USER_ID, "bodyWeight", days=30)
    _assert_payload_contract(payload, "bodyWeight")
    assert payload["series"][0]["values"] == [74.5, 73.2, 72.4]


async def test_bmi_uses_profile_height(backend_engine):
    _seed_all(backend_engine)
    payload = await charts_service.build_payload(USER_ID, "bmi", days=30)
    _assert_payload_contract(payload, "bmi")
    # 72.4kg / 1.75m² ≈ 23.6 — 마지막 점이 최신 체중 기준
    assert abs(payload["series"][0]["values"][-1] - 23.6) < 0.2


async def test_exercise_pr_from_sets(backend_engine):
    _seed_all(backend_engine)
    payload = await charts_service.build_payload(
        USER_ID, "exercisePr", days=30,
        exercise_slug="barbell-bench-press", exercise_label="바벨 벤치프레스",
    )
    _assert_payload_contract(payload, "exercisePr")
    # e1RM은 세션이 갈수록 상승해야 한다 (60×10 → 67.5×6)
    values = payload["series"][0]["values"]
    assert values[-1] > values[0]


async def test_session_meta_charts(backend_engine):
    _seed_all(backend_engine)
    for metric in ("workoutDuration", "workoutFrequency", "weekdayFrequency"):
        payload = await charts_service.build_payload(USER_ID, metric, days=30)
        assert payload is not None, metric
        _assert_payload_contract(payload, metric)


async def test_raw_workout_charts_with_real_metadata(backend_engine):
    _seed_all(backend_engine)
    volume = await charts_service.build_payload(USER_ID, "muscleVolume", days=30, muscle="chest")
    _assert_payload_contract(volume, "muscleVolume")
    balance = await charts_service.build_payload(USER_ID, "muscleBalance", days=30)
    _assert_payload_contract(balance, "muscleBalance")
    top = await charts_service.build_payload(USER_ID, "topExercises", days=30)
    _assert_payload_contract(top, "topExercises")
    assert "바벨 벤치프레스" in top["x"][0]


async def test_insufficient_data_degrades_to_none(backend_engine):
    # 체중 1점 — 추이 최소 점수(2) 미달이면 차트 대신 None (툴이 텍스트로 강등)
    seed(backend_engine, [
        UserProfile(id=uid("p"), user_id=mysql.uuid_bytes(USER_ID), gender="MALE",
                    birth_date=datetime(1995, 4, 12).date(), height_cm=175.0,
                    workout_goal="HYPERTROPHY", level="INTERMEDIATE"),
        _weight(1, 72.4),
    ])
    assert await charts_service.build_payload(USER_ID, "bodyWeight", days=30) is None
