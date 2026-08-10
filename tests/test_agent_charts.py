"""차트 집계 테스트 — §7 payload 계약(x·values 길이 동일)과 데이터 부족 시 None 강등."""
from app.charts import domain as charts

META = {
    "barbell-bench-press": {"name_ko": "바벨 벤치프레스", "primaryMuscles": ["Chest"]},
    "barbell-squat": {"name_ko": "바벨 스쿼트", "primaryMuscles": ["Quadriceps"]},
}


def _session(started_at: str, slug: str, sets: list[dict]) -> dict:
    return {
        "startedAt": started_at,
        "exercises": [{"slug": slug, "exerciseName": META[slug]["name_ko"], "sets": sets}],
    }


def _assert_payload_shape(payload: dict, metric: str, chart_type: str):
    assert payload["metric"] == metric
    assert payload["chartType"] == chart_type
    assert len(payload["series"]) == 1
    # x와 values 길이가 다르면 클라가 구멍 처리를 해야 한다 — 계약상 금지
    assert len(payload["x"]) == len(payload["series"][0]["values"])


def test_body_weight_chart_dedups_same_day_and_sorts():
    items = [
        {"measuredAt": "2026-06-01T09:00:00Z", "weightKg": 75.0},
        {"measuredAt": "2026-06-01T21:00:00Z", "weightKg": 74.5},
        {"measuredAt": "2026-05-01T09:00:00Z", "weightKg": 76.0},
    ]
    payload = charts.body_weight_chart(items)
    _assert_payload_shape(payload, "bodyWeight", "line")
    assert payload["x"] == ["5/1", "6/1"]
    assert payload["series"][0]["values"] == [76, 74.5]


def test_body_weight_chart_needs_two_points():
    assert charts.body_weight_chart([{"measuredAt": "2026-06-01T09:00:00Z", "weightKg": 75.0}]) is None
    assert charts.body_weight_chart([]) is None


def test_bmi_chart_requires_height():
    items = [
        {"measuredAt": "2026-05-01T09:00:00Z", "weightKg": 76.0},
        {"measuredAt": "2026-06-01T09:00:00Z", "weightKg": 74.0},
    ]
    assert charts.bmi_chart(items, None) is None
    payload = charts.bmi_chart(items, 175.0)
    _assert_payload_shape(payload, "bmi", "line")
    assert payload["series"][0]["values"] == [24.8, 24.2]


def test_exercise_pr_chart_takes_session_max_and_skips_high_reps():
    sets = [
        {"workoutId": "w1", "performedAt": "2026-06-01T09:00:00Z", "weight": 60, "reps": 10},
        {"workoutId": "w1", "performedAt": "2026-06-01T09:00:00Z", "weight": 70, "reps": 5},
        # 12렙 초과는 Epley 오차가 커 제외된다
        {"workoutId": "w1", "performedAt": "2026-06-01T09:00:00Z", "weight": 100, "reps": 20},
        {"workoutId": "w2", "performedAt": "2026-06-08T09:00:00Z", "weight": 75, "reps": 5},
    ]
    payload = charts.exercise_pr_chart(sets, "바벨 벤치프레스")
    _assert_payload_shape(payload, "exercisePr", "line")
    assert payload["x"] == ["6/1", "6/8"]
    assert payload["series"][0]["values"] == [81.7, 87.5]


def test_exercise_pr_chart_ignores_bodyweight_sets():
    sets = [
        {"workoutId": "w1", "performedAt": "2026-06-01T09:00:00Z", "weight": None, "reps": 10},
        {"workoutId": "w2", "performedAt": "2026-06-08T09:00:00Z", "weight": 0, "reps": 10},
    ]
    assert charts.exercise_pr_chart(sets, "푸시업") is None


def test_workout_duration_chart_averages_by_week():
    sessions = [
        {"startedAt": "2026-06-01T09:00:00Z", "activeDurationSeconds": 3600},
        {"startedAt": "2026-06-03T09:00:00Z", "activeDurationSeconds": 1800},
        {"startedAt": "2026-06-08T09:00:00Z", "activeDurationSeconds": 2400},
    ]
    payload = charts.workout_duration_chart(sessions)
    _assert_payload_shape(payload, "workoutDuration", "line")
    assert payload["x"] == ["6/1", "6/8"]
    assert payload["series"][0]["values"] == [45, 40]


def test_weekday_frequency_chart_keeps_seven_slots():
    sessions = [
        {"startedAt": "2026-06-01T09:00:00Z"},   # 월
        {"startedAt": "2026-06-08T09:00:00Z"},   # 월
        {"startedAt": "2026-06-06T09:00:00Z"},   # 토
    ]
    payload = charts.weekday_frequency_chart(sessions)
    _assert_payload_shape(payload, "weekdayFrequency", "bar")
    # 0건인 요일도 0으로 남겨 월~일 축을 유지한다
    assert payload["x"] == ["월", "화", "수", "목", "금", "토", "일"]
    assert payload["series"][0]["values"] == [2, 0, 0, 0, 0, 1, 0]


def test_muscle_balance_chart_sums_volume_by_primary_muscle():
    workouts = [
        _session("2026-06-01T09:00:00Z", "barbell-bench-press", [{"weight": 60, "reps": 10}]),
        _session("2026-06-03T09:00:00Z", "barbell-squat", [{"weight": 100, "reps": 10}]),
    ]
    payload = charts.muscle_balance_chart(workouts, META)
    _assert_payload_shape(payload, "muscleBalance", "bar")
    # 내림차순 — 파이가 없어 정렬된 bar로 비중을 표현한다
    assert payload["x"] == ["대퇴사두근", "가슴"]
    assert payload["series"][0]["values"] == [1000, 600]


def test_top_exercises_chart_counts_sets():
    workouts = [
        _session("2026-06-01T09:00:00Z", "barbell-squat", [{"weight": 100, "reps": 5}] * 4),
        _session("2026-06-02T09:00:00Z", "barbell-bench-press", [{"weight": 60, "reps": 5}] * 2),
    ]
    payload = charts.top_exercises_chart(workouts, META)
    _assert_payload_shape(payload, "topExercises", "bar")
    assert payload["x"] == ["바벨 스쿼트", "바벨 벤치프레스"]
    assert payload["series"][0]["values"] == [4, 2]


def test_muscle_volume_chart_filters_to_requested_muscle():
    workouts = [
        _session("2026-06-01T09:00:00Z", "barbell-bench-press", [{"weight": 60, "reps": 10}]),
        _session("2026-06-08T09:00:00Z", "barbell-bench-press", [{"weight": 65, "reps": 10}]),
        _session("2026-06-08T09:00:00Z", "barbell-squat", [{"weight": 100, "reps": 10}]),
    ]
    payload = charts.muscle_volume_chart(workouts, META, "chest")
    _assert_payload_shape(payload, "muscleVolume", "line")
    assert payload["series"][0]["values"] == [600, 650]
    # 기록이 없는 부위는 차트를 만들지 않는다
    assert charts.muscle_volume_chart(workouts, META, "biceps") is None
