import asyncio
import logging
import random
import re

from app.clients import postgres
from app.core import llm, ratelimit
from app.core.config import get_settings
from app.core.exceptions import AiUnavailableError, RateLimitedError
from app.exercises import repository as exercise_catalog
from app.routines import prompts, repository, utils
from app.routines.exceptions import NoRoutineCandidateError, UnsafeConstraintError
from app.routines.schemas import (
    RoutineExerciseOut,
    RoutineGenerateRequest,
    RoutineOut,
    RoutineSetOut,
)
from app.users import repository as users_repository
from app.workouts import repository as workouts_repository

logger = logging.getLogger("fitset")


async def generate_routine(user_id: str, request: RoutineGenerateRequest) -> RoutineOut:
    """프로필 기록 조회 룰 필터 검색 LLM 선택 루틴 생성"""
    if not ratelimit.routine_limiter().allow(user_id):
        raise RateLimitedError()
    settings = get_settings()
    if not postgres.is_configured():
        raise AiUnavailableError("루틴 검색 저장소가 설정되지 않았습니다.")

    profile = await asyncio.to_thread(users_repository.get_profile, user_id)
    goal = profile.get("goal") or utils.DEFAULT_GOAL
    workouts, analysis = await asyncio.gather(
        asyncio.to_thread(workouts_repository.get_recent_workouts, user_id, settings.workout_days),
        _analyze_query(request, goal),
    )
    query_description, parsed_avoided, unsafe_constraints = analysis

    if unsafe_constraints and settings.guardrail_block_unsafe:
        logger.warning("guardrail blocked recommendation: %s", unsafe_constraints)
        raise UnsafeConstraintError()

    meta = exercise_catalog.get_exercise_meta()
    stats = utils.build_e1rm_stats(workouts, meta)
    ratio = utils.bodyweight_ratio(workouts, meta)
    home_only = ratio is not None and ratio >= settings.bodyweight_home_ratio

    avoided = utils.effective_avoided(profile.get("avoidBodyParts"), request.muscle_groups)
    parsed = utils.effective_avoided(sorted(parsed_avoided), request.muscle_groups)
    if parsed:
        logger.info("context-parsed avoid applied: %s", sorted(parsed))

    query_vector = await asyncio.to_thread(llm.try_embed_query, query_description)
    if query_vector is None:
        raise AiUnavailableError()

    def filters(avoid: set[str]) -> repository.SearchFilters:
        """기피 부위 집합으로 검색 필터 조립"""
        return repository.SearchFilters(
            muscle_groups=request.muscle_groups, avoided=avoid, level=request.level,
            minutes=request.minutes, tolerance=settings.minutes_tolerance, home_only=home_only,
        )

    top = await asyncio.to_thread(repository.search, filters(avoided | parsed), query_vector, settings.cosine_top_k)
    if not top and parsed:
        logger.info("parsed avoid %s emptied candidates, retrying without it", sorted(parsed))
        top = await asyncio.to_thread(repository.search, filters(avoided), query_vector, settings.cosine_top_k)
    if not top:
        raise NoRoutineCandidateError()

    sampled = random.sample(top, min(settings.llm_candidate_count, len(top)))
    chosen = await _pick_with_llm(sampled, query_description)
    routine = (chosen if chosen is not None else top[0])["body"]

    return _build_response(routine, request, profile, stats, meta)


async def _analyze_query(request: RoutineGenerateRequest, goal: str) -> tuple[str, set[str], list[str]]:
    """요청 LLM 분석 묘사문 기피 부위 안전 제약 추출"""
    system, user = prompts.describe_query(
        goal, request.level, request.muscle_groups, request.minutes, request.context,
    )
    fallback = prompts.fallback_description(
        goal, request.level, request.muscle_groups, request.minutes,
    )
    raw = await asyncio.to_thread(llm.try_complete, "describe", system, user, 300)
    if raw is None:
        return fallback, set(), []
    description, avoided, unsafe = utils.parse_query_analysis(raw)
    if description is None:
        logger.warning("describe LLM returned unparsable output (%r), falling back to template", raw[:200])
        return fallback, set(), []
    return description, avoided, unsafe


async def _pick_with_llm(sampled: list[dict], query_description: str) -> dict | None:
    """샘플 후보 중 LLM 최적 선택 실패 시 None"""
    system, user = prompts.pick_best(sampled, query_description)
    answer = await asyncio.to_thread(llm.try_complete, "pick", system, user, 10)
    if answer is None:
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
    """종목 수행 방식별 세트 무게 렙 시간 조립"""
    order_index = set_template["order_index"]
    reps = set_template["reps"]

    if kind == "DURATION":
        return RoutineSetOut(
            order_index=order_index,
            weight=0,
            reps=0,
            duration_seconds=utils.set_duration_seconds(
                slug, level, get_settings().duration_set_seconds
            ),
        )
    if kind == "REPS_ONLY":
        return RoutineSetOut(order_index=order_index, weight=0, reps=reps, duration_seconds=0)

    weight = utils.recommend_weight(slug, reps, stats, profile, meta) or 0
    return RoutineSetOut(order_index=order_index, weight=weight, reps=reps, duration_seconds=0)


def _build_response(
    routine: dict,
    request: RoutineGenerateRequest,
    profile: dict,
    stats: tuple[dict, dict],
    meta: dict,
) -> RoutineOut:
    """선택 루틴을 무게 추천 포함 응답 스키마로 변환"""
    settings = get_settings()
    exercises = []
    for exercise in routine.get("exercises", []):
        slug = exercise["slug"]
        equipment = (meta.get(slug) or {}).get("equipment", [])
        kind = exercise_catalog.exercise_type(slug)
        sets = [
            _build_set(slug, set_template, kind, request.level, stats, profile, meta)
            for set_template in exercise.get("sets", [])
        ]
        if request.include_warmup and sets:
            if sets[0].duration_seconds:
                sets[0].duration_seconds = max(
                    1, int(sets[0].duration_seconds * settings.warmup_duration_factor)
                )
            else:
                sets[0].weight = utils.warmup_weight(slug, sets[0].weight, equipment) or 0
        exercises.append(
            RoutineExerciseOut(
                exercise_id=exercise_catalog.exercise_id(slug),
                slug=slug,
                exercise_name=exercise["exercise_name"],
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
