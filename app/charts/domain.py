from collections import defaultdict
from datetime import datetime, timedelta

from app.routines.utils import epley_e1rm

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

MIN_LINE_POINTS = 2
MIN_BAR_POINTS = 1
E1RM_MAX_REPS = 12


def _parse_time(value: str | None) -> datetime | None:
    """ISO 8601 문자열을 datetime 객체로 변환"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _day_label(moment: datetime) -> str:
    """시각을 월 슬래시 일 축 라벨로 변환"""
    return f"{moment.month}/{moment.day}"


def _week_start(moment: datetime) -> datetime:
    """해당 주 월요일 0시 주 단위 버킷 키"""
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
    """차트 페이로드 축 라벨과 시리즈 값 조립"""
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
    """그래프 표시용 소수 자릿수 반올림"""
    rounded = round(value, digits)
    return int(rounded) if rounded == int(rounded) else rounded


def _primary_muscles(slug: str | None, meta_by_slug: dict) -> list[str]:
    """종목 slug 기준 주동근 소문자 목록 조회"""
    meta = meta_by_slug.get(slug)
    if meta is None:
        return []
    return [muscle.lower() for muscle in meta.get("primaryMuscles", [])]


def _exercise_slug(exercise: dict) -> str | None:
    """기록 항목에서 종목 slug 또는 exerciseId 추출"""
    return exercise.get("slug") or exercise.get("exerciseId")


def _weekly_series(pairs: list[tuple[datetime, float]], reducer) -> tuple[list[str], list[float]]:
    """시각과 값 목록을 주 단위 집계 시리즈로 변환"""
    buckets: dict[datetime, list[float]] = defaultdict(list)
    for moment, value in pairs:
        buckets[_week_start(moment)].append(value)
    weeks = sorted(buckets)
    return [_day_label(week) for week in weeks], [_round(reducer(buckets[week])) for week in weeks]


def body_weight_chart(items: list[dict]) -> dict | None:
    """체중 변화 차트 같은 날은 마지막 측정값 사용"""
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
    """체중 시계열과 프로필 키 기반 BMI 추이 차트"""
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
    """종목 세션별 최대 추정 1RM 추이 차트"""
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
    """주별 평균 순수 운동 시간 분 단위 차트"""
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
    """주별 운동 세션 수 빈도 차트"""
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
    """요일별 운동 횟수 월부터 일까지 7칸 차트"""
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
    """세션별 시작 시각 주동근 볼륨 평탄 목록 생성"""
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
    """지정 부위 주별 볼륨 합계 추이 차트"""
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
    """부위별 볼륨 비중 내림차순 막대 차트"""
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
    """수행 세트 수 기준 상위 종목 순위 차트"""
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
