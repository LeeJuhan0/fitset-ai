"""루틴 생성 서비스 — CLAUDE.md 7단계 플로우 구현.

② 내부 API(프로필·기록)와 ⑥ 쿼리 변환 LLM은 병렬 실행.
폴백 규칙: 변환 LLM 실패→템플릿 조립 / 선택 LLM 실패·범위 밖→코사인 1위 /
503 AI_UNAVAILABLE은 쿼리 임베딩까지 실패한 경우에만.
"""
import asyncio
import random
import re

import numpy as np

from app.clients.spring import get_spring_client
from app.core import llm
from app.core.config import get_settings
from app.core.errors import AiUnavailableError, NoRoutineCandidateError
from app.routines import domain, prompts
from app.routines.repository import get_exercise_meta, get_routine_store
from app.routines.schemas import (
    RoutineExerciseOut,
    RoutineGenerateRequest,
    RoutineOut,
    RoutineSetOut,
)


async def generate_routine(user_id: str, request: RoutineGenerateRequest) -> RoutineOut:
    settings = get_settings()
    store = get_routine_store()
    if not store.ready:
        raise AiUnavailableError("루틴 데이터를 로딩 중입니다. 잠시 후 다시 시도해주세요.")

    # ②∥⑥a — 유저 컨텍스트 조회와 쿼리 변환 LLM 병렬 실행
    profile, workouts, query_description = await asyncio.gather(
        get_spring_client().get_profile(user_id),
        get_spring_client().get_recent_workouts(user_id, settings.workout_days),
        _describe_query(request),
    )

    # ③ 기록 통계 — e1RM(무게 추천)·맨몸 비율(홈트 판정)
    meta = get_exercise_meta()
    stats = domain.build_e1rm_stats(workouts, meta)
    ratio = domain.bodyweight_ratio(workouts, meta)
    home_only = ratio is not None and ratio >= settings.bodyweight_home_ratio

    # ④·⑤ 조건 결합 + 룰 필터 통과 전체를 후보로
    avoided = domain.effective_avoided(profile.get("avoidBodyParts"), request.muscle_groups)
    candidates = [
        index
        for index, routine in enumerate(store.routines)
        if domain.passes_filters(
            routine,
            muscle_groups=request.muscle_groups,
            avoided=avoided,
            level=request.level,
            minutes=request.minutes,
            tolerance=settings.minutes_tolerance,
            home_only=home_only,
        )
    ]
    if not candidates:
        raise NoRoutineCandidateError()

    # ⑥ 쿼리 임베딩 → 코사인 전량 → 탑30 → 랜덤 5 → LLM 일등 선택
    try:
        query_vector = await asyncio.to_thread(llm.embed_query, query_description)
    except Exception as exc:
        raise AiUnavailableError() from exc
    query = np.asarray(query_vector, dtype=np.float32)
    query /= np.linalg.norm(query) or 1.0
    similarities = store.vectors[candidates] @ query
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


async def _describe_query(request: RoutineGenerateRequest) -> str:
    """쿼리 → 루틴 묘사문 (LLM). 실패 시 요청 필드 템플릿 조립으로 폴백."""
    system, user = prompts.describe_query(
        request.goal, request.level, request.muscle_groups, request.minutes, request.context,
    )
    try:
        return await asyncio.to_thread(llm.complete, system, user, 200)
    except Exception:
        return prompts.fallback_description(
            request.goal, request.level, request.muscle_groups, request.minutes,
        )


async def _pick_with_llm(sampled: list[int], store, query_description: str) -> int | None:
    """탑5 중 일등 선택 (LLM). 실패·범위 밖 응답이면 None → 호출부가 코사인 1위 폴백."""
    candidates = [store.routines[i] for i in sampled]
    system, user = prompts.pick_best(candidates, query_description)
    try:
        answer = await asyncio.to_thread(llm.complete, system, user, 10)
    except Exception:
        return None
    match = re.search(r"\d+", answer)
    if match is None:
        return None
    number = int(match.group())
    if not 1 <= number <= len(sampled):
        return None
    return sampled[number - 1]


def _build_response(
    routine: dict,
    request: RoutineGenerateRequest,
    profile: dict,
    stats: tuple[dict, dict],
    meta: dict,
) -> RoutineOut:
    exercises = []
    for exercise in routine.get("exercises", []):
        slug = exercise["slug"]
        equipment = (meta.get(slug) or {}).get("equipment", [])
        sets = []
        for set_template in exercise.get("sets", []):
            reps = set_template["reps"]
            weight = domain.recommend_weight(slug, reps, stats, profile, meta)
            sets.append(RoutineSetOut(order_index=set_template["order_index"], weight=weight, reps=reps))
        if request.include_warmup and sets:
            sets[0].weight = domain.warmup_weight(sets[0].weight, equipment)
        exercises.append(
            RoutineExerciseOut(
                slug=slug,
                exercise_name=exercise["exercise_name"],
                thumbnail_url=exercise.get("thumbnail_url", ""),
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
