import pytest


def _sets(weight: float | None, reps: int | None, count: int) -> list[dict]:
    """같은 무게와 렙 세트 여러 개 생성"""
    return [{"orderIndex": index, "weight": weight, "reps": reps} for index in range(count)]


@pytest.fixture
def exercise_meta() -> dict:
    """벤치프레스 스쿼트 플랭크 종목 카탈로그"""
    return {
        "bench-press": {"slug": "bench-press", "name_ko": "벤치프레스", "primaryMuscles": ["Chest", "Triceps"]},
        "barbell-squat": {"slug": "barbell-squat", "name_ko": "바벨 스쿼트", "primaryMuscles": ["Quadriceps", "Glutes"]},
        "plank": {"slug": "plank", "name_ko": "플랭크", "primaryMuscles": ["Core"]},
    }


@pytest.fixture
def body_weights() -> list[dict]:
    """같은 날 두 번 잰 체중 포함 이틀치 측정값"""
    return [
        {"measuredAt": "2026-09-15T08:00:00Z", "weightKg": 70.0},
        {"measuredAt": "2026-09-15T20:00:00Z", "weightKg": 70.4},
        {"measuredAt": "2026-09-20T08:00:00Z", "weightKg": 69.84},
    ]


@pytest.fixture
def bench_sets() -> list[dict]:
    """두 세션 벤치프레스 세트 12렙 초과 세트 포함"""
    return [
        {"workoutId": "w1", "performedAt": "2026-09-15T09:00:00Z", "orderIndex": 0, "weight": 60.0, "reps": 10},
        {"workoutId": "w1", "performedAt": "2026-09-15T09:00:00Z", "orderIndex": 1, "weight": 80.0, "reps": 5},
        {"workoutId": "w2", "performedAt": "2026-09-22T09:00:00Z", "orderIndex": 0, "weight": 85.0, "reps": 5},
        {"workoutId": "w2", "performedAt": "2026-09-22T09:00:00Z", "orderIndex": 1, "weight": 100.0, "reps": 15},
    ]


@pytest.fixture
def workout_sessions() -> list[dict]:
    """두 주에 걸친 월 수 월 세션 세 개"""
    return [
        {"id": "w1", "startedAt": "2026-09-14T09:00:00Z", "endedAt": "2026-09-14T10:00:00Z", "activeDurationSeconds": 3600},
        {"id": "w2", "startedAt": "2026-09-16T09:00:00Z", "endedAt": "2026-09-16T09:30:00Z", "activeDurationSeconds": 1800},
        {"id": "w3", "startedAt": "2026-09-21T09:00:00Z", "endedAt": "2026-09-21T09:50:00Z", "activeDurationSeconds": 3000},
    ]


@pytest.fixture
def recent_workouts() -> list[dict]:
    """두 주 벤치 스쿼트 플랭크 종목별 세트 기록"""
    return [
        {
            "id": "w1",
            "startedAt": "2026-09-15T09:00:00Z",
            "endedAt": "2026-09-15T10:00:00Z",
            "exercises": [
                {"slug": "bench-press", "exerciseName": "벤치프레스", "sets": _sets(60.0, 10, 1) + _sets(80.0, 5, 1)},
                {"slug": "barbell-squat", "exerciseName": "바벨 스쿼트", "sets": _sets(100.0, 5, 3)},
            ],
        },
        {
            "id": "w2",
            "startedAt": "2026-09-22T09:00:00Z",
            "endedAt": "2026-09-22T10:00:00Z",
            "exercises": [
                {"slug": "bench-press", "exerciseName": "벤치프레스", "sets": _sets(70.0, 10, 2)},
                {"slug": "plank", "exerciseName": "플랭크", "sets": _sets(None, None, 2)},
            ],
        },
    ]
