import asyncio
import logging
from functools import lru_cache

from app.clients import postgres
from app.core.ratelimit import RateLimiter
from app.core.config import get_settings
from app.core.exceptions import AiUnavailableError, DomainError, RateLimitedError
from app.exercises import repository as exercise_catalog
from app.memory import service as memory_service
from app.memory.schemas import UserPreferences
from app.rag import pipeline, query
from app.rag.retrieve import SearchFilters
from app.routines import utils
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


@lru_cache
def routine_limiter() -> RateLimiter:
    """유저별 루틴 생성 레이트리미터 전역 싱글턴"""
    return RateLimiter(get_settings().routine_generations_per_minute)


async def generate_routine(user_id: str, request: RoutineGenerateRequest) -> RoutineOut:
    """프로필 기록 조회 룰 필터 검색 LLM 선택 루틴 생성"""
    if not routine_limiter().allow(user_id):
        raise RateLimitedError()
    settings = get_settings()
    if not postgres.is_configured():
        raise AiUnavailableError("루틴 검색 저장소가 설정되지 않았습니다.")

    profile, preferences = await asyncio.gather(
        asyncio.to_thread(users_repository.get_profile, user_id),
        _latest_preferences(user_id),
    )
    goal = profile.get("goal") or utils.DEFAULT_GOAL
    context = utils.preference_context(request.context, preferences)
    workouts, analysis = await asyncio.gather(
        asyncio.to_thread(workouts_repository.get_recent_workouts, user_id, settings.workout_days),
        query.analyze(goal, request.level, request.muscle_groups, request.minutes, context),
    )
    query_description, parsed_avoided, unsafe_constraints = analysis

    if unsafe_constraints and settings.guardrail_block_unsafe:
        logger.warning("guardrail blocked recommendation: %s", unsafe_constraints)
        raise UnsafeConstraintError()

    meta = exercise_catalog.get_exercise_catalog()
    stats = utils.build_e1rm_stats(workouts)
    ratio = utils.bodyweight_ratio(workouts, meta)
    home_only = (ratio is not None and ratio >= settings.bodyweight_home_ratio) or utils.prefers_home(preferences)

    avoided = utils.effective_avoided(profile.get("avoidBodyParts"), request.muscle_groups)
    parsed = utils.effective_avoided(sorted(parsed_avoided), request.muscle_groups)
    if parsed:
        logger.info("context-parsed avoid applied: %s", sorted(parsed))

    filters = SearchFilters(
        muscle_groups=request.muscle_groups, avoided=avoided, level=request.level,
        minutes=request.minutes, tolerance=settings.minutes_tolerance, home_only=home_only,
        excluded_exercises=utils.excluded_exercises(preferences),
    )
    chosen = await pipeline.find_routine(query_description, filters, parsed)
    if chosen is None:
        raise NoRoutineCandidateError()
    routine = chosen["body"]

    return _build_response(routine, request, stats, meta)


async def _latest_preferences(user_id: str) -> UserPreferences | None:
    """운동 편향 조회, 저장소 실패면 편향 없이"""
    try:
        return await memory_service.latest_preferences(user_id)
    except DomainError:
        logger.warning("preferences lookup failed, recommending without them", exc_info=True)
        return None


def _build_set(
    slug: str,
    set_template: dict,
    kind: str | None,
    level: str,
    stats: dict[str, float],
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

    weight = utils.recommend_weight(slug, reps, stats, meta) or 0
    return RoutineSetOut(order_index=order_index, weight=weight, reps=reps, duration_seconds=0)


def _build_response(
    routine: dict,
    request: RoutineGenerateRequest,
    stats: dict[str, float],
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
            _build_set(slug, set_template, kind, request.level, stats, meta)
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
