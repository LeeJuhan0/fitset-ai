"""유저 프로필·체중 조회 — 백엔드 MySQL 직조회 (구 내부 API §4.1·§4.4 승계).

반환 형태는 구 spring 클라이언트와 동일하게 유지한다 — 소비자(routines·charts)의
로직이 데이터 출처 교체를 모르게 한다. 전 함수 동기 — 호출부가 asyncio.to_thread로 감싼다.
"""
from datetime import timedelta

from sqlalchemy import select

from app.clients import mysql
from app.core import clock
from app.core.errors import UserNotFoundError
from app.exercises.domain import Muscle
from app.users.domain import BodyWeightHistory, UserAvoidedMuscle, UserProfile


def get_profile(user_id: str) -> dict:
    """유저 프로필 (구 §4.1) — heightCm·weightKg(최신)·gender·birthDate·goal·level·avoidBodyParts."""
    uid = mysql.uuid_bytes(user_id)
    rows = mysql.fetch_all(
        select(
            UserProfile.height,
            UserProfile.gender,
            UserProfile.birth_date,
            UserProfile.workout_goal,
            UserProfile.level,
        ).where(UserProfile.user_id == uid)
    )
    if not rows:
        raise UserNotFoundError()
    profile = rows[0]

    avoided = mysql.fetch_all(
        select(Muscle.slug)
        .select_from(UserAvoidedMuscle)
        .join(Muscle, Muscle.id == UserAvoidedMuscle.muscle_id)
        .where(UserAvoidedMuscle.user_id == uid)
    )
    # 프로필 테이블에 체중이 없다 — 최신 몸무게는 body_weight_history 마지막 측정값 (구 §4.1 규약)
    latest_weight = mysql.fetch_all(
        select(BodyWeightHistory.weight)
        .where(BodyWeightHistory.user_id == uid)
        .order_by(BodyWeightHistory.measured_at.desc())
        .limit(1)
    )
    return {
        "heightCm": float(profile["height"]),
        "weightKg": float(latest_weight[0]["weight"]) if latest_weight else None,
        "gender": profile["gender"],
        "birthDate": profile["birth_date"].isoformat(),
        "goal": mysql.camel_enum(profile["workout_goal"]),
        "level": mysql.camel_enum(profile["level"]),
        "avoidBodyParts": [row["slug"] for row in avoided],
    }


def get_body_weights(user_id: str, days: int) -> list[dict]:
    """체중 측정 이력 (구 §4.4) — measuredAt 오름차순. 챗봇 체중·BMI 차트용."""
    cutoff = (clock.now_utc() - timedelta(days=days)).replace(tzinfo=None)
    rows = mysql.fetch_all(
        select(BodyWeightHistory.measured_at, BodyWeightHistory.weight)
        .where(BodyWeightHistory.user_id == mysql.uuid_bytes(user_id), BodyWeightHistory.measured_at >= cutoff)
        .order_by(BodyWeightHistory.measured_at)
    )
    return [
        {"measuredAt": mysql.iso_from_db(row["measured_at"]), "weightKg": float(row["weight"])}
        for row in rows
    ]
