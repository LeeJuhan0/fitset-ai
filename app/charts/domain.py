"""차트 집계 — I/O 없는 순수 함수. 조회는 tools/chart.py가 하고 여기선 계산만 한다.

클라이언트 API 명세 §7 chart payload(line|bar, x·series 길이 동일)로 표현 가능한
9종 metric을 만든다. 데이터 출처와 집계 책임 분담은 내부 API 명세 §4-B.1 참조 —
백엔드는 원본 행만 주고 버킷팅·합산은 전부 이 모듈이 한다.

그릴 데이터가 모자라면 payload 대신 None을 돌려준다 — 호출부가 텍스트로 강등한다.
빈 차트를 내보내지 않는 것이 §7 계약이다.
"""
from collections import defaultdict
from datetime import datetime, timedelta

from app.routines.domain import epley_e1rm

MUSCLE_LABELS_KO = {
    "back": "등",
    "biceps": "이두근",
    "calves": "종아리",
    "chest": "가슴",
    "core": "코어",
    "forearms": "전완근",
    "glutes": "둔근",
    "hamstrings": "햄스트링",
    "quadriceps": "대퇴사두근",
    "shoulders": "어깨",
    "trapezius": "승모근",
    "triceps": "삼두근",
}
WEEKDAY_LABELS_KO = ("월", "화", "수", "목", "금", "토", "일")

MIN_LINE_POINTS = 2   # 점 1개짜리 추이는 차트로 의미가 없다
MIN_BAR_POINTS = 1
E1RM_MAX_REPS = 12    # Epley 오차가 커지는 고렙 세트는 PR 계산에서 제외


def _parse_time(value: str | None) -> datetime | None:
    """ISO 8601 문자열 → datetime. Z 접미와 오프셋 표기를 모두 받는다."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _day_label(moment: datetime) -> str:
    return f"{moment.month}/{moment.day}"


def _week_start(moment: datetime) -> datetime:
    """해당 주 월요일 0시 — 주 단위 버킷 키."""
    start = moment - timedelta(days=moment.weekday())
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


def _payload(
    metric: str,
    chart_type: str,
    title: str,
    x_label: str,
    y_label: str,
    x: list[str],
    series_name: str,
    values: list[float],
) -> dict:
    """§7 chart payload 조립 — x와 values 길이는 항상 같다."""
    return {
        "chartType": chart_type,
        "metric": metric,
        "title": title,
        "xLabel": x_label,
        "yLabel": y_label,
        "x": x,
        "series": [{"name": series_name, "values": values}],
    }


def _round(value: float, digits: int = 1) -> float:
    """소수 자릿수 정리 — 그래프 축·툴팁에 긴 부동소수가 뜨지 않게."""
    rounded = round(value, digits)
    return int(rounded) if rounded == int(rounded) else rounded


def _primary_muscles(slug: str | None, meta_by_slug: dict) -> list[str]:
    """종목 slug → 주동근 소문자 목록. metadata는 Capitalized 표기라 낮춘다."""
    meta = meta_by_slug.get(slug)
    if meta is None:
        return []
    return [muscle.lower() for muscle in meta.get("primaryMuscles", [])]


def _exercise_slug(exercise: dict) -> str | None:
    # 내부 API 응답의 종목 참조 — 필드명 slug 확정이나 과도기 exerciseId도 수용
    return exercise.get("slug") or exercise.get("exerciseId")


def _weekly_series(pairs: list[tuple[datetime, float]], reducer) -> tuple[list[str], list[float]]:
    """(시각, 값) 목록을 주 단위로 묶고 reducer로 접는다 — 주 시작일 오름차순."""
    buckets: dict[datetime, list[float]] = defaultdict(list)
    for moment, value in pairs:
        buckets[_week_start(moment)].append(value)
    weeks = sorted(buckets)
    return [_day_label(week) for week in weeks], [_round(reducer(buckets[week])) for week in weeks]


def body_weight_chart(items: list[dict]) -> dict | None:
    """체중 변화 — 같은 날 복수 측정은 마지막 값만 남긴다."""
    by_day: dict[str, tuple[datetime, float]] = {}
    for item in items:
        measured_at = _parse_time(item.get("measuredAt"))
        weight = item.get("weightKg")
        if measured_at is None or weight is None:
            continue
        by_day[measured_at.date().isoformat()] = (measured_at, float(weight))
    points = [by_day[day] for day in sorted(by_day)]
    if len(points) < MIN_LINE_POINTS:
        return None
    return _payload(
        "bodyWeight", "line", "체중 변화", "날짜", "체중(kg)",
        [_day_label(moment) for moment, _ in points],
        "체중", [_round(weight) for _, weight in points],
    )


def bmi_chart(items: list[dict], height_cm: float | None) -> dict | None:
    """BMI 추이 — 체중 시계열 × 프로필 키. 키가 없으면 만들 수 없다."""
    if not height_cm:
        return None
    weight_chart = body_weight_chart(items)
    if weight_chart is None:
        return None
    height_m = float(height_cm) / 100
    values = [_round(value / (height_m * height_m)) for value in weight_chart["series"][0]["values"]]
    return _payload(
        "bmi", "line", "BMI 추이", "날짜", "BMI",
        weight_chart["x"], "BMI", values,
    )


def exercise_pr_chart(sets: list[dict], exercise_name: str) -> dict | None:
    """종목 PR 추이 — 세션별 최대 추정 1RM(Epley). 맨몸·고렙 세트는 제외한다."""
    best_by_session: dict[str, tuple[datetime, float]] = {}
    for record in sets:
        performed_at = _parse_time(record.get("performedAt"))
        weight = record.get("weight") or 0
        reps = record.get("reps") or 0
        if performed_at is None or weight <= 0 or reps <= 0 or reps > E1RM_MAX_REPS:
            continue
        e1rm = epley_e1rm(float(weight), int(reps))
        workout_id = record.get("workoutId") or performed_at.isoformat()
        previous = best_by_session.get(workout_id)
        if previous is None or e1rm > previous[1]:
            best_by_session[workout_id] = (performed_at, e1rm)
    points = sorted(best_by_session.values())
    if len(points) < MIN_LINE_POINTS:
        return None
    return _payload(
        "exercisePr", "line", f"{exercise_name} 추정 1RM 추이", "날짜", "추정 1RM(kg)",
        [_day_label(moment) for moment, _ in points],
        exercise_name, [_round(e1rm) for _, e1rm in points],
    )


def workout_duration_chart(sessions: list[dict]) -> dict | None:
    """운동 시간 변화 — 주별 평균 순수 운동 시간(분)."""
    pairs = []
    for session in sessions:
        started_at = _parse_time(session.get("startedAt"))
        seconds = session.get("activeDurationSeconds")
        if started_at is None or not seconds:
            continue
        pairs.append((started_at, int(seconds) / 60))
    if not pairs:
        return None
    x, values = _weekly_series(pairs, lambda group: sum(group) / len(group))
    if len(x) < MIN_LINE_POINTS:
        return None
    return _payload(
        "workoutDuration", "line", "주별 평균 운동 시간", "주차", "운동 시간(분)", x, "운동 시간", values
    )


def workout_frequency_chart(sessions: list[dict]) -> dict | None:
    """운동 빈도 — 주별 세션 수."""
    pairs = [
        (started_at, 1.0)
        for started_at in (_parse_time(session.get("startedAt")) for session in sessions)
        if started_at is not None
    ]
    if not pairs:
        return None
    x, values = _weekly_series(pairs, sum)
    if len(x) < MIN_BAR_POINTS:
        return None
    return _payload("workoutFrequency", "bar", "주별 운동 횟수", "주차", "횟수", x, "운동 횟수", values)


def weekday_frequency_chart(sessions: list[dict]) -> dict | None:
    """요일별 운동 횟수 — 0건인 요일도 0으로 채워 월~일 7칸을 유지한다."""
    counts = [0.0] * 7
    total = 0
    for session in sessions:
        started_at = _parse_time(session.get("startedAt"))
        if started_at is None:
            continue
        counts[started_at.weekday()] += 1
        total += 1
    if total == 0:
        return None
    return _payload(
        "weekdayFrequency", "bar", "요일별 운동 횟수", "요일", "횟수",
        list(WEEKDAY_LABELS_KO), "운동 횟수", counts,
    )


def _volume_by_session(workouts: list[dict], meta_by_slug: dict):
    """세션별 (시작 시각, 주동근, 볼륨) 평탄 목록 — 부위 집계 3종이 공유한다."""
    rows = []
    for session in workouts:
        started_at = _parse_time(session.get("startedAt"))
        if started_at is None:
            continue
        for exercise in session.get("exercises", []):
            slug = _exercise_slug(exercise)
            volume = sum(
                float(record.get("weight") or 0) * int(record.get("reps") or 0)
                for record in exercise.get("sets", [])
            )
            if volume <= 0:
                continue
            for muscle in _primary_muscles(slug, meta_by_slug):
                rows.append((started_at, muscle, volume))
    return rows


def muscle_volume_chart(workouts: list[dict], meta_by_slug: dict, muscle: str) -> dict | None:
    """부위별 볼륨 추이 — 지정 부위 하나의 주별 볼륨 합."""
    pairs = [
        (started_at, volume)
        for started_at, row_muscle, volume in _volume_by_session(workouts, meta_by_slug)
        if row_muscle == muscle
    ]
    if not pairs:
        return None
    x, values = _weekly_series(pairs, sum)
    if len(x) < MIN_LINE_POINTS:
        return None
    label = MUSCLE_LABELS_KO.get(muscle, muscle)
    return _payload(
        "muscleVolume", "line", f"{label} 볼륨 추이", "주차", "볼륨(kg)", x, label, values
    )


def muscle_balance_chart(workouts: list[dict], meta_by_slug: dict, top_n: int = 8) -> dict | None:
    """부위별 볼륨 비중 — 파이가 없어 내림차순 bar로 표현한다(§4-B.1)."""
    totals: dict[str, float] = defaultdict(float)
    for _, muscle, volume in _volume_by_session(workouts, meta_by_slug):
        totals[muscle] += volume
    if not totals:
        return None
    ranked = sorted(totals.items(), key=lambda pair: pair[1], reverse=True)[:top_n]
    return _payload(
        "muscleBalance", "bar", "부위별 볼륨 비중", "부위", "볼륨(kg)",
        [MUSCLE_LABELS_KO.get(muscle, muscle) for muscle, _ in ranked],
        "볼륨", [_round(volume) for _, volume in ranked],
    )


def top_exercises_chart(workouts: list[dict], meta_by_slug: dict, top_n: int = 8) -> dict | None:
    """많이 한 종목 순위 — 수행 세트 수 기준 내림차순."""
    counts: dict[str, int] = defaultdict(int)
    names: dict[str, str] = {}
    for session in workouts:
        for exercise in session.get("exercises", []):
            slug = _exercise_slug(exercise)
            if slug is None:
                continue
            counts[slug] += len(exercise.get("sets", []))
            names[slug] = exercise.get("exerciseName") or (
                meta_by_slug.get(slug) or {}
            ).get("name_ko") or slug
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda pair: pair[1], reverse=True)[:top_n]
    return _payload(
        "topExercises", "bar", "많이 한 운동", "종목", "세트 수",
        [names[slug] for slug, _ in ranked],
        "세트 수", [float(count) for _, count in ranked],
    )
