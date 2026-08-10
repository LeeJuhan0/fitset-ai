import asyncio
import logging
import random
import re
import time

import numpy as np

from app.clients.spring import get_spring_client
from app.core import llm, ratelimit
from app.core.config import get_settings
from app.core.errors import (
    AiUnavailableError,
    NoRoutineCandidateError,
    RateLimitedError,
    UnsafeConstraintError,
)
from app.exercises import repository as exercise_catalog
from app.routines import domain, prompts
from app.exercises.repository import get_exercise_meta
from app.routines.repository import get_routine_store
from app.routines.schemas import (
    RoutineExerciseOut,
    RoutineGenerateRequest,
    RoutineOut,
    RoutineSetOut,
)

logger = logging.getLogger("fitset")


async def generate_routine(user_id: str, request: RoutineGenerateRequest) -> RoutineOut:
    # LLM 3회 + 임베딩 1회짜리 워크로드 — abuse는 진입 즉시 끊는다 (감사 F17)
    if not ratelimit.routine_limiter().allow(user_id):
        raise RateLimitedError()
    settings = get_settings()
    store = get_routine_store()
    if not store.ready:
        raise AiUnavailableError("루틴 데이터를 로딩 중입니다. 잠시 후 다시 시도해주세요.")

    # ② goal이 프로필로 이동(2026-07-29, 명세 §1)해 쿼리 변환이 프로필에 의존한다 —
    # 프로필을 먼저 받고, 기록 조회와 변환 LLM만 병렬로 돌린다
    # (직렬화 비용 = 내부망 프로필 1회분. 변환 LLM ~1초와의 병렬성은 유지)
    profile = await get_spring_client().get_profile(user_id)
    goal = profile.get("goal") or domain.DEFAULT_GOAL
    workouts, analysis = await asyncio.gather(
        get_spring_client().get_recent_workouts(user_id, settings.workout_days),
        _analyze_query(request, goal),
    )
    query_description, parsed_avoided, unsafe_constraints = analysis

    # 가드레일 — 룰 필터로 표현할 수 없는 안전 제약은 추천하지 않는다.
    # 임베딩만으로는 반영이 보장되지 않아(docs/쿼리 변환 LLM AB 실험.md 예시 4) 위험한 루틴이 나갈 수 있다
    if unsafe_constraints and settings.guardrail_block_unsafe:
        logger.warning("guardrail blocked recommendation: %s", unsafe_constraints)
        raise UnsafeConstraintError()

    # ③ 기록 통계 — e1RM(무게 추천)·맨몸 비율(홈트 판정)
    meta = get_exercise_meta()
    stats = domain.build_e1rm_stats(workouts, meta)
    ratio = domain.bodyweight_ratio(workouts, meta)
    home_only = ratio is not None and ratio >= settings.bodyweight_home_ratio

    # ④·⑤ 조건 결합 + 룰 필터 통과 전체를 후보로
    # 기피 부위 = 프로필 조회값 ∪ context에서 파싱한 통증·부상 부위 (양쪽 모두 요청 부위가 우선)
    avoided = domain.effective_avoided(profile.get("avoidBodyParts"), request.muscle_groups)
    parsed = domain.effective_avoided(sorted(parsed_avoided), request.muscle_groups)
    if parsed:
        # LLM 오파싱 여부를 사후에 확인할 수 있도록 적용된 기피를 남긴다
        logger.info("context-parsed avoid applied: %s", sorted(parsed))
    candidates = _filter_candidates(store, request, avoided | parsed, home_only, settings.minutes_tolerance)

    # 파싱한 기피만으로 후보가 비면 그것만 풀고 재시도한다 — 프로필 기피는 유지
    if not candidates and parsed:
        logger.info("parsed avoid %s emptied candidates, retrying without it", sorted(parsed))
        candidates = _filter_candidates(store, request, avoided, home_only, settings.minutes_tolerance)
    if not candidates:
        raise NoRoutineCandidateError()

    # ⑥ 쿼리 임베딩 → 코사인 전량 → 탑30 → 랜덤 5 → LLM 일등 선택
    try:
        query_vector = await asyncio.to_thread(llm.embed_query, query_description)
    except Exception as exc:
        # 폴백이 없는 유일한 구간 — 503으로 나가므로 원인을 남긴다
        logger.exception("query embedding failed")
        raise AiUnavailableError() from exc
    query = np.asarray(query_vector, dtype=np.float32)
    query /= np.linalg.norm(query) or 1.0
    # 후보 행렬 복사를 피하려고 전체 행렬곱 후 후보만 취한다
    similarities = (store.vectors @ query)[candidates]
    ranked = [candidates[i] for i in np.argsort(-similarities)]
    top = ranked[: settings.cosine_top_k]
    sampled = random.sample(top, min(settings.llm_candidate_count, len(top)))
    chosen = await _pick_with_llm(sampled, store, query_description)
    chosen_slug = store.routines[chosen if chosen is not None else top[0]]["slug"]

    # ⑦ 최종 선택 루틴만 전체 조회(GetItem — 인메모리는 라이트 필드뿐) 후 응답 변환
    routine = await asyncio.to_thread(store.get_full, chosen_slug)
    if routine is None:
        raise AiUnavailableError("선택된 루틴을 불러오지 못했습니다.")
    return _build_response(routine, request, profile, stats, meta)


def _filter_candidates(
    store,
    request: RoutineGenerateRequest,
    avoided: set[str],
    home_only: bool,
    tolerance: float,
) -> list[int]:
    """룰 필터를 통과한 루틴 인덱스.

    순수 파이썬 O(N) 루프가 이벤트 루프에서 돈다 — 적재 N이 커지면 요청 전체가 이 구간에
    정지한다(감사 F11). 아래 측정 로그로 실측치를 확인한 뒤 벡터화 여부를 결정한다.
    """
    started = time.perf_counter()
    matched = [
        index
        for index, routine in enumerate(store.routines)
        if domain.passes_filters(
            routine,
            muscle_groups=request.muscle_groups,
            avoided=avoided,
            level=request.level,
            minutes=request.minutes,
            tolerance=tolerance,
            home_only=home_only,
        )
    ]
    logger.info(
        "rule filter: %d/%d passed in %.1fms",
        len(matched), len(store.routines), (time.perf_counter() - started) * 1000,
    )
    return matched


async def _analyze_query(request: RoutineGenerateRequest, goal: str) -> tuple[str, set[str], list[str]]:
    """쿼리 → (묘사문, 기피 부위, 안전 제약) LLM 분석. 실패·파싱 불가 시 템플릿 묘사문만 반환.

    goal은 요청이 아니라 프로필 조회값이다 — 호출부가 기본값 처리 후 넘긴다.
    """
    system, user = prompts.describe_query(
        goal, request.level, request.muscle_groups, request.minutes, request.context,
    )
    fallback = prompts.fallback_description(
        goal, request.level, request.muscle_groups, request.minutes,
    )
    try:
        raw = await asyncio.to_thread(llm.complete, system, user, 300)
    except Exception:
        # 폴백이 응답을 살리므로 클라는 성공으로 본다 — 강등 사실은 로그로만 드러난다
        logger.warning("describe LLM failed, falling back to template", exc_info=True)
        return fallback, set(), []
    description, avoided, unsafe = domain.parse_query_analysis(raw)
    if description is None:
        logger.warning("describe LLM returned unparsable output (%r), falling back to template", raw[:200])
        return fallback, set(), []
    return description, avoided, unsafe


async def _pick_with_llm(sampled: list[int], store, query_description: str) -> int | None:
    """탑5 중 일등 선택 (LLM). 실패·범위 밖 응답이면 None → 호출부가 코사인 1위 폴백."""
    candidates = [store.routines[i] for i in sampled]
    system, user = prompts.pick_best(candidates, query_description)
    try:
        answer = await asyncio.to_thread(llm.complete, system, user, 10)
    except Exception:
        logger.warning("pick LLM failed, falling back to cosine top-1", exc_info=True)
        return None
    match = re.search(r"\d+", answer)
    if match is None:
        logger.warning("pick LLM returned no number (%r), falling back to cosine top-1", answer)
        return None
    number = int(match.group())
    if not 1 <= number <= len(sampled):
        logger.warning("pick LLM chose out-of-range %d, falling back to cosine top-1", number)
        return None
    return sampled[number - 1]


def _build_set(
    slug: str,
    set_template: dict,
    kind: str | None,
    level: str,
    stats: tuple[dict, dict],
    profile: dict,
    meta: dict,
) -> RoutineSetOut:
    """세트 1개 조립 — 종목 수행 방식(exerciseType)에 따라 채우는 필드가 갈린다.

    루틴 원본은 전부 렙 기반이라 시간 종목이어도 reps가 들어있다. 그 값을 초로 오해해
    내보내지 않고, 설정된 세트 길이(duration_set_seconds)로 대체한다.

    세 필드는 not null이고 해당 없는 자리는 0으로 채운다 — 클라가 null 분기를 하지 않도록.
    """
    order_index = set_template["order_index"]
    reps = set_template["reps"]

    if kind == "DURATION":
        return RoutineSetOut(
            order_index=order_index,
            weight=0,
            reps=0,
            # 종목 특성 × 요청 난이도 — 원본 렙 값은 초가 아니라 버린다
            duration_seconds=domain.set_duration_seconds(
                slug, level, get_settings().duration_set_seconds
            ),
        )
    if kind == "REPS_ONLY":
        return RoutineSetOut(order_index=order_index, weight=0, reps=reps, duration_seconds=0)

    # 맨몸 종목은 추천 무게가 없다(None) — 0으로 채운다
    weight = domain.recommend_weight(slug, reps, stats, profile, meta) or 0
    return RoutineSetOut(order_index=order_index, weight=weight, reps=reps, duration_seconds=0)


def _build_response(
    routine: dict,
    request: RoutineGenerateRequest,
    profile: dict,
    stats: tuple[dict, dict],
    meta: dict,
) -> RoutineOut:
    settings = get_settings()
    exercises = []
    for exercise in routine.get("exercises", []):
        slug = exercise["slug"]
        equipment = (meta.get(slug) or {}).get("equipment", [])
        # 카탈로그 미스면 None — 종전대로 무게·렙 세트를 만든다
        kind = exercise_catalog.exercise_type(slug)
        sets = [
            _build_set(slug, set_template, kind, request.level, stats, profile, meta)
            for set_template in exercise.get("sets", [])
        ]
        if request.include_warmup and sets:
            # 워밍업은 부하를 줄이는 것 — 시간 종목은 시간을, 나머지는 무게를 낮춘다
            if sets[0].duration_seconds:
                sets[0].duration_seconds = max(
                    1, int(sets[0].duration_seconds * settings.warmup_duration_factor)
                )
            else:
                sets[0].weight = domain.warmup_weight(sets[0].weight, equipment) or 0
        exercises.append(
            RoutineExerciseOut(
                exercise_id=exercise_catalog.exercise_id(slug),
                slug=slug,
                exercise_name=exercise["exercise_name"],
                # 적재값은 비공개 S3 직접 주소라 클라에서 403이 난다(2026-08-05 실측) — CDN 우선
                thumbnail_url=exercise_catalog.cdn_thumbnail_url(slug) or exercise.get("thumbnail_url", ""),
                order_index=exercise["order_index"],
                sets=sets,
            )
        )
    return RoutineOut(
        slug=routine.get("slug"),
        name=routine.get("name", ""),
        estimated_minutes=routine.get("minutes_per_routine") or request.minutes,
        exercises=exercises,
    )
